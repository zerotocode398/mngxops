"""提供节点、节点组页面及不含敏感信息的选择器 API。"""

import math
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from ngxops.accounts.models import User
from ngxops.api.contracts import api_error_responses
from ngxops.audit.service import (
    request_client_ip,
    suppress_model_audit,
    write_audit_log,
)
from ngxops.credentials.models import Credential
from ngxops.database.session import get_session, session_scope
from ngxops.nodes.models import Node, NodeGroup, NodeSyncSetting
from ngxops.nodes.services import (
    apply_node_import,
    build_node_export_bytes,
    build_node_template_bytes,
    create_or_restore_node,
    node_query,
    parse_node_workbook,
    split_terms,
    validate_node_import_rows,
)
from ngxops.nodes.tasks import (
    _run_batch_probe,
    _run_nginx_probe,
    _run_single_probe,
    _run_system_info,
)
from ngxops.security.dependencies import (
    PERM_DENIED_MESSAGE,
    PERM_DENIED_TITLE,
    require_authenticated_user,
    require_permission,
)
from ngxops.settings.service import read_setting
from ngxops.tasks.executor import create_task
from ngxops.ui import render_page


router = APIRouter(prefix="/nodes", tags=["nodes"])
api_router = APIRouter(prefix="/api/nodes", tags=["nodes"])
PAGE_SIZES = (10, 25, 50, 100)
MAX_BATCH_COUNT = 3
MAX_GROUPS_PER_NODE = 3
MAX_NAME_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 4000
_BEIJING_TZ = timezone(timedelta(hours=8), name="CST")


