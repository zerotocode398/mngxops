"""提供源码包管理、升级向导、历史和回滚路由。"""

import json
import re
from math import ceil
from pathlib import Path
from typing import List, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path as ApiPath, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload
from starlette.concurrency import run_in_threadpool

from ngxops.accounts.models import User
from ngxops.api.contracts import api_error_responses
from ngxops.audit.service import request_client_ip
from ngxops.database.session import get_session
from ngxops.nodes.models import Node
from ngxops.security.dependencies import require_permission
from ngxops.settings.service import read_setting
from ngxops.tasks.executor import cancel_task
from ngxops.tasks.models import Task, TaskLog
from ngxops.ui import render_page
from ngxops.upgrade.builtin_modules import BUILTIN_ADD_MODULES
from ngxops.upgrade.models import NginxModulePackage, NginxSourcePackage, NginxUpgradeRun
from ngxops.upgrade.services import (
    PackageConflict,
    compute_target_configure_opts,
    create_rollback_task,
    create_upgrade_batch,
    delete_package,
    fetch_node_nginx_v,
    join_configure_opts,
    parse_nginx_v_output,
    persist_uploaded_package,
    rewrite_configure_prefix,
    tokenize_configure_args,
    validate_target_prefix,
    validate_remote_work_dir,
)


router = APIRouter(tags=["upgrade"])
_PAGE_SIZE_OPTIONS = (10, 15, 30, 50)
_DEFAULT_PAGE_SIZE = 15
_STATUS_LABELS = {
    "pending": "等待执行",
    "running": "执行中",
    "success": "升级成功",
    "failed": "升级失败",
    "cancelled": "已取消",
}
_MODE_LABELS = {"upgrade": "平滑升级（同路径）", "switch_path": "切换路径升级"}


class ParseConfigRequest(BaseModel):
    """声明解析 nginx -V 文本的请求体。"""

    raw_output: str = Field(..., min_length=1, max_length=200000)


class ConfigurePayload(BaseModel):
    """描述单节点 nginx -V 编译参数基线。"""

    node_id: int = Field(..., ge=1)
    current_version: str = Field("", max_length=50)
    current_configure_opts: str = Field(..., min_length=1, max_length=20000)
    params: List[str] = Field(default_factory=list, max_items=250)
    prefix: str = Field("", max_length=500)
    binary_path: str = Field(..., min_length=1, max_length=500)


class ThirdPartyModulePayload(BaseModel):
    """描述单个在线 Git 或平台托管的第三方模块来源。"""

    name: str = Field(..., min_length=1, max_length=100)
    source: Literal["git", "package"] = "git"
    git_url: Optional[str] = Field(None, max_length=2048)
    branch: Optional[str] = Field("master", max_length=200)
    package_id: Optional[int] = Field(None, ge=1)


class UpgradeBatchRequest(BaseModel):
    """声明多节点升级批次及模块参数。"""

    node_ids: List[int] = Field(..., min_items=1, max_items=50)
    source_package: int = Field(..., ge=1)
    upgrade_mode: str = Field("upgrade", max_length=20)
    remote_work_dir: str = Field("/tmp/nginx-upgrade", max_length=500)
    make_jobs: int = Field(4, ge=1, le=32)
    target_version: str = Field("", max_length=50)
    target_prefix: str = Field("", max_length=500)
    added_modules: List[str] = Field(default_factory=list, max_items=120)
    removed_modules: List[str] = Field(default_factory=list, max_items=250)
    added_third_party: List[ThirdPartyModulePayload] = Field(default_factory=list, max_items=20)
    nodes_payload: List[ConfigurePayload] = Field(..., min_items=1, max_items=50)


class NginxConfigData(BaseModel):
    """描述节点 `nginx -V` 输出中可用的编译基线。"""

    version: str
    configure_opts: str
    prefix: str
    binary_path: str
    params: List[str]
    builtin_modules: List[str]
    third_party_modules: List[str]


