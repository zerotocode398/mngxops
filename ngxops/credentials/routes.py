"""提供凭证管理页面、受保护的解密和启停 API。"""

import json
import math
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional
from urllib.parse import urlencode

import paramiko
from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ngxops.accounts.models import User
from ngxops.api.contracts import api_error_responses
from ngxops.audit.service import (
    prepare_audit_session,
    request_client_ip,
    suppress_model_audit,
    write_audit_log,
)
from ngxops.credentials.crypto import CredentialDecryptionError, encrypt_secret
from ngxops.credentials.models import Credential
from ngxops.credentials.tasks import build_credential_enable_runner
from ngxops.credentials.workbooks import (
    MAX_WORKBOOK_SIZE,
    apply_credential_rows,
    build_credential_export_bytes,
    build_credential_template_bytes,
    credential_name_preview,
    parse_credential_workbook,
    validate_credential_rows,
)
from ngxops.database.session import get_session, session_scope
from ngxops.security.dependencies import require_permission, require_superuser
from ngxops.settings.service import read_setting
from ngxops.ui import render_page
from ngxops.nodes.models import Node, NodeGroup
from ngxops.tasks.executor import create_task
from ngxops.tasks.models import Task


router = APIRouter(prefix="/credentials", tags=["credentials"])
api_router = APIRouter(prefix="/api/credentials", tags=["credentials"])
PAGE_SIZES = (10, 25, 50, 100)
_NOTICE_KEY = "ngxops_credentials_notice"
_MAX_PASSWORD_LENGTH = 4096
_MAX_PRIVATE_KEY_LENGTH = 65536
_MAX_DESCRIPTION_LENGTH = 4000
_PRIVATE_KEY_FORMATS = "RSA/ECDSA/Ed25519"
_BEIJING_TIMEZONE = timezone(timedelta(hours=8))


class CredentialApiItem(BaseModel):
    """描述节点选择器可用的 SSH 凭证摘要。"""

    id: int
    name: str
    username: str
    auth_type: Literal["password", "key"]
    auth_type_display: str
    is_enabled: bool


class CredentialListResponse(BaseModel):
    """描述可用于节点表单的已启用凭证列表。"""

    success: bool = True
    data: List[CredentialApiItem]


class CredentialRelatedNodeApiItem(BaseModel):
    """描述凭证关联节点弹窗使用的非敏感字段。"""

    id: int
    hostname: str
    ip: str
    status: Literal["online", "offline", "unknown"]
    status_display: str
    nginx_available: Optional[bool]
    nginx_version: str
    probe_time: str


class CredentialRelatedNodeListResponse(BaseModel):
    """描述凭证关联节点列表及分页信息。"""

    success: bool = True
    credential_id: int
    credential_name: str
    items: List[CredentialRelatedNodeApiItem]
    page: int
    page_size: int
    total: int
    pages: int


class CredentialSecretResponse(BaseModel):
    """描述经授权解密返回的单个凭证字段。"""

    success: bool = True
    value: str


class CredentialToggleResponse(BaseModel):
    """描述凭证启停后的状态和用户提示。"""

    success: bool = True
    message: str
    id: int
    is_enabled: bool
    task_id: Optional[int] = None


class CredentialEnableProgressResponse(BaseModel):
    """描述最近一次凭证关联节点测试任务进度。"""

    success: bool = True
    has_task: bool
    task_id: Optional[int] = None
    status: Optional[Literal["pending", "running", "success", "failed", "cancelled"]] = None
    progress: int = 0
    detail: str = ""
    result_tree: Optional[dict] = None
    created_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class CredentialImportError(BaseModel):
    """描述凭证工作簿的行号和不回显敏感内容的错误提示。"""

    row: int
    message: str


class CredentialImportResponse(BaseModel):
    """描述凭证批量导入结果和整文件校验错误。"""

    success: bool
    message: str
    created: int = 0
    updated: int = 0
    total: int = 0
    errors: List[CredentialImportError] = Field(default_factory=list)


def _has_permission(
    request: Request,
    session: Session,
    user: User,
    resource: str,
    action: str,
) -> bool:
    """使用应用配置的 RBAC 解析器检查页面按钮权限。"""
    if user.is_superuser:
        return True
    checker = getattr(request.app.state, "permission_checker", None)
    return bool(checker and checker(session, user, resource, action))