def _beijing_datetime(value: Optional[datetime]) -> Optional[datetime]:
    """将数据库中的 UTC 时间转换为带时区的北京时间。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_BEIJING_TZ)


def _beijing_datetime_display(value: Optional[datetime]) -> str:
    """格式化节点列表中的探测时间为北京时间。"""
    local_value = _beijing_datetime(value)
    return local_value.strftime("%Y-%m-%d %H:%M") if local_value else "未探测"


class NodeApiItem(BaseModel):
    """描述节点选择器中可见的非敏感资产信息。"""

    id: int
    hostname: str
    ip: str
    port: int
    status: Literal["online", "offline", "unknown"]
    environment: Literal["dev", "test", "prod"]
    is_locked: bool
    credential_id: Optional[int]
    credential_name: str
    groups: List[str]


class NodeListResponse(BaseModel):
    """描述节点列表 API 响应。"""

    success: bool = True
    data: List[NodeApiItem]
    total: int


class NodeGroupApiItem(BaseModel):
    """描述节点组及其活跃成员数量。"""

    id: int
    name: str
    description: str
    node_count: int


class NodeGroupListResponse(BaseModel):
    """描述节点组选择器响应。"""

    success: bool = True
    data: List[NodeGroupApiItem]


class NodeDetailItem(BaseModel):
    """描述节点详情中的资产、SSH 与 Nginx 状态。"""

    id: int
    hostname: str
    ip: str
    port: int
    environment: Literal["dev", "test", "prod"]
    status: Literal["online", "offline", "unknown"]
    is_locked: bool
    description: str
    nginx_path: str
    nginx_available: Optional[bool]
    nginx_version: str
    last_probe_at: Optional[datetime]
    last_nginx_probe_at: Optional[datetime]
    credential_name: str
    credential_username: str
    credential_auth_type: str
    has_credential: bool
    groups: List[str]
    created_by: str
    created_at: datetime
    updated_at: datetime


class NodeDetailResponse(BaseModel):
    """描述节点详情 API 响应。"""

    success: bool = True
    node: NodeDetailItem


class NodeBatchProbeRequest(BaseModel):
    """描述批量 SSH 探测的节点范围。"""

    node_ids: List[int] = Field(min_length=1, max_length=100)


class NodeTaskCreatedResponse(BaseModel):
    """描述已加入后台执行队列的节点任务。"""

    success: bool = True
    message: str
    task_id: int


class BatchNodeRequest(BaseModel):
    """描述锁定或批量删除节点的请求体。"""

    node_ids: List[int] = Field(min_length=1, max_length=100)


class NodeLockRequest(BatchNodeRequest):
    """描述节点批量锁定或解锁请求体。"""

    action: Literal["lock", "unlock"] = "lock"


class NodeOperationResponse(BaseModel):
    """描述节点批量操作执行结果。"""

    success: bool = True
    message: str
    count: int
    task_id: Optional[int] = None


class NodeImportError(BaseModel):
    """描述工作簿中的行级或合并校验问题。"""

    row: int = Field(description="Excel 行号；多行相同问题合并时为 0")
    row_range: Optional[str] = Field(
        default=None,
        description="合并错误影响的 Excel 行号或连续行范围",
    )
    message: str
    merged: bool = Field(
        default=False,
        description="是否将相同校验问题合并",
    )


class NodeImportResponse(BaseModel):
    """描述节点批量导入结果或按原因合并的校验错误。"""

    success: bool
    message: str
    created: int = 0
    restored: int = 0
    total: int = 0
    errors: List[NodeImportError] = Field(default_factory=list)


def _has_permission(
    request: Request,
    session: Session,
    user: User,
    action: str,
    resource: str = "nodes",
) -> bool:
    """检查当前用户是否拥有指定节点操作权限。"""
    if user.is_superuser:
        return True
    checker = getattr(request.app.state, "permission_checker", None)
    return bool(checker and checker(session, user, resource, action))


def _page_values(request: Request) -> tuple:
    """规范节点列表页码和每页条数。"""
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(request.query_params.get("per_page", "10"))
    except (TypeError, ValueError):
        per_page = 10
    return page, per_page if per_page in PAGE_SIZES else 10


def _filtered_nodes(
    session: Session,
    request: Request,
    *,
    include_deleted: bool = False,
):
    """按列表页搜索、节点组、环境和 SSH 状态筛选节点。"""
    try:
        group_id = int(request.query_params.get("group", ""))
    except (TypeError, ValueError):
        group_id = None
    return node_query(
        session,
        search=request.query_params.get("search", ""),
        group_id=group_id,
        environment=request.query_params.get("environment", ""),
        status=request.query_params.get("status", ""),
        include_deleted=include_deleted,
    )


def _node_form_context(
    request: Request,
    session: Session,
    user: User,
    *,
    node: Optional[Node],
    values: Optional[dict] = None,
    errors: Optional[dict] = None,
    status_code: int = 200,
):
    """组装节点新增或编辑表单所需的分组及凭证选项。"""
    credentials = []
    if _has_permission(request, session, user, "read", "credentials"):
        credentials = session.scalars(
            select(Credential)
            .where(Credential.is_enabled.is_(True))
            .order_by(Credential.auth_type.asc(), Credential.name.asc(), Credential.id.asc())
        ).all()
    if node is not None and node.credential is not None and all(
        item.id != node.credential.id for item in credentials
    ):
        credentials.append(node.credential)
    groups = session.scalars(select(NodeGroup).order_by(NodeGroup.name.asc())).all()
    current_values = values or {}
    return render_page(
        request,
        "nodes/form.html",
        {
            "node": node,
            "is_edit": node is not None,
            "values": current_values,
            "errors": errors or {},
            "groups": groups,
            "credentials": credentials,
            "default_port": read_setting(session, "node.ssh_default_port", 22),
            "default_nginx_path": read_setting(session, "config.default_nginx_bin", "/usr/sbin/nginx"),
            "default_main_conf_path": read_setting(session, "config.default_nginx_path", "/etc/nginx/nginx.conf"),
            "selected_groups": set(
                int(value)
                for value in current_values.get(
                    "group_ids", [item.id for item in node.groups] if node else []
                )
                if str(value).isdigit()
            ),
            "environment_choices": (("dev", "开发环境"), ("test", "测试环境"), ("prod", "生产环境")),
            "return_to": _return_to(
                request,
                current_values.get("return_to", request.query_params.get("return_to", "")),
            ),
        },
        user,
        session,
        status_code=status_code,
    )


def _return_to(request: Request, value: str = "") -> str:
    """仅允许返回节点列表页内的本地链接。"""
    candidate = value or ("/nodes/?" + request.url.query if request.url.query else "/nodes/")
    if candidate.startswith("/nodes/") and not candidate.startswith("//"):
        return candidate
    return "/nodes/"


def _node_import_audit_detail(summary: str, nodes: List[dict]) -> str:
    """生成包含节点主机名和 IP 且不超过审计详情长度的摘要。"""
    prefix = summary + "；资产："
    included = []
    for index, item in enumerate(nodes):
        label = "{} ({})".format(
            " ".join(str(item.get("hostname", "")).splitlines())[:100],
            " ".join(str(item.get("ip", "")).splitlines())[:45],
        )
        remaining = len(nodes) - index - 1
        suffix = "……（另有 {} 台未列出）".format(remaining) if remaining else ""
        candidate = prefix + "、".join(included + [label]) + suffix
        if len(candidate) > 4000:
            omitted = len(nodes) - len(included)
            suffix = "……（另有 {} 台未列出）".format(omitted)
            while included and len(prefix + "、".join(included) + suffix) > 4000:
                included.pop()
                omitted += 1
                suffix = "……（另有 {} 台未列出）".format(omitted)
            return (prefix + "、".join(included) + suffix)[:4000]
        included.append(label)
    return (prefix + "、".join(included))[:4000]


def _validate_node_values(
    session: Session,
    values: dict,
    node_id: Optional[int] = None,
    can_read_credentials: bool = False,
) -> dict:
    """校验节点表单字段、关联凭证和节点组数量。"""
    errors = {}
    hostname = values.get("hostname", "").strip()
    if not hostname or len(hostname) > MAX_NAME_LENGTH:
        errors["hostname"] = "主机名必填且不能超过 100 个字符"
    ip = values.get("ip", "").strip()
    try:
        import ipaddress

        values["ip"] = str(ipaddress.ip_address(ip))
    except ValueError:
        errors["ip"] = "请输入有效的 IPv4 或 IPv6 地址"
    try:
        port = int(values.get("port", "22"))
        if port < 1 or port > 65535:
            raise ValueError()
        values["port"] = port
    except (TypeError, ValueError):
        errors["port"] = "SSH 端口必须为 1 到 65535 的整数"
    if values.get("environment") not in ("dev", "test", "prod"):
        errors["environment"] = "请选择有效的所属环境"
    if len(values.get("nginx_path", "")) > 255:
        errors["nginx_path"] = "Nginx 路径不能超过 255 个字符"
    if len(values.get("main_conf_path", "")) > 500:
        errors["main_conf_path"] = "Nginx 主配置路径不能超过 500 个字符"
    if len(values.get("description", "")) > MAX_DESCRIPTION_LENGTH:
        errors["description"] = "备注不能超过 4000 个字符"

    group_ids = values.get("group_ids", [])
    try:
        group_ids = list(dict.fromkeys(int(item) for item in group_ids if str(item).strip()))
    except (TypeError, ValueError):
        group_ids = []
        errors["group_ids"] = "节点组选择无效"
    if len(group_ids) > MAX_GROUPS_PER_NODE:
        errors["group_ids"] = "节点最多只能关联 3 个节点组"
    existing_groups = set(
        session.scalars(select(NodeGroup.id).where(NodeGroup.id.in_(group_ids))).all()
    ) if group_ids else set()
    if existing_groups != set(group_ids):
        errors["group_ids"] = "选择的节点组不存在"
    values["group_ids"] = group_ids

    raw_credential_id = values.get("credential_id", "")
    if raw_credential_id:
        try:
            credential_id = int(raw_credential_id)
        except (TypeError, ValueError):
            credential_id = 0
        credential = session.get(Credential, credential_id)
        node = session.get(Node, node_id) if node_id is not None else None
        keeping_existing = node is not None and node.credential_id == credential_id
        if credential is None or (not credential.is_enabled and not keeping_existing):
            errors["credential_id"] = "请选择已启用的 SSH 凭证"
        elif not can_read_credentials and not keeping_existing:
            errors["credential_id"] = "当前账号没有查看 SSH 凭证的权限"
        else:
            values["credential_id"] = credential.id
    else:
        values["credential_id"] = None

    if "ip" in values and values["ip"]:
        existing = session.scalar(select(Node).where(Node.ip == values["ip"]))
        if existing is not None and existing.id != node_id and not existing.is_deleted:
            errors["ip"] = "该 IP 地址已被其他活跃节点使用"
        elif (
            existing is not None
            and node_id is not None
            and existing.id != node_id
            and existing.is_deleted
        ):
            errors["ip"] = "该 IP 对应已删除节点，请新建同 IP 节点以恢复历史记录"
    return errors


@router.get("/", include_in_schema=False)
def list_nodes(
    request: Request,
    user: User = Depends(require_permission("nodes", "read")),
    session: Session = Depends(get_session),
):
    """显示可筛选、分页的活跃节点清单。"""
    query = _filtered_nodes(session, request)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    page, per_page = _page_values(request)
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)
    nodes = session.scalars(query.offset((page - 1) * per_page).limit(per_page)).unique().all()
    groups = session.scalars(select(NodeGroup).order_by(NodeGroup.name.asc())).all()
    return render_page(
        request,
        "nodes/list.html",
        {
            "nodes": nodes,
            "probe_time_display": {
                node.id: _beijing_datetime_display(node.last_probe_at)
                for node in nodes
            },
            "groups": groups,
            "search": request.query_params.get("search", ""),
            "group_filter": request.query_params.get("group", ""),
            "environment_filter": request.query_params.get("environment", ""),
            "status_filter": request.query_params.get("status", ""),
            "updated_node": request.query_params.get("updated", ""),
            "batch_max_count": read_setting(session, "node.batch_max_count", MAX_BATCH_COUNT),
            "pagination": {
                "page": page,
                "pages": pages,
                "total": total,
                "per_page": per_page,
                "per_page_options": PAGE_SIZES,
            },
            "permissions": {
                action: _has_permission(request, session, user, action)
                for action in ("create", "update", "delete", "lock", "unlock", "ssh_test")
            },
            "export_query": str(request.query_params),
            "list_return_to": "/nodes/?{}".format(request.url.query) if request.url.query else "/nodes/",
        },
        user,
        session,
    )


@router.get("/create/", include_in_schema=False)
def create_node_page(
    request: Request,
    user: User = Depends(require_permission("nodes", "create")),
    session: Session = Depends(get_session),
):
    """显示新建节点表单。"""
    return _node_form_context(request, session, user, node=None)


@router.post("/create/", include_in_schema=False)
def create_node(
    request: Request,
    hostname: str = Form(""),
    ip: str = Form(""),
    port: str = Form(""),
    environment: str = Form("dev"),
    nginx_path: str = Form(""),
    main_conf_path: str = Form(""),
    credential_id: str = Form(""),
    description: str = Form(""),
    group_ids: List[str] = Form([]),
    return_to: str = Form("/nodes/"),
    user: User = Depends(require_permission("nodes", "create")),
    session: Session = Depends(get_session),
):
    """校验并创建节点或恢复同 IP 的历史节点。"""
    port = port or str(read_setting(session, "node.ssh_default_port", 22))
    nginx_path = nginx_path or read_setting(
        session, "config.default_nginx_bin", "/usr/sbin/nginx"
    )
    main_conf_path = main_conf_path or read_setting(
        session, "config.default_nginx_path", "/etc/nginx/nginx.conf"
    )
    values = {
        "hostname": hostname,
        "ip": ip,
        "port": port,
        "environment": environment,
        "nginx_path": nginx_path,
        "main_conf_path": main_conf_path,
        "credential_id": credential_id,
        "description": description,
        "group_ids": group_ids,
        "return_to": return_to,
    }
    can_read_credentials = _has_permission(request, session, user, "read", "credentials")
    errors = _validate_node_values(session, values, can_read_credentials=can_read_credentials)
    if errors:
        return _node_form_context(request, session, user, node=None, values=values, errors=errors, status_code=422)
    try:
        node, restored = create_or_restore_node(
            session,
            user.id,
            hostname=values["hostname"].strip(),
            ip=values["ip"],
            port=values["port"],
            credential_id=values["credential_id"],
            group_ids=values["group_ids"],
            environment=values["environment"],
            nginx_path=values["nginx_path"].strip(),
            main_conf_path=values["main_conf_path"].strip(),
            description=values["description"].strip(),
        )
        session.flush()
        hostname_saved = node.hostname
        session.commit()
    except (IntegrityError, ValueError) as exc:
        session.rollback()
        values["ip"] = ip
        errors = {"ip": "IP 已被另一个请求创建"} if isinstance(exc, IntegrityError) else {"ip": str(exc)}
        return _node_form_context(request, session, user, node=None, values=values, errors=errors, status_code=409)
    flag = "restored" if restored else "created"
    destination = _return_to(request, return_to)
    separator = "&" if "?" in destination else "?"
    return RedirectResponse("{}{}{}={}".format(destination, separator, flag, quote(hostname_saved)), status_code=303)


@router.get("/{node_id}/edit/", include_in_schema=False)
def edit_node_page(
    request: Request,
    node_id: int = Path(..., ge=1),
    user: User = Depends(require_permission("nodes", "update")),
    session: Session = Depends(get_session),
):
    """显示活跃节点的编辑表单。"""
    node = session.scalar(
        select(Node).where(Node.id == node_id, Node.is_deleted.is_(False)).options(
            joinedload(Node.credential),
            joinedload(Node.sync_setting),
            selectinload(Node.groups),
        )
    )
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    return _node_form_context(request, session, user, node=node)


@router.post("/{node_id}/edit/", include_in_schema=False)
def update_node(
    request: Request,
    node_id: int = Path(..., ge=1),
    hostname: str = Form(""),
    ip: str = Form(""),
    port: str = Form("22"),
    environment: str = Form("dev"),
    nginx_path: str = Form("/usr/sbin/nginx"),
    main_conf_path: str = Form("/etc/nginx/nginx.conf"),
    credential_id: str = Form(""),
    description: str = Form(""),
    group_ids: List[str] = Form([]),
    return_to: str = Form("/nodes/"),
    user: User = Depends(require_permission("nodes", "update")),
    session: Session = Depends(get_session),
):
    """校验并更新活跃节点资产字段。"""
    node = session.scalar(
        select(Node).where(Node.id == node_id, Node.is_deleted.is_(False)).options(
            joinedload(Node.sync_setting), selectinload(Node.groups)
        )
    )
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    values = {
        "hostname": hostname,
        "ip": ip,
        "port": port,
        "environment": environment,
        "nginx_path": nginx_path,
        "main_conf_path": main_conf_path,
        "credential_id": credential_id,
        "description": description,
        "group_ids": group_ids,
        "return_to": return_to,
    }
    can_read_credentials = _has_permission(request, session, user, "read", "credentials")
    errors = _validate_node_values(session, values, node.id, can_read_credentials)
    if errors:
        return _node_form_context(request, session, user, node=node, values=values, errors=errors, status_code=422)
    try:
        node.hostname = values["hostname"].strip()
        node.ip = values["ip"]
        node.port = values["port"]
        node.environment = values["environment"]
        node.nginx_path = values["nginx_path"].strip() or "/usr/sbin/nginx"
        node.credential_id = values["credential_id"]
        node.description = values["description"].strip()
        node.groups = list(
            session.scalars(select(NodeGroup).where(NodeGroup.id.in_(values["group_ids"]))).all()
        ) if values["group_ids"] else []
        if node.sync_setting is None:
            node.sync_setting = NodeSyncSetting(main_conf_path=values["main_conf_path"].strip())
        else:
            node.sync_setting.main_conf_path = values["main_conf_path"].strip() or "/etc/nginx/nginx.conf"
        node.updated_at = datetime.utcnow()
        session.flush()
        session.commit()
    except IntegrityError:
        session.rollback()
        errors = {"ip": "该 IP 地址已被其他节点使用"}
        return _node_form_context(request, session, user, node=node, values=values, errors=errors, status_code=409)
    destination = _return_to(request, return_to)
    separator = "&" if "?" in destination else "?"
    return RedirectResponse("{}{}updated={}".format(destination, separator, quote(hostname)), status_code=303)


@router.get("/{node_id}/delete/", include_in_schema=False)
def delete_node_page(
    request: Request,
    node_id: int = Path(..., ge=1),
    user: User = Depends(require_permission("nodes", "delete")),
    session: Session = Depends(get_session),
):
    """显示节点逻辑删除确认页。"""
    node = session.scalar(
        select(Node).where(Node.id == node_id, Node.is_deleted.is_(False)).options(
            joinedload(Node.credential), selectinload(Node.groups)
        )
    )
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    return render_page(request, "nodes/delete.html", {"node": node, "return_to": _return_to(request)}, user, session)


@router.post("/{node_id}/delete/", include_in_schema=False)
def delete_node(
    request: Request,
    node_id: int = Path(..., ge=1),
    return_to: str = Form("/nodes/"),
    user: User = Depends(require_permission("nodes", "delete")),
    session: Session = Depends(get_session),
):
    """逻辑删除节点并保留 IP、凭证及组关联历史。"""
    node = session.scalar(select(Node).where(Node.id == node_id, Node.is_deleted.is_(False)))
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    hostname = node.hostname
    node.soft_delete(user.id)
    session.commit()
    destination = _return_to(request, return_to)
    separator = "&" if "?" in destination else "?"
    return RedirectResponse("{}{}deleted={}".format(destination, separator, quote(hostname)), status_code=303)


@router.get("/groups/", include_in_schema=False)
def list_node_groups(
    request: Request,
    user: User = Depends(require_permission("nodes", "read")),
    session: Session = Depends(get_session),
):
    """显示节点组、成员数量和筛选分页。"""
    search = request.query_params.get("search", "").strip()
    query = select(NodeGroup).options(
        selectinload(NodeGroup.nodes),
        joinedload(NodeGroup.creator),
    ).order_by(NodeGroup.created_at.desc(), NodeGroup.id.desc())
    for term in split_terms(search):
        query = query.where(NodeGroup.name.ilike("%{}%".format(term)))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    page, per_page = _page_values(request)
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)
    groups = session.scalars(query.offset((page - 1) * per_page).limit(per_page)).unique().all()
    group_counts = {
        group.id: sum(not node.is_deleted for node in group.nodes)
        for group in groups
    }
    return render_page(
        request,
        "nodes/groups.html",
        {
            "groups": groups,
            "group_counts": group_counts,
            "search": search,
            "pagination": {"page": page, "pages": pages, "total": total, "per_page": per_page, "per_page_options": PAGE_SIZES},
            "can_create": _has_permission(request, session, user, "create"),
            "can_update": _has_permission(request, session, user, "update"),
            "can_delete": _has_permission(request, session, user, "delete"),
        },
        user,
        session,
    )


def _group_form_response(request: Request, session: Session, user: User, group: Optional[NodeGroup], values: dict, errors: dict, status_code: int = 200):
    """渲染节点组新增或编辑表单及可选节点。"""
    nodes = session.scalars(
        select(Node)
        .where(Node.is_deleted.is_(False))
        .options(selectinload(Node.groups))
        .order_by(Node.hostname.asc())
    ).unique().all()
    selected_ids = set(int(item) for item in values.get("node_ids", [node.id for node in group.nodes] if group else []) if str(item).isdigit())
    return render_page(
        request,
        "nodes/group_form.html",
        {"group": group, "values": values, "errors": errors, "nodes": nodes, "selected_ids": selected_ids},
        user,
        session,
        status_code,
    )


def _save_group(request: Request, session: Session, user: User, group: Optional[NodeGroup], name: str, description: str, node_ids: List[str]):
    """校验并保存节点组以及不超过三组的节点成员关系。"""
    values = {"name": name, "description": description, "node_ids": node_ids}
    errors = {}
    normalized = name.strip()
    if not normalized or len(normalized) > MAX_NAME_LENGTH:
        errors["name"] = "节点组名称必填且不能超过 100 个字符"
    if len(description) > MAX_DESCRIPTION_LENGTH:
        errors["description"] = "描述不能超过 4000 个字符"
    existing = session.scalar(select(NodeGroup).where(NodeGroup.name == normalized)) if normalized else None
    if existing is not None and (group is None or existing.id != group.id):
        errors["name"] = "节点组名称已存在"
    try:
        desired_ids = set(int(item) for item in node_ids if str(item).strip())
    except (TypeError, ValueError):
        desired_ids = set()
        errors["node_ids"] = "节点选择无效"
    nodes = list(
        session.scalars(select(Node).where(Node.id.in_(desired_ids), Node.is_deleted.is_(False)).options(selectinload(Node.groups))).unique().all()
    ) if desired_ids else []
    if len(nodes) != len(desired_ids):
        errors["node_ids"] = "选择的节点不存在或已删除"
    for item in nodes:
        if group is not None and group in item.groups:
            continue
        if len(item.groups) >= MAX_GROUPS_PER_NODE:
            errors["node_ids"] = "节点「{}」已关联 3 个节点组".format(item.hostname)
            break
    if errors:
        return _group_form_response(request, session, user, group, values, errors, 422)
    is_new = group is None
    try:
        if group is None:
            group = NodeGroup(name=normalized, description=description.strip(), created_by=user.id)
            session.add(group)
            session.flush()
        else:
            group.name = normalized
            group.description = description.strip()
            group.updated_at = datetime.utcnow()
        selected = set(nodes)
        for item in list(group.nodes):
            if item not in selected:
                item.groups.remove(group)
        for item in nodes:
            if group not in item.groups:
                item.groups.append(group)
        session.commit()
    except IntegrityError:
        session.rollback()
        return _group_form_response(request, session, user, group, values, {"name": "节点组名称已存在"}, 409)
    flag = "created_group" if is_new else "updated_group"
    return RedirectResponse("/nodes/groups/?{}={}".format(flag, quote(group.name)), status_code=303)


@router.get("/groups/create/", include_in_schema=False)
def create_node_group_page(
    request: Request,
    user: User = Depends(require_permission("nodes", "create")),
    session: Session = Depends(get_session),
):
    """显示新建节点组表单。"""
    return _group_form_response(request, session, user, None, {}, {})


@router.post("/groups/create/", include_in_schema=False)
def create_node_group(
    request: Request,
    name: str = Form(""),
    description: str = Form(""),
    node_ids: List[str] = Form([]),
    user: User = Depends(require_permission("nodes", "create")),
    session: Session = Depends(get_session),
):
    """创建节点组并设置初始成员。"""
    return _save_group(request, session, user, None, name, description, node_ids)


@router.get("/groups/{group_id}/edit/", include_in_schema=False)
def edit_node_group_page(
    request: Request,
    group_id: int = Path(..., ge=1),
    user: User = Depends(require_permission("nodes", "update")),
    session: Session = Depends(get_session),
):
    """显示节点组及其当前成员。"""
    group = session.scalar(select(NodeGroup).where(NodeGroup.id == group_id).options(selectinload(NodeGroup.nodes)))
    if group is None:
        raise HTTPException(status_code=404, detail="节点组不存在")
    return _group_form_response(request, session, user, group, {}, {})


@router.post("/groups/{group_id}/edit/", include_in_schema=False)
def update_node_group(
    request: Request,
    group_id: int = Path(..., ge=1),
    name: str = Form(""),
    description: str = Form(""),
    node_ids: List[str] = Form([]),
    user: User = Depends(require_permission("nodes", "update")),
    session: Session = Depends(get_session),
):
    """更新节点组名称、描述和成员。"""
    group = session.scalar(select(NodeGroup).where(NodeGroup.id == group_id).options(selectinload(NodeGroup.nodes)))
    if group is None:
        raise HTTPException(status_code=404, detail="节点组不存在")
    return _save_group(request, session, user, group, name, description, node_ids)


@router.get("/groups/{group_id}/delete/", include_in_schema=False)
def delete_node_group_page(
    request: Request,
    group_id: int = Path(..., ge=1),
    user: User = Depends(require_permission("nodes", "delete")),
    session: Session = Depends(get_session),
):
    """显示节点组删除确认页。"""
    group = session.get(NodeGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="节点组不存在")
    active_count = sum(not node.is_deleted for node in group.nodes)
    return render_page(
        request,
        "nodes/group_delete.html",
        {"group": group, "active_count": active_count},
        user,
        session,
    )


@router.post("/groups/{group_id}/delete/", include_in_schema=False)
def delete_node_group(
    request: Request,
    group_id: int = Path(..., ge=1),
    user: User = Depends(require_permission("nodes", "delete")),
    session: Session = Depends(get_session),
):
    """删除节点组并解除成员关系。"""
    group = session.get(NodeGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="节点组不存在")
    name = group.name
    session.delete(group)
    session.commit()
    return RedirectResponse("/nodes/groups/?deleted_group={}".format(quote(name)), status_code=303)


@api_router.get(
    "",
    response_model=NodeListResponse,
    summary="查询节点资产",
    description="按节点管理权限返回活跃节点的非敏感资产字段。",
    responses=api_error_responses((401, 403, 422, 500)),
)
def api_list_nodes(
    request: Request,
    search: str = Query("", max_length=200),
    group_id: Optional[int] = Query(None, ge=1),
    environment: str = Query(""),
    status: str = Query(""),
    user: User = Depends(require_permission("nodes", "read")),
    session: Session = Depends(get_session),
) -> NodeListResponse:
    """返回当前筛选条件匹配的活跃节点。"""
    query = node_query(session, search=search, group_id=group_id, environment=environment, status=status)
    nodes = session.scalars(query.limit(500)).unique().all()
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    return NodeListResponse(
        data=[
            NodeApiItem(
                id=node.id,
                hostname=node.hostname,
                ip=node.ip,
                port=node.port,
                status=node.status,
                environment=node.environment,
                is_locked=node.is_locked,
                credential_id=node.credential_id,
                credential_name=node.credential.name if node.credential else "",
                groups=[group.name for group in node.groups],
            )
            for node in nodes
        ],
        total=total,
    )


@api_router.get(
    "/groups",
    response_model=NodeGroupListResponse,
    summary="查询节点组",
    responses=api_error_responses((401, 403, 422, 500)),
)
def api_list_node_groups(
    user: User = Depends(require_permission("nodes", "read")),
    session: Session = Depends(get_session),
) -> NodeGroupListResponse:
    """返回节点组名称、说明和活跃节点数量。"""
    groups = session.scalars(
        select(NodeGroup).options(selectinload(NodeGroup.nodes)).order_by(NodeGroup.name.asc())
    ).unique().all()
    return NodeGroupListResponse(
        data=[
            NodeGroupApiItem(
                id=group.id,
                name=group.name,
                description=group.description,
                node_count=sum(not node.is_deleted for node in group.nodes),
            )
            for group in groups
        ]
    )


@api_router.post(
    "/batch-delete",
    response_model=NodeOperationResponse,
    summary="批量逻辑删除节点",
    responses=api_error_responses((400, 401, 403, 404, 409, 422, 500)),
)
def api_batch_delete_nodes(
    payload: BatchNodeRequest,
    user: User = Depends(require_permission("nodes", "delete")),
    session: Session = Depends(get_session),
) -> NodeOperationResponse:
    """按系统批量上限逻辑删除活跃节点并保留其历史关联。"""
    batch_limit = read_setting(session, "node.batch_max_count", MAX_BATCH_COUNT)
    if len(set(payload.node_ids)) > batch_limit:
        raise HTTPException(status_code=400, detail="单次最多删除 {} 台节点".format(batch_limit))
    nodes = list(
        session.scalars(
            select(Node).where(Node.id.in_(set(payload.node_ids)), Node.is_deleted.is_(False))
        ).all()
    )
    if len(nodes) != len(set(payload.node_ids)):
        raise HTTPException(status_code=404, detail="部分节点不存在或已删除")
    for node in nodes:
        node.soft_delete(user.id)
    session.commit()
    return NodeOperationResponse(message="已从运维清单移除 {} 个节点（历史记录已保留）".format(len(nodes)), count=len(nodes))


@api_router.post(
    "/lock",
    response_model=NodeOperationResponse,
    summary="批量锁定或解锁节点",
    description="锁定会将 SSH 状态设为离线；解锁后自动创建 SSH/Nginx 探测任务并返回 task_id。",
    responses=api_error_responses((400, 401, 403, 404, 422, 500, 503)),
)
def api_lock_nodes(
    request: Request,
    payload: NodeLockRequest,
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> NodeOperationResponse:
    """按 nodes.lock 或 nodes.unlock 权限更新节点门禁状态。"""
    action = "lock" if payload.action == "lock" else "unlock"
    checker = getattr(request.app.state, "permission_checker", None)
    if not user.is_superuser and not (checker and checker(session, user, "nodes", action)):
        from ngxops.security.errors import PermissionDenied

        raise PermissionDenied(PERM_DENIED_TITLE, PERM_DENIED_MESSAGE)
    batch_limit = read_setting(session, "node.batch_max_count", MAX_BATCH_COUNT)
    if len(set(payload.node_ids)) > batch_limit:
        action_label = "锁定" if action == "lock" else "解锁"
        raise HTTPException(
            status_code=400,
            detail="单次最多{} {} 台节点".format(action_label, batch_limit),
        )
    nodes = list(
        session.scalars(
            select(Node).where(Node.id.in_(set(payload.node_ids)), Node.is_deleted.is_(False))
        ).all()
    )
    if len(nodes) != len(set(payload.node_ids)):
        raise HTTPException(status_code=404, detail="部分节点不存在或已删除")
    if action == "unlock" and getattr(request.app.state, "task_executor", None) is None:
        raise HTTPException(status_code=503, detail="后台任务执行器尚未启动")

    with suppress_model_audit(session):
        for node in nodes:
            node.is_locked = action == "lock"
            node.status = "offline" if action == "lock" else "unknown"
            node.updated_at = datetime.utcnow()
        if action == "lock":
            node_names = "、".join(node.hostname for node in nodes[:10])
            remainder = len(nodes) - min(len(nodes), 10)
            if remainder:
                node_names += "等 {} 台".format(len(nodes))
            write_audit_log(
                session,
                "节点管理",
                "批量锁定节点",
                "锁定 {} 台节点：{}".format(len(nodes), node_names),
            )
        session.commit()

    if action == "lock":
        return NodeOperationResponse(
            message="已锁定 {} 个节点，SSH 状态已更新为离线".format(len(nodes)),
            count=len(nodes),
        )

    task_id = _enqueue_node_task(
        request,
        session,
        user,
        operation_type="node_batch_test",
        runner=_run_batch_probe(
            request.app.state.database.session_factory,
            [node.id for node in nodes],
            request.app.state.credential_encryption_key,
            update_credential_error_state=True,
        ),
        detail="节点解锁后 SSH/Nginx 探测（{} 台）".format(len(nodes)),
        nodes=nodes,
        subject_type="node",
    )
    return NodeOperationResponse(
        message="已解锁 {} 个节点，SSH/Nginx 探测任务已启动".format(len(nodes)),
        count=len(nodes),
        task_id=task_id,
    )


@api_router.get(
    "/import-template",
    summary="下载节点导入模板",
    description="需要 nodes.create 权限；模板不包含任何凭证明文。",
    responses=api_error_responses((401, 403, 500)),
)
def download_node_template(user: User = Depends(require_permission("nodes", "create"))) -> Response:
    """返回节点 Excel 批量导入模板。"""
    return Response(
        build_node_template_bytes(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="node_import_template.xlsx"'},
    )


@api_router.post(
    "/import",
    response_model=NodeImportResponse,
    summary="批量导入节点",
    description="整份工作簿先校验再写入；任一行失败时不创建或恢复节点。相同校验原因合并并附带受影响行号。",
    responses=api_error_responses((400, 401, 403, 409, 413, 422, 500)),
)
def import_nodes(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_permission("nodes", "create")),
    session: Session = Depends(get_session),
):
    """解析、全量校验并事务性导入节点工作簿。"""
    if not (file.filename or "").lower().endswith(".xlsx"):
        return JSONResponse(status_code=400, content={"success": False, "message": "仅支持 .xlsx 格式", "errors": [{"row": 0, "message": "仅支持 .xlsx 格式"}]})
    content = file.file.read(8 * 1024 * 1024 + 1)
    if len(content) > 8 * 1024 * 1024:
        return JSONResponse(status_code=413, content={"success": False, "message": "文件不能超过 8 MiB", "errors": [{"row": 0, "message": "文件不能超过 8 MiB"}]})
    rows, parse_errors = parse_node_workbook(content)
    if parse_errors:
        return JSONResponse(status_code=422, content={"success": False, "message": "Excel 解析失败", "errors": parse_errors})
    cleaned, errors = validate_node_import_rows(
        session,
        rows,
        user.id,
        default_port=read_setting(session, "node.ssh_default_port", 22),
        default_nginx_path=read_setting(
            session, "config.default_nginx_bin", "/usr/sbin/nginx"
        ),
        default_main_conf_path=read_setting(
            session, "config.default_nginx_path", "/etc/nginx/nginx.conf"
        ),
    )
    if errors:
        return JSONResponse(status_code=422, content={"success": False, "message": "校验未通过，未导入任何节点", "errors": errors})
    try:
        with suppress_model_audit(session):
            result = apply_node_import(session, cleaned, user.id)
            parts = []
            if result["created"]:
                parts.append("新建 {} 台".format(result["created"]))
            if result["restored"]:
                parts.append("恢复 {} 台".format(result["restored"]))
            write_audit_log(
                session,
                "节点管理",
                "导入节点",
                _node_import_audit_detail("批量导入成功：" + "，".join(parts), cleaned),
            )
            session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="节点 IP 在导入期间发生冲突") from exc
    parts = []
    if result["created"]:
        parts.append("新建 {} 台".format(result["created"]))
    if result["restored"]:
        parts.append("恢复 {} 台".format(result["restored"]))
    return {"success": True, "message": "批量导入成功：" + "，".join(parts), **result, "errors": []}


@api_router.get(
    "/export",
    summary="导出节点资产",
    description="有 ids 参数时导出选中节点；没有时按当前筛选条件导出。凭证仅导出名称。",
    responses=api_error_responses((401, 403, 422, 500)),
)
def export_nodes(
    request: Request,
    ids: str = Query("", max_length=4000),
    user: User = Depends(require_permission("nodes", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """导出选中节点或当前筛选范围内的节点工作簿。"""
    selected_ids = []
    for item in split_terms(ids):
        if item.isdigit() and int(item) not in selected_ids:
            selected_ids.append(int(item))
    if selected_ids:
        order = {node_id: index for index, node_id in enumerate(selected_ids)}
        query = node_query(session).where(Node.id.in_(selected_ids))
        nodes = session.scalars(query).unique().all()
        nodes.sort(key=lambda node: order.get(node.id, len(order)))
    else:
        nodes = session.scalars(_filtered_nodes(session, request)).unique().all()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    scope_label = "勾选" if selected_ids else "筛选全量"
    write_audit_log(
        session,
        "节点管理",
        "导出节点",
        "导出 {} 条（{}）".format(len(nodes), scope_label),
    )
    session.commit()
    return Response(
        build_node_export_bytes(nodes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="nodes_export_{}.xlsx"'.format(stamp),
            "X-Content-Type-Options": "nosniff",
        },
    )


@api_router.get(
    "/{node_id}",
    response_model=NodeDetailResponse,
    summary="读取节点详情",
    description="返回节点资产、凭证标识及独立 SSH/Nginx 状态，不返回任何凭证明文。",
    responses=api_error_responses((401, 403, 404, 422, 500)),
)
def api_get_node_detail(
    node_id: int = Path(..., ge=1),
    user: User = Depends(require_permission("nodes", "read")),
    session: Session = Depends(get_session),
) -> NodeDetailResponse:
    """读取活动节点的非敏感详情及状态时间。"""
    node = session.scalar(
        select(Node)
        .options(
            joinedload(Node.credential),
            joinedload(Node.creator),
            selectinload(Node.groups),
        )
        .where(Node.id == node_id, Node.is_deleted.is_(False))
    )
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    credential = node.credential
    return NodeDetailResponse(
        node=NodeDetailItem(
            id=node.id,
            hostname=node.hostname,
            ip=node.ip,
            port=node.port,
            environment=node.environment,
            status=node.status,
            is_locked=node.is_locked,
            description=node.description or "",
            nginx_path=node.nginx_path or "",
            nginx_available=node.nginx_available,
            nginx_version=node.nginx_version or "",
            last_probe_at=_beijing_datetime(node.last_probe_at),
            last_nginx_probe_at=_beijing_datetime(node.last_nginx_probe_at),
            credential_name=credential.name if credential else "未配置",
            credential_username=credential.username if credential else "-",
            credential_auth_type=credential.auth_type if credential else "-",
            has_credential=bool(credential and credential.is_enabled),
            groups=[group.name for group in node.groups],
            created_by=node.creator.username if node.creator else "",
            created_at=node.created_at,
            updated_at=node.updated_at,
        )
    )


@api_router.post(
    "/probe",
    response_model=NodeTaskCreatedResponse,
    status_code=202,
    summary="批量探测节点 SSH 连接",
    description="按系统批量上限为活动节点创建后台 SSH/Nginx 双维度探测任务；结果可由任务详情 API 轮询。",
    responses=api_error_responses((401, 403, 404, 422, 500, 503)),
)
def api_batch_probe_nodes(
    request: Request,
    payload: NodeBatchProbeRequest,
    user: User = Depends(require_permission("nodes", "ssh_test")),
    session: Session = Depends(get_session),
) -> NodeTaskCreatedResponse:
    """按系统批量上限创建活动节点的并发探测任务。"""
    node_ids = list(dict.fromkeys(payload.node_ids))
    batch_limit = read_setting(session, "node.batch_max_count", MAX_BATCH_COUNT)
    if len(node_ids) > batch_limit:
        raise HTTPException(status_code=400, detail="单次最多选择 {} 台节点".format(batch_limit))
    nodes = list(
        session.scalars(
            select(Node)
            .where(Node.id.in_(node_ids), Node.is_deleted.is_(False))
            .order_by(Node.id.asc())
        ).all()
    )
    if len(nodes) != len(node_ids):
        raise HTTPException(status_code=404, detail="部分节点不存在或已删除")
    task_id = _enqueue_node_task(
        request,
        session,
        user,
        operation_type="node_batch_test",
        runner=_run_batch_probe(
            request.app.state.database.session_factory,
            node_ids,
            request.app.state.credential_encryption_key,
            batch_limit,
        ),
        detail="批量 SSH 探测 {} 台节点".format(len(nodes)),
        nodes=nodes,
        subject_type="node_batch",
    )
    return NodeTaskCreatedResponse(
        message="已创建后台节点探测任务（{} 台）".format(len(nodes)),
        task_id=task_id,
    )


@api_router.post(
    "/{node_id}/probe",
    response_model=NodeTaskCreatedResponse,
    status_code=202,
    summary="探测单个节点 SSH 连接",
    description="要求节点未锁定且关联启用凭证；任务同时检测 Nginx 能力。",
    responses=api_error_responses((400, 401, 403, 404, 422, 500, 503)),
)
def api_probe_node(
    request: Request,
    node_id: int = Path(..., ge=1),
    user: User = Depends(require_permission("nodes", "ssh_test")),
    session: Session = Depends(get_session),
) -> NodeTaskCreatedResponse:
    """创建单个节点的 SSH 与 Nginx 探测任务。"""
    node = _probeable_node(session, node_id, reject_locked=True)
    task_id = _enqueue_node_task(
        request,
        session,
        user,
        operation_type="node_ssh_test",
        runner=_run_single_probe(
            request.app.state.database.session_factory,
            node_id,
            request.app.state.credential_encryption_key,
        ),
        detail="SSH 探测节点 {}".format(node.hostname),
        nodes=[node],
        subject_type="node",
        subject_id=node_id,
    )
    return NodeTaskCreatedResponse(message="已创建后台节点探测任务", task_id=task_id)


@api_router.post(
    "/{node_id}/system-info",
    response_model=NodeTaskCreatedResponse,
    status_code=202,
    summary="采集节点系统信息",
    description="在后台采集操作系统、内核、CPU、内存、磁盘和运行时间。",
    responses=api_error_responses((400, 401, 403, 404, 422, 500, 503)),
)
def api_collect_node_system_info(
    request: Request,
    node_id: int = Path(..., ge=1),
    user: User = Depends(require_permission("nodes", "ssh_test")),
    session: Session = Depends(get_session),
) -> NodeTaskCreatedResponse:
    """创建单个节点的系统信息采集任务。"""
    node = _probeable_node(session, node_id)
    task_id = _enqueue_node_task(
        request,
        session,
        user,
        operation_type="node_system_info",
        runner=_run_system_info(
            request.app.state.database.session_factory,
            node_id,
            request.app.state.credential_encryption_key,
        ),
        detail="采集节点系统信息 {}".format(node.hostname),
        nodes=[node],
        subject_type="node",
        subject_id=node_id,
    )
    return NodeTaskCreatedResponse(message="已创建系统信息采集任务", task_id=task_id)


@api_router.post(
    "/{node_id}/nginx-probe",
    response_model=NodeTaskCreatedResponse,
    status_code=202,
    summary="检测节点 Nginx 版本",
    description="单独记录 SSH 连通性和 Nginx 命令检测结果；Nginx 不可用不会覆盖 SSH 在线状态。",
    responses=api_error_responses((400, 401, 403, 404, 422, 500, 503)),
)
def api_probe_node_nginx(
    request: Request,
    node_id: int = Path(..., ge=1),
    user: User = Depends(require_permission("nodes", "ssh_test")),
    session: Session = Depends(get_session),
) -> NodeTaskCreatedResponse:
    """创建单个节点的 Nginx 版本探测任务。"""
    node = _probeable_node(session, node_id)
    task_id = _enqueue_node_task(
        request,
        session,
        user,
        operation_type="node_nginx_version",
        runner=_run_nginx_probe(
            request.app.state.database.session_factory,
            node_id,
            request.app.state.credential_encryption_key,
        ),
        detail="检测节点 Nginx 版本 {}".format(node.hostname),
        nodes=[node],
        subject_type="node",
        subject_id=node_id,
    )
    return NodeTaskCreatedResponse(message="已创建 Nginx 版本检测任务", task_id=task_id)


def _probeable_node(
    session: Session,
    node_id: int,
    *,
    reject_locked: bool = False,
) -> Node:
    """验证节点活动状态、锁定状态和关联凭证是否可用于探测。"""
    node = session.scalar(
        select(Node)
        .options(joinedload(Node.credential))
        .where(Node.id == node_id, Node.is_deleted.is_(False))
    )
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    if reject_locked and node.is_locked:
        raise HTTPException(status_code=400, detail="节点已锁定，无法测试连接")
    if node.credential is None:
        raise HTTPException(status_code=400, detail="节点未配置 SSH 凭证")
    if not node.credential.is_enabled:
        raise HTTPException(status_code=400, detail="节点关联凭证已禁用")
    return node


def _enqueue_node_task(
    request: Request,
    session: Session,
    user: User,
    *,
    operation_type: str,
    runner,
    detail: str,
    nodes: List[Node],
    subject_type: str,
    subject_id: Optional[int] = None,
) -> int:
    """提交持久化节点任务并关联安全的节点摘要。"""
    executor = getattr(request.app.state, "task_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="后台任务执行器尚未启动")
    hostnames = [node.hostname for node in nodes]
    ips = [node.ip for node in nodes]
    session.rollback()
    return create_task(
        request.app.state.database.session_factory,
        executor,
        runner,
        operation_type=operation_type,
        detail=detail,
        target_hostnames=hostnames,
        target_ips=ips,
        subject_type=subject_type,
        subject_id=subject_id,
        trigger_user_id=user.id,
        trigger_ip=request_client_ip(request),
    )