class ParseConfigResponse(BaseModel):
    """描述已解析的 Nginx 版本与 configure 参数。"""

    success: bool = True
    data: NginxConfigData


class ComputeConfigRequest(BaseModel):
    """声明 configure 参数预览的输入内容。"""

    current_params: List[str] = Field(default_factory=list, max_items=250)
    added_modules: List[str] = Field(default_factory=list, max_items=120)
    removed_modules: List[str] = Field(default_factory=list, max_items=250)
    added_third_party: List[ThirdPartyModulePayload] = Field(default_factory=list, max_items=20)
    remote_work_dir: str = Field("/tmp/nginx-upgrade", max_length=500)
    upgrade_mode: str = Field("upgrade", max_length=20)
    target_prefix: str = Field("", max_length=500)


class ComputeConfigResponse(BaseModel):
    """描述目标 configure 参数预览。"""

    success: bool = True
    target_opts: str


class UpgradeTaskCreateResponse(BaseModel):
    """描述批量升级任务创建结果。"""

    success: bool = True
    batch_number: str
    task_id: int
    task_ids: List[int]
    message: str


class UpgradeRollbackResponse(BaseModel):
    """描述回滚任务排队结果。"""

    success: bool = True
    task_id: int
    batch_number: str
    message: str


class UpgradeCancelResponse(BaseModel):
    """描述升级任务协作取消结果。"""

    success: bool = True
    message: str
    task_id: int


class SourcePackageCheckResponse(BaseModel):
    """描述当前用户的源码版本冲突检查结果。"""

    success: bool = True
    exists: bool
    version: str


class ModulePackageCheckResponse(BaseModel):
    """描述当前用户的模块名称和版本冲突检查结果。"""

    success: bool = True
    exists: bool
    name: str
    version: str


def _package_limit(request: Request, session: Session) -> int:
    """读取进程配置中的包上传大小上限。"""
    return read_setting(
        session,
        "upgrade.package_max_size_mb",
        request.app.state.settings.upgrade_package_max_size_mb,
    )


def _has_upgrade_permission(request: Request, session: Session, user: User, action: str) -> bool:
    """检查当前用户是否具备指定升级权限。"""
    checker = getattr(request.app.state, "permission_checker", None)
    return user.is_superuser or bool(
        checker and checker(session, user, "upgrade", action)
    )


def _page_context(
    request: Request,
    session: Session,
    query,
    page: int,
    per_page: int,
) -> dict:
    """读取分页数据并生成公共分页上下文。"""
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    pages = max(1, ceil(total / per_page))
    current_page = min(page, pages)
    items = session.scalars(
        query.limit(per_page).offset((current_page - 1) * per_page)
    ).all()
    return {
        "items": items,
        "pagination": {
            "page": current_page,
            "pages": pages,
            "total": total,
            "per_page": per_page,
            "per_page_options": _PAGE_SIZE_OPTIONS,
        },
    }


def _search_terms(value: str) -> List[str]:
    """拆分升级历史中的逗号关键词。"""
    return [part.strip()[:100] for part in re.split(r"[,，]", value or "") if part.strip()]


def _upload_response(
    request: Request,
    message: str,
    *,
    success: bool,
    redirect_to: str,
    status_code: int = 200,
    need_overwrite: bool = False,
    duplicate: bool = False,
) -> Response:
    """按 AJAX 或普通表单请求格式返回上传结果。"""
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", ""):
        return Response(
            content=json.dumps(
                {
                    "success": success,
                    "message": message,
                    "redirect": redirect_to,
                    "need_overwrite": need_overwrite,
                    "md5_duplicate": duplicate,
                },
                ensure_ascii=False,
            ),
            status_code=status_code,
            media_type="application/json",
        )
    destination = redirect_to + ("?error=" + quote(message) if not success else "?uploaded=1")
    return RedirectResponse(destination, status_code=303)