def _page_values(request: Request) -> tuple:
    """规范凭证列表的页码和每页行数。"""
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(request.query_params.get("per_page", "10"))
    except (TypeError, ValueError):
        per_page = 10
    if per_page not in PAGE_SIZES:
        per_page = 10
    return page, per_page


def _filter_conditions(request: Request) -> tuple:
    """将凭证搜索词、认证方式和启用状态转成 ORM 条件。"""
    search = request.query_params.get("search", "").strip()[:200]
    auth_type = request.query_params.get("auth_type", "").strip()
    status = request.query_params.get("status", "").strip()
    conditions = []
    terms = [
        term.strip()
        for term in search.replace("，", ",").split(",")
        if term.strip()
    ]
    for term in terms:
        conditions.append(
            or_(
                Credential.name.ilike("%{}%".format(term)),
                Credential.username.ilike("%{}%".format(term)),
            )
        )
    if auth_type in ("password", "key"):
        conditions.append(Credential.auth_type == auth_type)
    if status == "enabled":
        conditions.append(Credential.is_enabled.is_(True))
    elif status == "disabled":
        conditions.append(Credential.is_enabled.is_(False))
    return conditions, search, auth_type, status


def _export_ids(request: Request) -> List[int]:
    """解析导出选择 ID 并保留用户提交的顺序。"""
    raw_values = [request.query_params.get("ids", "")]
    raw_values.extend(request.query_params.getlist("id"))
    result = []
    for part in ",".join(raw_values).replace("，", ",").split(","):
        try:
            credential_id = int(part.strip())
        except (TypeError, ValueError):
            continue
        if credential_id > 0 and credential_id not in result:
            result.append(credential_id)
    return result


def _auth_type_display(auth_type: str) -> str:
    """返回认证方式的中文展示名称。"""
    return "密码认证" if auth_type == "password" else "密钥认证"


def _last_test_display(result: str) -> str:
    """返回最近一次 SSH 测试结果的中文展示名称。"""
    return {
        "success": "全部成功",
        "partial": "部分失败",
        "failed": "全部失败",
        "unknown": "未测试",
    }.get(result, "未测试")