def _format_size(size: int) -> str:
    """将字节数转换为便于列表扫描的大小文本。"""
    if size >= 1024 * 1024:
        return "{:.2f} MB".format(size / 1024 / 1024)
    if size >= 1024:
        return "{:.1f} KB".format(size / 1024)
    return "{} B".format(size)


def _status_context(task: Task) -> dict:
    """构造任务中心和升级历史共用的状态展示值。"""
    return {
        "status_label": _STATUS_LABELS.get(task.status, task.status),
        "status_class": {
            "pending": "secondary",
            "running": "info",
            "success": "success",
            "failed": "danger",
            "cancelled": "secondary",
        }.get(task.status, "secondary"),
    }


@router.get("/upgrade/", include_in_schema=False)
def upgrade_home(
    user: User = Depends(require_permission("upgrade", "read")),
) -> Response:
    """将 Nginx 升级首页导向升级中心。"""
    return RedirectResponse("/upgrade/center/", status_code=302)


@router.get("/upgrade/center/", include_in_schema=False)
def upgrade_center(
    request: Request,
    user: User = Depends(require_permission("upgrade", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """渲染升级向导、可用节点、包列表和最近批次。"""
    nodes = session.scalars(
        select(Node)
        .options(joinedload(Node.credential))
        .where(Node.is_deleted.is_(False), Node.is_locked.is_(False))
        .order_by(Node.hostname.asc())
    ).unique().all()
    node_items = []
    for node in nodes:
        if node.status != "online" or node.nginx_available is not True:
            continue
        if node.credential is None or not node.credential.is_enabled:
            continue
        node_items.append(node)
    packages = session.scalars(
        select(NginxSourcePackage).options(joinedload(NginxSourcePackage.creator))
        .order_by(NginxSourcePackage.created_at.desc(), NginxSourcePackage.id.desc())
    ).all()
    module_packages = session.scalars(
        select(NginxModulePackage).options(joinedload(NginxModulePackage.creator))
        .order_by(NginxModulePackage.created_at.desc(), NginxModulePackage.id.desc())
    ).all()
    latest_runs = session.scalars(
        select(NginxUpgradeRun)
        .join(Task, Task.id == NginxUpgradeRun.task_id)
        .options(joinedload(NginxUpgradeRun.task))
        .order_by(NginxUpgradeRun.created_at.desc())
        .limit(read_setting(session, "dashboard.recent_tasks_count", 20))
    ).all()
    return render_page(
        request,
        "upgrade/center.html",
        {
            "nodes": node_items,
            "packages": packages,
            "module_packages": module_packages,
            "latest_runs": latest_runs,
            "default_work_dir": read_setting(session, "upgrade.default_work_dir", "/tmp/nginx-upgrade"),
            "default_make_jobs": read_setting(session, "upgrade.make_jobs_default", 4),
            "batch_max_count": read_setting(session, "node.batch_max_count", 3),
            "builtin_modules": list(BUILTIN_ADD_MODULES),
            "can_create": _has_upgrade_permission(request, session, user, "create"),
            "can_execute": _has_upgrade_permission(request, session, user, "execute"),
        },
        user,
        session,
    )


@router.get("/upgrade/packages/", include_in_schema=False)
def source_package_list(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=50),
    user: User = Depends(require_permission("upgrade", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """分页显示 Nginx 源码包及上传信息。"""
    query = select(NginxSourcePackage).options(joinedload(NginxSourcePackage.creator)).order_by(
        NginxSourcePackage.created_at.desc(), NginxSourcePackage.id.desc()
    )
    result = _page_context(request, session, query, page, per_page)
    result["packages"] = result.pop("items")
    result["package_kind"] = "source"
    result["per_page_options"] = _PAGE_SIZE_OPTIONS
    result["can_create"] = _has_upgrade_permission(request, session, user, "create")
    result["can_delete"] = _has_upgrade_permission(request, session, user, "delete")
    return render_page(request, "upgrade/packages.html", result, user, session)


@router.get("/upgrade/packages/upload/", include_in_schema=False)
def source_package_upload_page(
    request: Request,
    user: User = Depends(require_permission("upgrade", "create")),
    session: Session = Depends(get_session),
) -> Response:
    """渲染源码包上传表单。"""
    return render_page(
        request,
        "upgrade/package_upload.html",
        {"package_kind": "source", "package_limit": _package_limit(request, session)},
        user,
        session,
    )


@router.post("/upgrade/packages/upload/", include_in_schema=False)
async def upload_source_package(
    request: Request,
    name: str = Form(""),
    version: str = Form(""),
    description: str = Form(""),
    is_official: Optional[str] = Form(None),
    overwrite: Optional[str] = Form(None),
    package_file: UploadFile = File(...),
    user: User = Depends(require_permission("upgrade", "create")),
    session: Session = Depends(get_session),
) -> Response:
    """校验并保存 Nginx 源码归档。"""
    limit = _package_limit(request, session)
    content = await package_file.read(limit * 1024 * 1024 + 1)
    destination = "/upgrade/packages/"
    if len(content) > limit * 1024 * 1024:
        return _upload_response(
            request,
            "文件大小不能超过 {}MB".format(limit),
            success=False,
            redirect_to=destination,
            status_code=413,
        )
    await run_in_threadpool(session.rollback)
    try:
        record, replaced = await run_in_threadpool(
            persist_uploaded_package,
            request.app.state.database.session_factory,
            request.app.state.settings.upgrade_package_dir,
            package_kind="source",
            filename=package_file.filename or "",
            content=content,
            user_id=user.id,
            name=name,
            version=version,
            description=description,
            is_official=is_official in ("1", "on", "true"),
            overwrite=overwrite in ("1", "on", "true"),
        )
    except PackageConflict as exc:
        return _upload_response(
            request,
            str(exc),
            success=False,
            redirect_to=destination,
            status_code=409,
            need_overwrite=exc.conflict_type == "version",
            duplicate=exc.conflict_type == "md5",
        )
    except ValueError as exc:
        return _upload_response(
            request, str(exc), success=False, redirect_to=destination, status_code=400
        )
    message = "源码包 {} (nginx-{}) {}".format(
        record.name, record.version, "覆盖更新" if replaced else "上传成功"
    )
    return _upload_response(
        request, message, success=True, redirect_to=destination
    )


@router.get("/upgrade/modules/", include_in_schema=False)
def module_package_list(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=50),
    user: User = Depends(require_permission("upgrade", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """分页显示第三方模块离线包。"""
    query = select(NginxModulePackage).options(joinedload(NginxModulePackage.creator)).order_by(
        NginxModulePackage.created_at.desc(), NginxModulePackage.id.desc()
    )
    result = _page_context(request, session, query, page, per_page)
    result["packages"] = result.pop("items")
    result["package_kind"] = "module"
    result["can_create"] = _has_upgrade_permission(request, session, user, "create")
    result["can_delete"] = _has_upgrade_permission(request, session, user, "delete")
    return render_page(request, "upgrade/packages.html", result, user, session)


@router.get("/upgrade/modules/upload/", include_in_schema=False)
def module_package_upload_page(
    request: Request,
    user: User = Depends(require_permission("upgrade", "create")),
    session: Session = Depends(get_session),
) -> Response:
    """渲染第三方模块离线包上传表单。"""
    return render_page(
        request,
        "upgrade/package_upload.html",
        {"package_kind": "module", "package_limit": _package_limit(request, session)},
        user,
        session,
    )


@router.post("/upgrade/modules/upload/", include_in_schema=False)
async def upload_module_package(
    request: Request,
    name: str = Form(""),
    version: str = Form(""),
    description: str = Form(""),
    overwrite: Optional[str] = Form(None),
    package_file: UploadFile = File(...),
    user: User = Depends(require_permission("upgrade", "create")),
    session: Session = Depends(get_session),
) -> Response:
    """校验并保存第三方 Nginx 模块归档。"""
    limit = _package_limit(request, session)
    content = await package_file.read(limit * 1024 * 1024 + 1)
    destination = "/upgrade/modules/"
    if len(content) > limit * 1024 * 1024:
        return _upload_response(
            request,
            "文件大小不能超过 {}MB".format(limit),
            success=False,
            redirect_to=destination,
            status_code=413,
        )
    await run_in_threadpool(session.rollback)
    try:
        record, replaced = await run_in_threadpool(
            persist_uploaded_package,
            request.app.state.database.session_factory,
            request.app.state.settings.upgrade_package_dir,
            package_kind="module",
            filename=package_file.filename or "",
            content=content,
            user_id=user.id,
            name=name,
            version=version,
            description=description,
            overwrite=overwrite in ("1", "on", "true"),
        )
    except PackageConflict as exc:
        return _upload_response(
            request,
            str(exc),
            success=False,
            redirect_to=destination,
            status_code=409,
            need_overwrite=exc.conflict_type == "version",
            duplicate=exc.conflict_type == "md5",
        )
    except ValueError as exc:
        return _upload_response(
            request, str(exc), success=False, redirect_to=destination, status_code=400
        )
    label = record.name + (" ({})".format(record.version) if record.version else "")
    message = "模块包 {} {}".format(label, "覆盖更新" if replaced else "上传成功")
    return _upload_response(request, message, success=True, redirect_to=destination)


@router.post("/upgrade/packages/{package_id}/delete/", include_in_schema=False)
def delete_source_package(
    request: Request,
    package_id: int = ApiPath(..., ge=1),
    user: User = Depends(require_permission("upgrade", "delete")),
    session: Session = Depends(get_session),
) -> Response:
    """删除未被活跃升级任务使用的源码包。"""
    session.rollback()
    try:
        deleted = delete_package(
            request.app.state.database.session_factory,
            request.app.state.settings.upgrade_package_dir,
            package_id,
            "source",
        )
    except ValueError as exc:
        return RedirectResponse("/upgrade/packages/?error=" + quote(str(exc)), status_code=303)
    if not deleted:
        raise HTTPException(status_code=404, detail="源码包不存在")
    return RedirectResponse("/upgrade/packages/?deleted=1", status_code=303)


@router.post("/upgrade/modules/{package_id}/delete/", include_in_schema=False)
def delete_module_package(
    request: Request,
    package_id: int = ApiPath(..., ge=1),
    user: User = Depends(require_permission("upgrade", "delete")),
    session: Session = Depends(get_session),
) -> Response:
    """删除未被活跃升级任务使用的离线模块包。"""
    session.rollback()
    try:
        deleted = delete_package(
            request.app.state.database.session_factory,
            request.app.state.settings.upgrade_package_dir,
            package_id,
            "module",
        )
    except ValueError as exc:
        return RedirectResponse("/upgrade/modules/?error=" + quote(str(exc)), status_code=303)
    if not deleted:
        raise HTTPException(status_code=404, detail="模块包不存在")
    return RedirectResponse("/upgrade/modules/?deleted=1", status_code=303)


@router.get("/upgrade/packages/{package_id}/download/", include_in_schema=False)
def download_source_package(
    request: Request,
    package_id: int = ApiPath(..., ge=1),
    user: User = Depends(require_permission("upgrade", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """下载平台托管的 Nginx 源码包。"""
    package = session.get(NginxSourcePackage, package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="源码包不存在")
    path = request.app.state.settings.upgrade_package_dir / package.file_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="源码包文件缺失")
    extension = ".tar.gz" if package.file_name.lower().endswith(".tar.gz") else ".tgz"
    return FileResponse(path, filename="nginx-{}{}".format(package.version, extension))


@router.get("/upgrade/modules/{package_id}/download/", include_in_schema=False)
def download_module_package(
    request: Request,
    package_id: int = ApiPath(..., ge=1),
    user: User = Depends(require_permission("upgrade", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """下载平台托管的第三方模块离线包。"""
    package = session.get(NginxModulePackage, package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="模块包不存在")
    path = request.app.state.settings.upgrade_package_dir / package.file_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="模块包文件缺失")
    extension = ".zip" if package.file_name.lower().endswith(".zip") else (
        ".tar.gz" if package.file_name.lower().endswith(".tar.gz") else ".tgz"
    )
    label = package.name + ("-{}".format(package.version) if package.version else "")
    return FileResponse(path, filename=label + extension)


@router.get("/api/upgrade/packages/check", response_model=SourcePackageCheckResponse, include_in_schema=True, summary="检查源码包版本冲突", responses=api_error_responses((401, 403, 422, 500)))
def check_source_package(
    version: str = Query("", max_length=50),
    user: User = Depends(require_permission("upgrade", "create")),
    session: Session = Depends(get_session),
) -> SourcePackageCheckResponse:
    """检查当前用户是否已上传同版本源码包。"""
    exists = bool(version and session.scalar(select(NginxSourcePackage.id).where(
        NginxSourcePackage.version == version.strip(), NginxSourcePackage.created_by == user.id
    )))
    return SourcePackageCheckResponse(exists=exists, version=version)


@router.get("/api/upgrade/modules/check", response_model=ModulePackageCheckResponse, include_in_schema=True, summary="检查模块包名称版本冲突", responses=api_error_responses((401, 403, 422, 500)))
def check_module_package(
    name: str = Query("", max_length=100),
    version: str = Query("", max_length=50),
    user: User = Depends(require_permission("upgrade", "create")),
    session: Session = Depends(get_session),
) -> ModulePackageCheckResponse:
    """检查当前用户是否已上传同名同版本模块包。"""
    exists = bool(name and session.scalar(select(NginxModulePackage.id).where(
        NginxModulePackage.name == name.strip(),
        NginxModulePackage.version == version.strip(),
        NginxModulePackage.created_by == user.id,
    )))
    return ModulePackageCheckResponse(exists=exists, name=name, version=version)


@router.post("/api/upgrade/nodes/{node_id}/nginx-v", response_model=ParseConfigResponse, summary="读取节点 Nginx 编译参数", description="连接 SSH 在线节点执行 nginx -V，要求 upgrade.create 权限。", responses=api_error_responses((400, 401, 403, 404, 422, 500)))
def read_node_nginx_v(
    request: Request,
    node_id: int = ApiPath(..., ge=1),
    user: User = Depends(require_permission("upgrade", "create")),
    session: Session = Depends(get_session),
) -> ParseConfigResponse:
    """在 FastAPI 同步工作线程中读取单节点编译参数。"""
    if session.get(Node, node_id) is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    try:
        data = fetch_node_nginx_v(
            request.app.state.database.session_factory,
            request.app.state.credential_encryption_key,
            node_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ParseConfigResponse(data=data)


@router.post("/api/upgrade/parse-config", response_model=ParseConfigResponse, summary="解析 nginx -V 输出", responses=api_error_responses((401, 403, 422, 500)))
def parse_config_api(
    payload: ParseConfigRequest,
    user: User = Depends(require_permission("upgrade", "create")),
) -> ParseConfigResponse:
    """将 nginx -V 原始文本解析为结构化参数。"""
    return ParseConfigResponse(data=parse_nginx_v_output(payload.raw_output))


@router.post("/api/upgrade/compute-config", response_model=ComputeConfigResponse, summary="预览目标 configure 参数", responses=api_error_responses((400, 401, 403, 422, 500)))
def compute_config_api(
    payload: ComputeConfigRequest,
    user: User = Depends(require_permission("upgrade", "create")),
) -> ComputeConfigResponse:
    """计算模块增减后的 configure 参数预览。"""
    try:
        if payload.upgrade_mode not in ("upgrade", "switch_path"):
            raise ValueError("升级模式无效")
        work_dir = validate_remote_work_dir(payload.remote_work_dir)
        target = compute_target_configure_opts(
            payload.current_params,
            payload.added_modules,
            payload.removed_modules,
            payload.added_third_party,
            work_dir,
        )
        if payload.upgrade_mode == "switch_path":
            prefix = validate_target_prefix(payload.target_prefix)
            target = join_configure_opts(
                rewrite_configure_prefix(tokenize_configure_args(target), prefix)
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ComputeConfigResponse(target_opts=target)


@router.post("/api/upgrade/tasks", response_model=UpgradeTaskCreateResponse, summary="创建批量 Nginx 升级任务", description="在一个数据库事务中创建每节点一条任务，再交给统一后台执行器。", responses=api_error_responses((400, 401, 403, 409, 422, 500)))
def create_upgrade_tasks_api(
    request: Request,
    payload: UpgradeBatchRequest,
    user: User = Depends(require_permission("upgrade", "execute")),
    session: Session = Depends(get_session),
) -> UpgradeTaskCreateResponse:
    """全量校验升级批次并排入统一任务线程池。"""
    batch_limit = read_setting(session, "node.batch_max_count", 3)
    if len(payload.node_ids) > batch_limit or len(payload.nodes_payload) > batch_limit:
        raise HTTPException(status_code=400, detail="单次最多选择 {} 台节点".format(batch_limit))
    session.rollback()
    try:
        result = create_upgrade_batch(
            request.app.state.database.session_factory,
            request.app.state.task_executor,
            request.app.state.credential_encryption_key,
            request.app.state.settings.upgrade_package_dir,
            user.id,
            payload.model_dump(),
            batch_limit=batch_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    task_ids = result["task_ids"]
    return UpgradeTaskCreateResponse(
        batch_number=result["batch_number"],
        task_id=task_ids[0],
        task_ids=task_ids,
        message="已创建 {} 个升级任务，批次 {}".format(len(task_ids), result["batch_number"]),
    )


@router.post("/api/upgrade/tasks/{task_id}/cancel", response_model=UpgradeCancelResponse, summary="取消 Nginx 升级任务", responses=api_error_responses((400, 401, 403, 404, 409, 422, 500)))
def cancel_upgrade_task_api(
    request: Request,
    task_id: int = ApiPath(..., ge=1),
    user: User = Depends(require_permission("upgrade", "execute")),
    session: Session = Depends(get_session),
) -> UpgradeCancelResponse:
    """只在获取参数或上传阶段取消升级任务。"""
    task = session.get(Task, task_id)
    run = session.scalars(select(NginxUpgradeRun).where(NginxUpgradeRun.task_id == task_id)).one_or_none()
    if task is None or run is None:
        raise HTTPException(status_code=404, detail="升级任务不存在")
    if task.status not in ("pending", "running"):
        raise HTTPException(status_code=400, detail="当前任务状态不允许取消")
    if run.upgrade_mode == "rollback":
        raise HTTPException(status_code=400, detail="二进制回滚任务不可取消")
    if run.phase not in ("pending", "fetching_config", "uploading_package"):
        raise HTTPException(status_code=400, detail="当前状态不允许取消")
    session.rollback()
    if not cancel_task(
        request.app.state.database.session_factory,
        task_id,
        actor_id=user.id,
        actor_username=user.username,
        actor_ip=request_client_ip(request),
    ):
        raise HTTPException(status_code=409, detail="任务状态已变化，无法取消")
    request.app.state.task_executor.cancel(task_id)
    return UpgradeCancelResponse(message="升级任务已取消", task_id=task_id)


@router.post("/api/upgrade/tasks/{task_id}/rollback", response_model=UpgradeRollbackResponse, summary="创建 Nginx 二进制回滚任务", responses=api_error_responses((400, 401, 403, 404, 409, 422, 500)))
def rollback_upgrade_task_api(
    request: Request,
    task_id: int = ApiPath(..., ge=1),
    user: User = Depends(require_permission("upgrade", "execute")),
    session: Session = Depends(get_session),
) -> UpgradeRollbackResponse:
    """将符合条件的历史升级记录转成异步回滚任务。"""
    session.rollback()
    try:
        result = create_rollback_task(
            request.app.state.database.session_factory,
            request.app.state.task_executor,
            request.app.state.credential_encryption_key,
            user.id,
            task_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UpgradeRollbackResponse(
        task_id=result["task_id"],
        batch_number=result["batch_number"],
        message="回滚任务已加入任务中心",
    )


@router.get("/upgrade/history/", include_in_schema=False)
def upgrade_history(
    request: Request,
    search: str = Query("", max_length=300),
    status: str = Query("", max_length=20),
    page: int = Query(1, ge=1),
    per_page: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=50),
    user: User = Depends(require_permission("upgrade", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """按节点、版本、批次和状态筛选升级历史。"""
    query = (
        select(NginxUpgradeRun)
        .join(Task, Task.id == NginxUpgradeRun.task_id)
        .options(joinedload(NginxUpgradeRun.task))
        .order_by(NginxUpgradeRun.created_at.desc(), NginxUpgradeRun.id.desc())
    )
    if status == "running":
        query = query.where(Task.status.in_(("pending", "running")))
    elif status in _STATUS_LABELS:
        query = query.where(Task.status == status)
    for term in _search_terms(search):
        query = query.where(
            or_(
                NginxUpgradeRun.node_hostname.ilike("%{}%".format(term)),
                NginxUpgradeRun.node_ip.ilike("%{}%".format(term)),
                NginxUpgradeRun.target_version.ilike("%{}%".format(term)),
                NginxUpgradeRun.current_version.ilike("%{}%".format(term)),
                NginxUpgradeRun.batch_number.ilike("%{}%".format(term)),
            )
        )
    result = _page_context(request, session, query, page, per_page)
    result.update(
        {
            "runs": result.pop("items"),
            "search": search,
            "status_filter": status,
            "status_choices": list(_STATUS_LABELS.items()),
            "page_size_options": _PAGE_SIZE_OPTIONS,
        }
    )
    return render_page(request, "upgrade/history.html", result, user, session)


@router.get("/upgrade/tasks/{task_id}/", include_in_schema=False)
def upgrade_task_detail(
    request: Request,
    task_id: int = ApiPath(..., ge=1),
    user: User = Depends(require_permission("upgrade", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """显示升级任务参数差异、实时日志和可用回滚入口。"""
    run = session.scalars(
        select(NginxUpgradeRun)
        .options(joinedload(NginxUpgradeRun.task))
        .where(NginxUpgradeRun.task_id == task_id)
    ).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="升级任务不存在")
    task = run.task
    logs = session.scalars(
        select(TaskLog).where(TaskLog.task_id == task.id).order_by(TaskLog.id.asc())
    ).all()
    current = tokenize_configure_args(run.current_configure_opts)
    target = tokenize_configure_args(run.target_configure_opts)
    context = {
        "run": run,
        "task": task,
        "logs": logs,
        "current_params": current,
        "target_params": target,
        "param_removed": [item for item in current if item not in set(target)],
        "param_added": [item for item in target if item not in set(current)],
        "status_label": _STATUS_LABELS.get(task.status, task.status),
        "mode_label": _MODE_LABELS.get(run.upgrade_mode, "二进制回滚"),
        "can_rollback": (
            _has_upgrade_permission(request, session, user, "execute")
            and run.upgrade_mode != "rollback"
            and task.status in ("success", "failed")
            and bool(run.backup_binary_path)
            and run.rolled_back_at is None
        ),
        "can_cancel": (
            _has_upgrade_permission(request, session, user, "execute")
            and run.upgrade_mode != "rollback"
            and task.status in ("pending", "running")
            and run.phase in ("pending", "fetching_config", "uploading_package")
        ),
        "rolled_back": run.rolled_back_at is not None,
    }
    return render_page(request, "upgrade/task_detail.html", context, user, session)