def _format_beijing_time(value: Optional[datetime]) -> str:
    """将以 UTC 保存的时间格式化为北京时间。"""
    if value is None:
        return "-"
    utc_value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return utc_value.astimezone(_BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M")


def _render_form(
    request: Request,
    user: User,
    session: Session,
    *,
    credential: Optional[Credential] = None,
    values: Optional[dict] = None,
    errors: Optional[dict] = None,
    status_code: int = 200,
) -> Response:
    """按创建或编辑模式渲染不回显密钥的凭证表单。"""
    is_edit = credential is not None
    initial_values = values or {}
    return render_page(
        request,
        "credentials/form.html",
        context={
            "credential": credential,
            "is_edit": is_edit,
            "values": initial_values,
            "errors": errors or {},
            "auth_type": initial_values.get(
                "auth_type", credential.auth_type if credential else "password"
            ),
            "has_password": bool(credential and credential.password),
            "has_private_key": bool(credential and credential.private_key),
        },
        user=user,
        db_session=session,
        status_code=status_code,
    )


def _validate_form_values(
    session: Session,
    owner_id: int,
    name: str,
    username: str,
    auth_type: str,
    password: str,
    private_key: str,
    description: str,
    credential: Optional[Credential] = None,
) -> dict:
    """检查凭证字段长度、名称唯一性和认证材料。"""
    values = {
        "name": name.strip(),
        "username": username.strip(),
        "auth_type": auth_type.strip(),
        "description": description.strip(),
    }
    errors = {}
    if not values["name"]:
        errors["name"] = "请输入凭证名称"
    elif len(values["name"]) > 100:
        errors["name"] = "凭证名称不能超过 100 个字符"
    else:
        duplicate_query = select(Credential.id).where(
            Credential.name == values["name"], Credential.created_by == owner_id
        )
        if credential is not None:
            duplicate_query = duplicate_query.where(Credential.id != credential.id)
        if session.scalar(duplicate_query) is not None:
            errors["name"] = "凭证名称已存在"

    if not values["username"]:
        errors["username"] = "请输入 SSH 用户名"
    elif len(values["username"]) > 100:
        errors["username"] = "SSH 用户名不能超过 100 个字符"
    if values["auth_type"] not in ("password", "key"):
        errors["auth_type"] = "请选择有效的认证方式"
    if len(values["description"]) > _MAX_DESCRIPTION_LENGTH:
        errors["description"] = "描述不能超过 4000 个字符"
    if len(password) > _MAX_PASSWORD_LENGTH:
        errors["password"] = "密码不能超过 4096 个字符"
    if len(private_key) > _MAX_PRIVATE_KEY_LENGTH:
        errors["private_key"] = "私钥内容不能超过 65536 个字符"

    has_password = bool(password or (credential and credential.password))
    has_private_key = bool(private_key or (credential and credential.private_key))
    if values["auth_type"] == "password" and not has_password:
        errors["password"] = "密码认证方式必须填写密码"
    if values["auth_type"] == "key":
        if not has_private_key:
            errors["private_key"] = "密钥认证方式必须填写私钥"
        elif private_key and not _is_valid_private_key(private_key):
            errors["private_key"] = (
                "私钥格式无效，请提供合法的 {} 格式私钥".format(
                    _PRIVATE_KEY_FORMATS
                )
            )
    return {"values": values, "errors": errors}


def _is_valid_private_key(private_key: str) -> bool:
    """校验未加密的 RSA、ECDSA 或 Ed25519 私钥。"""
    from io import StringIO

    for key_type in (paramiko.RSAKey, paramiko.ECDSAKey, paramiko.Ed25519Key):
        try:
            key_type.from_private_key(StringIO(private_key))
            return True
        except Exception:
            continue
    return False


def _form_context_values(
    name: str,
    username: str,
    auth_type: str,
    description: str,
) -> dict:
    """构造不含凭证明文的表单回显字段。"""
    return {
        "name": name[:100],
        "username": username[:100],
        "auth_type": auth_type,
        "description": description[:_MAX_DESCRIPTION_LENGTH],
    }


def _redirect_with_notice(request: Request, message: str) -> RedirectResponse:
    """保存一次性页面提示并回到凭证列表。"""
    request.session[_NOTICE_KEY] = message
    query = {"notice": message}
    redirect_path = "/credentials/?{}".format(urlencode(query))
    return RedirectResponse(redirect_path, status_code=303)


@router.get("/", include_in_schema=False)
def credential_list_page(
    request: Request,
    user: User = Depends(require_permission("credentials", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """显示带搜索、认证类型和启用状态筛选的凭证列表。"""
    conditions, search, auth_type, status = _filter_conditions(request)
    query = select(Credential)
    count_query = select(func.count()).select_from(Credential)
    for condition in conditions:
        query = query.where(condition)
        count_query = count_query.where(condition)
    total = int(session.scalar(count_query) or 0)
    page, per_page = _page_values(request)
    pages = max(1, int(math.ceil(total / float(per_page))))
    page = min(page, pages)
    credentials = session.scalars(
        query.order_by(Credential.updated_at.desc(), Credential.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    node_counts = dict(
        session.execute(
            select(Node.credential_id, func.count(Node.id))
            .where(Node.is_deleted.is_(False), Node.credential_id.is_not(None))
            .group_by(Node.credential_id)
        ).all()
    )
    notice = request.session.pop(_NOTICE_KEY, "")
    if not notice:
        notice = request.query_params.get("notice", "")
    export_query = urlencode(
        {
            key: request.query_params.get(key, "")
            for key in ("search", "auth_type", "status")
            if request.query_params.get(key, "")
        }
    )
    return render_page(
        request,
        "credentials/list.html",
        context={
            "credentials": credentials,
            "node_counts": node_counts,
            "updated_at_display": {
                credential.id: _format_beijing_time(credential.updated_at)
                for credential in credentials
            },
            "search": search,
            "auth_type": auth_type,
            "status": status,
            "notice": notice,
            "export_query": export_query,
            "permissions": {
                action: _has_permission(request, session, user, "credentials", action)
                for action in ("create", "update", "delete", "enable")
            },
            "pagination": {
                "page": page,
                "pages": pages,
                "total": total,
                "per_page": per_page,
                "per_page_options": PAGE_SIZES,
            },
        },
        user=user,
        db_session=session,
    )


@router.get("/create/", include_in_schema=False)
def credential_create_page(
    request: Request,
    user: User = Depends(require_permission("credentials", "create")),
    session: Session = Depends(get_session),
) -> Response:
    """显示新增 SSH 凭证表单。"""
    return _render_form(request, user, session)


@router.post("/create/", include_in_schema=False)
def credential_create_submit(
    request: Request,
    name: str = Form(""),
    username: str = Form(""),
    auth_type: str = Form("password"),
    password: str = Form(""),
    private_key: str = Form(""),
    description: str = Form(""),
    user: User = Depends(require_permission("credentials", "create")),
    session: Session = Depends(get_session),
) -> Response:
    """校验并加密保存新凭证，验证失败时不回显密文材料。"""
    form_values = _form_context_values(name, username, auth_type, description)
    credential = None
    errors = {}
    try:
        with session_scope(request.app.state.database.session_factory) as write_session:
            prepare_audit_session(
                write_session, user.id, user.username, request_client_ip(request)
            )
            with write_session.begin():
                checked = _validate_form_values(
                    write_session,
                    user.id,
                    name,
                    username,
                    auth_type,
                    password,
                    private_key,
                    description,
                )
                errors = checked["errors"]
                form_values = checked["values"]
                if not errors:
                    key = request.app.state.credential_encryption_key
                    credential = Credential(
                        name=form_values["name"],
                        username=form_values["username"],
                        auth_type=form_values["auth_type"],
                        password=encrypt_secret(key, password),
                        private_key=encrypt_secret(key, private_key),
                        description=form_values["description"],
                        created_by=user.id,
                    )
                    write_session.add(credential)
                    write_session.flush()
    except IntegrityError:
        errors["name"] = "凭证名称已存在"
    if errors:
        return _render_form(
            request, user, session, values=form_values, errors=errors, status_code=200
        )
    return _redirect_with_notice(request, "凭证 {} 创建成功".format(form_values["name"]))


@router.get("/{credential_id}/edit/", include_in_schema=False)
def credential_edit_page(
    credential_id: int,
    request: Request,
    user: User = Depends(require_permission("credentials", "update")),
    session: Session = Depends(get_session),
) -> Response:
    """显示凭证资料并以空白字段保护已存密钥。"""
    credential = session.get(Credential, credential_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="凭证不存在")
    values = {
        "name": credential.name,
        "username": credential.username,
        "auth_type": credential.auth_type,
        "description": credential.description,
    }
    return _render_form(request, user, session, credential=credential, values=values)


@router.post("/{credential_id}/edit/", include_in_schema=False)
def credential_edit_submit(
    credential_id: int,
    request: Request,
    name: str = Form(""),
    username: str = Form(""),
    auth_type: str = Form("password"),
    password: str = Form(""),
    private_key: str = Form(""),
    description: str = Form(""),
    user: User = Depends(require_permission("credentials", "update")),
    session: Session = Depends(get_session),
) -> Response:
    """更新凭证资料并在敏感字段留空时保留原密文。"""
    form_values = _form_context_values(name, username, auth_type, description)
    errors = {}
    current = None
    try:
        with session_scope(request.app.state.database.session_factory) as write_session:
            prepare_audit_session(
                write_session, user.id, user.username, request_client_ip(request)
            )
            with write_session.begin():
                current = write_session.get(Credential, credential_id)
                if current is None:
                    raise HTTPException(status_code=404, detail="凭证不存在")
                checked = _validate_form_values(
                    write_session,
                    user.id,
                    name,
                    username,
                    auth_type,
                    password,
                    private_key,
                    description,
                    credential=current,
                )
                errors = checked["errors"]
                form_values = checked["values"]
                if not errors:
                    key = request.app.state.credential_encryption_key
                    current.name = form_values["name"]
                    current.username = form_values["username"]
                    current.auth_type = form_values["auth_type"]
                    current.description = form_values["description"]
                    if password:
                        current.password = encrypt_secret(key, password)
                    if private_key:
                        current.private_key = encrypt_secret(key, private_key)
                    current.updated_at = datetime.utcnow()
                    write_session.flush()
    except IntegrityError:
        errors["name"] = "凭证名称已存在"
    if errors:
        return _render_form(
            request,
            user,
            session,
            credential=current,
            values=form_values,
            errors=errors,
            status_code=200,
        )
    return _redirect_with_notice(request, "凭证 {} 更新成功".format(form_values["name"]))


@router.get("/{credential_id}/delete/", include_in_schema=False)
def credential_delete_page(
    credential_id: int,
    request: Request,
    user: User = Depends(require_permission("credentials", "delete")),
    session: Session = Depends(get_session),
) -> Response:
    """显示删除确认页并列出凭证基本信息。"""
    credential = session.get(Credential, credential_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="凭证不存在")
    return render_page(
        request,
        "credentials/delete.html",
        context={"credential": credential},
        user=user,
        db_session=session,
    )


@router.post("/{credential_id}/delete/", include_in_schema=False)
def credential_delete_submit(
    credential_id: int,
    request: Request,
    user: User = Depends(require_permission("credentials", "delete")),
) -> Response:
    """在独立事务中删除凭证并返回凭证列表。"""
    deleted_name = ""
    with session_scope(request.app.state.database.session_factory) as write_session:
        prepare_audit_session(
            write_session, user.id, user.username, request_client_ip(request)
        )
        with write_session.begin():
            credential = write_session.get(Credential, credential_id)
            if credential is None:
                raise HTTPException(status_code=404, detail="凭证不存在")
            deleted_name = credential.name
            write_session.delete(credential)
    return _redirect_with_notice(request, "凭证 {} 已删除".format(deleted_name))


@router.post("/bulk-delete/", include_in_schema=False)
def credential_bulk_delete_submit(
    request: Request,
    credential_ids: List[int] = Form(default=[]),
    user: User = Depends(require_permission("credentials", "delete")),
) -> Response:
    """在单个事务中删除所选凭证并记录汇总审计。"""
    selected_ids = list(dict.fromkeys(value for value in credential_ids if value > 0))
    if not selected_ids:
        raise HTTPException(status_code=400, detail="请至少选择一条凭证")

    with session_scope(request.app.state.database.session_factory) as write_session:
        prepare_audit_session(
            write_session, user.id, user.username, request_client_ip(request)
        )
        with write_session.begin():
            credentials = write_session.scalars(
                select(Credential)
                .where(Credential.id.in_(selected_ids))
                .order_by(Credential.id.asc())
            ).all()
            if len(credentials) != len(selected_ids):
                raise HTTPException(status_code=404, detail="部分凭证不存在")
            names = [credential.name for credential in credentials]
            with suppress_model_audit(write_session):
                for credential in credentials:
                    write_session.delete(credential)
                write_session.flush()
            detail = "批量删除 {} 条凭证".format(len(credentials))
            name_preview = credential_name_preview(names)
            if name_preview:
                detail += "：" + name_preview
            write_audit_log(write_session, "凭证管理", "批量删除凭证", detail)

    return _redirect_with_notice(
        request, "已删除 {} 条凭证".format(len(selected_ids))
    )


@api_router.get(
    "/import-template",
    summary="下载凭证导入模板",
    description="需要 credentials.create 权限。",
    responses=api_error_responses((401, 403, 500)),
)
def download_credential_template(
    user: User = Depends(require_permission("credentials", "create")),
) -> Response:
    """返回凭证导入模板工作簿。"""
    return Response(
        build_credential_template_bytes(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="credential_import_template.xlsx"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@api_router.post(
    "/import",
    response_model=CredentialImportResponse,
    summary="批量导入 SSH 凭证",
    description=(
        "需要 credentials.create 权限。先校验整个 xlsx，拒绝文件内及当前用户已有凭证重名，再在单个事务中新建凭证；"
        "错误响应不回显密码或私钥。"
    ),
    responses=api_error_responses((400, 401, 403, 413, 422, 500)),
)
def import_credentials(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_permission("credentials", "create")),
    session: Session = Depends(get_session),
) -> CredentialImportResponse:
    """整文件校验凭证工作簿并加密保存认证材料。"""
    if not (file.filename or "").lower().endswith(".xlsx"):
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "仅支持 .xlsx 格式",
                "errors": [{"row": 0, "message": "仅支持 .xlsx 格式"}],
            },
        )
    content = file.file.read(MAX_WORKBOOK_SIZE + 1)
    if len(content) > MAX_WORKBOOK_SIZE:
        return JSONResponse(
            status_code=413,
            content={
                "success": False,
                "message": "文件不能超过 8 MiB",
                "errors": [{"row": 0, "message": "文件不能超过 8 MiB"}],
            },
        )
    rows, parse_errors = parse_credential_workbook(content)
    if parse_errors:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "message": "Excel 解析失败",
                "errors": parse_errors,
            },
        )
    existing_names = session.scalars(
        select(Credential.name).where(Credential.created_by == user.id)
    ).all()
    cleaned, errors = validate_credential_rows(rows, existing_names)
    if errors:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "message": "校验未通过，共 {} 条错误，未导入任何凭证".format(
                    len(errors)
                ),
                "errors": errors,
            },
        )
    prepare_audit_session(
        session,
        actor_id=user.id,
        username=user.username,
        ip=request_client_ip(request),
    )
    try:
        with suppress_model_audit(session):
            with session.begin_nested():
                result = apply_credential_rows(
                    session,
                    cleaned,
                    user.id,
                    request.app.state.credential_encryption_key,
                )
                parts = []
                if result["created"]:
                    parts.append(
                        "新建 {} 条：{}".format(
                            result["created"],
                            credential_name_preview(result["created_names"]),
                        )
                    )
                write_audit_log(
                    session,
                    "凭证管理",
                    "导入凭证",
                    "批量导入成功：" + "；".join(parts),
                )
            session.commit()
    except IntegrityError as exc:
        session.rollback()
        existing_names = set(
            session.scalars(
                select(Credential.name).where(Credential.created_by == user.id)
            ).all()
        )
        duplicate_names = [
            row["name"] for row in cleaned if row["name"] in existing_names
        ]
        if duplicate_names:
            detail = "凭证名称「{}」已存在于当前账号，不允许重复导入".format(
                credential_name_preview(duplicate_names)
            )
        else:
            detail = "导入期间发生凭证名称冲突，请检查工作簿内的重复名称后重试"
        raise HTTPException(status_code=409, detail=detail) from exc
    parts = []
    if result["created"]:
        parts.append("新建 {} 条".format(result["created"]))
    return CredentialImportResponse(
        success=True,
        message="批量导入成功：" + "，".join(parts),
        created=result["created"],
        updated=result["updated"],
        total=result["total"],
    )


@api_router.get(
    "/export",
    summary="导出 SSH 凭证",
    description=(
        "仅超级管理员可导出含明文密码/私钥的工作簿。指定 ids 时仅导出所选凭证，"
        "否则按当前搜索和筛选条件导出。"
    ),
    responses=api_error_responses((401, 403, 404, 422, 500)),
)
def export_credentials(
    request: Request,
    ids: str = Query("", max_length=4000),
    search: str = Query("", max_length=200),
    auth_type: Optional[Literal["password", "key"]] = Query(None),
    status: Optional[Literal["enabled", "disabled"]] = Query(None),
    user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> Response:
    """按所选 ID 或当前筛选范围生成凭证明文工作簿并写入审计。"""
    selected_ids = _export_ids(request)
    query = select(Credential)
    if selected_ids:
        credentials = session.scalars(
            query.where(Credential.id.in_(selected_ids))
        ).all()
        order = {credential_id: index for index, credential_id in enumerate(selected_ids)}
        credentials.sort(key=lambda item: order.get(item.id, len(order)))
        scope_label = "勾选"
    else:
        conditions, _search, _auth_type, _status = _filter_conditions(request)
        for condition in conditions:
            query = query.where(condition)
        credentials = session.scalars(
            query.order_by(Credential.updated_at.desc(), Credential.id.desc())
        ).all()
        scope_label = "筛选全量"
    try:
        content = build_credential_export_bytes(
            credentials,
            request.app.state.credential_encryption_key,
        )
    except CredentialDecryptionError as exc:
        raise HTTPException(status_code=500, detail="凭证解密失败，未生成导出文件") from exc
    detail = "导出 {} 条（{}）".format(len(credentials), scope_label)
    preview = credential_name_preview([item.name for item in credentials])
    if preview:
        detail += "：" + preview
    write_audit_log(session, "凭证管理", "导出凭证", detail)
    session.commit()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="credentials_export_{}.xlsx"'.format(stamp),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@api_router.get(
    "",
    response_model=CredentialListResponse,
    summary="查询已启用 SSH 凭证",
    description="供节点表单选择凭证；不返回密码或私钥。",
    responses=api_error_responses((401, 403, 422, 500)),
)
def list_enabled_credentials(
    user: User = Depends(require_permission("credentials", "read")),
    session: Session = Depends(get_session),
) -> CredentialListResponse:
    """返回已启用凭证的非敏感字段。"""
    credentials = session.scalars(
        select(Credential)
        .where(Credential.is_enabled.is_(True))
        .order_by(Credential.name.asc(), Credential.id.asc())
    ).all()
    return CredentialListResponse(
        data=[
            CredentialApiItem(
                id=item.id,
                name=item.name,
                username=item.username,
                auth_type=item.auth_type,
                auth_type_display=_auth_type_display(item.auth_type),
                is_enabled=item.is_enabled,
            )
            for item in credentials
        ]
    )


@api_router.get(
    "/{credential_id}/nodes",
    response_model=CredentialRelatedNodeListResponse,
    summary="查询凭证关联节点",
    description="需要 credentials.read 权限；返回分页节点摘要，不包含凭证明文。",
    responses=api_error_responses((401, 403, 404, 422, 500)),
)
def list_credential_related_nodes(
    credential_id: int = Path(..., ge=1),
    search: str = Query(""),
    status: Literal["", "online", "offline", "unknown"] = Query(""),
    nginx_status: Literal["", "available", "unavailable", "unknown"] = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    user: User = Depends(require_permission("credentials", "read")),
    session: Session = Depends(get_session),
) -> CredentialRelatedNodeListResponse:
    """按关键词和状态筛选分页返回凭证关联节点。"""
    credential = session.get(Credential, credential_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="凭证不存在")
    conditions = [Node.credential_id == credential_id, Node.is_deleted.is_(False)]
    terms = [
        term.strip()[:200]
        for term in search.strip()[:200].replace("，", ",").split(",")
        if term.strip()
    ]
    for term in terms:
        pattern = "%{}%".format(term)
        conditions.append(
            or_(
                Node.hostname.ilike(pattern),
                Node.ip.ilike(pattern),
                Node.groups.any(NodeGroup.name.ilike(pattern)),
            )
        )
    if status:
        conditions.append(Node.status == status)
    if nginx_status == "available":
        conditions.append(Node.nginx_available.is_(True))
    elif nginx_status == "unavailable":
        conditions.append(Node.nginx_available.is_(False))
    elif nginx_status == "unknown":
        conditions.append(Node.nginx_available.is_(None))
    total = int(
        session.scalar(select(func.count()).select_from(Node).where(*conditions)) or 0
    )
    pages = max(1, int(math.ceil(total / float(page_size))))
    page = min(page, pages)
    nodes = session.scalars(
        select(Node)
        .where(*conditions)
        .order_by(Node.hostname.asc(), Node.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    status_labels = {"online": "在线", "offline": "离线", "unknown": "未知"}
    return CredentialRelatedNodeListResponse(
        credential_id=credential.id,
        credential_name=credential.name,
        items=[
            CredentialRelatedNodeApiItem(
                id=node.id,
                hostname=node.hostname,
                ip=node.ip,
                status=node.status,
                status_display=status_labels.get(node.status, "未知"),
                nginx_available=node.nginx_available,
                nginx_version=node.nginx_version or "",
                probe_time=_format_beijing_time(node.last_probe_at),
            )
            for node in nodes
        ],
        page=page,
        page_size=page_size,
        total=total,
        pages=pages,
    )


@api_router.get(
    "/{credential_id}/secret",
    response_model=CredentialSecretResponse,
    summary="解密读取凭证字段",
    description="需要 credentials.read 权限；响应禁止缓存，且不记录凭证明文。",
    responses=api_error_responses((401, 403, 404, 422, 500)),
)
def get_credential_secret(
    request: Request,
    credential_id: int = Path(..., ge=1),
    field: Literal["password", "private_key"] = Query("password"),
    user: User = Depends(require_permission("credentials", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """返回当前用户获准读取的凭证密码或私钥明文。"""
    credential = session.get(Credential, credential_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="凭证不存在")
    try:
        key = request.app.state.credential_encryption_key
        value = (
            credential.get_password(key)
            if field == "password"
            else credential.get_private_key(key)
        )
    except CredentialDecryptionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return Response(
        content=CredentialSecretResponse(value=value).model_dump_json(),
        media_type="application/json",
        headers={"Cache-Control": "no-store, private"},
    )


@api_router.post(
    "/{credential_id}/toggle-enable",
    response_model=CredentialToggleResponse,
    summary="启用或禁用 SSH 凭证",
    description=(
        "需要 credentials.enable 权限。启用时对未锁定的活动关联节点异步测试 SSH，"
        "并独立更新 Nginx 可用状态；禁用时关联活动节点标记为离线。"
    ),
    responses=api_error_responses((401, 403, 404, 422, 500, 503)),
)
def toggle_credential_enabled(
    request: Request,
    credential_id: int = Path(..., ge=1),
    user: User = Depends(require_permission("credentials", "enable")),
) -> CredentialToggleResponse:
    """切换凭证状态并创建或取消对应节点测试动作。"""
    executor = getattr(request.app.state, "task_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="任务执行器尚未启动")
    factory = request.app.state.database.session_factory
    task_id = None
    related_nodes = []
    affected = 0
    with session_scope(request.app.state.database.session_factory) as write_session:
        prepare_audit_session(
            write_session, user.id, user.username, request_client_ip(request)
        )
        with write_session.begin():
            batch_limit = read_setting(write_session, "node.batch_max_count", 3)
            credential_state = write_session.execute(
                select(Credential.name, Credential.is_enabled).where(
                    Credential.id == credential_id
                )
            ).one_or_none()
            if credential_state is None:
                raise HTTPException(status_code=404, detail="凭证不存在")
            name = credential_state.name
            is_enabled = not credential_state.is_enabled
            write_session.execute(
                update(Credential)
                .where(Credential.id == credential_id)
                .values(is_enabled=is_enabled, updated_at=datetime.utcnow())
                .execution_options(synchronize_session=False)
            )
            audit_action = "启用凭证" if is_enabled else "锁定凭证"
            write_audit_log(
                write_session,
                "凭证管理",
                audit_action,
                "{}「{}」".format(audit_action, name),
            )
            if is_enabled:
                related_nodes = write_session.scalars(
                    select(Node)
                    .where(Node.credential_id == credential_id, Node.is_deleted.is_(False))
                    .order_by(Node.id.asc())
                ).all()
                for node in related_nodes:
                    if not node.is_locked:
                        node.status = "unknown"
                        node.updated_at = datetime.utcnow()
            else:
                result = write_session.execute(
                    update(Node)
                    .where(Node.credential_id == credential_id, Node.is_deleted.is_(False))
                    .values(status="offline", updated_at=datetime.utcnow())
                )
                affected = result.rowcount or 0

    if is_enabled and related_nodes:
        testable_nodes = [node for node in related_nodes if not node.is_locked]
        task_id = create_task(
            factory,
            executor,
            build_credential_enable_runner(
                factory,
                credential_id,
                request.app.state.credential_encryption_key,
                batch_limit,
            ),
            operation_type="credential_enable_test",
            detail="credential_enable_test credential_id={}".format(credential_id),
            target_hostnames=[node.hostname for node in testable_nodes],
            target_ips=[node.ip for node in testable_nodes],
            target_configs=[name],
            subject_type="credential",
            subject_id=credential_id,
            trigger_user_id=user.id,
            trigger_ip=request_client_ip(request),
        )
    message = (
        "凭证 {} 已启用；当前无关联节点，未启动连接测试。".format(name)
        if is_enabled and not related_nodes
        else "凭证 {} 已启用，已创建节点测试任务 #{}。".format(name, task_id)
        if is_enabled and task_id
        else "凭证 {} 已启用；关联节点均已锁定，测试任务将记录跳过结果。".format(name)
        if is_enabled
        else "凭证 {} 已禁用，{} 个关联节点状态已更新为离线。".format(name, affected)
    )
    return CredentialToggleResponse(
        message=message,
        id=credential_id,
        is_enabled=is_enabled,
        task_id=task_id,
    )


@api_router.get(
    "/{credential_id}/enable-progress",
    response_model=CredentialEnableProgressResponse,
    summary="查询凭证关联节点测试进度",
    description="需要 credentials.read 权限；返回最近一次启用测试任务状态和脱敏结果。",
    responses=api_error_responses((401, 403, 404, 422, 500)),
)
def get_credential_enable_progress(
    credential_id: int = Path(..., ge=1),
    user: User = Depends(require_permission("credentials", "read")),
    session: Session = Depends(get_session),
) -> CredentialEnableProgressResponse:
    """返回凭证最近一次关联节点测试任务的持久化状态。"""
    credential = session.get(Credential, credential_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="凭证不存在")
    task = session.scalar(
        select(Task)
        .where(
            Task.operation_type == "credential_enable_test",
            Task.subject_type == "credential",
            Task.subject_id == credential_id,
        )
        .order_by(Task.created_at.desc(), Task.id.desc())
        .limit(1)
    )
    if task is None:
        return CredentialEnableProgressResponse(has_task=False)
    result_tree = json.loads(task.result_tree_json) if task.result_tree_json else None
    return CredentialEnableProgressResponse(
        has_task=True,
        task_id=task.id,
        status=task.status,
        progress=task.progress,
        detail=task.detail,
        result_tree=result_tree,
        created_at=task.created_at,
        finished_at=task.finished_at,
    )
