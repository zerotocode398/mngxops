"""提供 Nginx 全新安装向导、历史页面和任务 API。"""

import re
from datetime import datetime, timedelta
from math import ceil
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path as ApiPath, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from ngxops.accounts.models import User
from ngxops.api.contracts import api_error_responses
from ngxops.database.session import get_session
from ngxops.database.session import session_scope
from ngxops.nginx_install.models import NginxInstallRun
from ngxops.nginx_install.services import (
    build_install_configure_opts,
    create_install_batch,
    default_install_modules,
    derive_paths_from_prefix,
    load_install_batch,
)
from ngxops.nodes.models import Node
from ngxops.security.dependencies import (
    PERM_DENIED_MESSAGE,
    PERM_DENIED_TITLE,
    require_authenticated_user,
    require_permission,
)
from ngxops.security.errors import PermissionDenied
from ngxops.settings.service import read_setting
from ngxops.tasks.models import Task, TaskLog
from ngxops.ui import render_page
from ngxops.upgrade.builtin_modules import BUILTIN_ADD_MODULES
from ngxops.upgrade.models import NginxModulePackage, NginxSourcePackage


router = APIRouter(tags=["nginx_install"])
_PAGE_SIZE_OPTIONS = (10, 15, 30, 50)
_DEFAULT_PAGE_SIZE = 15
_STATUS_LABELS = {
    "pending": "等待执行",
    "running": "执行中",
    "success": "安装成功",
    "failed": "安装失败",
    "cancelled": "已取消",
}
_PHASE_LABELS = {
    "pending": "等待执行",
    "checking_tools": "检查编译工具",
    "uploading_package": "上传源码包",
    "extracting_package": "解压源码包",
    "preparing_modules": "准备第三方模块",
    "configuring": "执行 configure",
    "compiling": "执行 make",
    "installing": "执行 make install",
    "verifying": "验证安装",
    "starting": "启动 Nginx",
    "syncing_config": "自动同步配置",
    "success": "安装完成",
    "failed": "安装失败",
    "cancelled": "已取消",
}


class ThirdPartyModuleInput(BaseModel):
    """描述一个在线 Git 或平台离线包第三方模块。"""

    name: str = Field(..., min_length=1, max_length=80)
    source: Literal["git", "package"] = "git"
    git_url: Optional[str] = Field(None, max_length=1000)
    branch: Optional[str] = Field("master", max_length=200)
    package_id: Optional[int] = Field(None, ge=1)


class InstallConfigureRequest(BaseModel):
    """描述全新安装 configure 参数预览请求。"""

    target_prefix: str = Field("/opt/app", min_length=1, max_length=500)
    nginx_user: str = Field("root", min_length=1, max_length=100)
    nginx_group: str = Field("root", min_length=1, max_length=100)
    listen_port: int = Field(80, ge=1, le=65535)
    remote_work_dir: str = Field("/tmp/nginx-upgrade", min_length=1, max_length=500)
    make_jobs: int = Field(4, ge=1, le=32)
    added_modules: List[str] = Field(default_factory=default_install_modules, max_length=120)
    added_third_party: List[ThirdPartyModuleInput] = Field(default_factory=list, max_length=20)
    extra_opts: str = Field("", max_length=5000)


class InstallBatchRequest(InstallConfigureRequest):
    """描述 Nginx 安装批次的目标节点、源码包和编译参数。"""

    node_ids: List[int] = Field(..., min_length=1, max_length=50)
    source_package_id: int = Field(..., ge=1)


class InstallPaths(BaseModel):
    """描述安装前缀推导出的 Nginx 路径。"""

    prefix: str
    nginx_path: str
    main_conf_path: str


class InstallConfigureResponse(BaseModel):
    """描述全新安装 configure 参数及目标路径预览。"""

    success: bool = True
    target_configure_opts: str
    paths: InstallPaths


class SkippedInstallNode(BaseModel):
    """描述未加入安装批次的节点及门禁原因。"""

    id: int
    hostname: str
    ip: str
    reason: str


class InstallBatchResponse(BaseModel):
    """描述已创建的安装批次和未执行目标。"""

    success: bool = True
    batch_number: str
    task_ids: List[int]
    skipped: List[SkippedInstallNode]
    message: str


class InstallBatchTaskItem(BaseModel):
    """描述安装批次内单节点任务的轮询摘要。"""

    task_id: int
    node_id: Optional[int]
    hostname: str
    ip: str
    status: str
    progress: int
    phase: str
    detail: str
    sync_ok: Optional[bool]
    sync_detail: str
    log_url: str
    finished: bool


class InstallBatchProgressResponse(BaseModel):
    """描述安装批次实时进度和节点任务列表。"""

    success: bool = True
    batch_number: str
    tasks: List[InstallBatchTaskItem]
    finished: bool
    all_success: bool
    success_count: int
    fail_count: int
    total: int
    progress: int


def _has_install_permission(
    request: Request,
    session: Session,
    user: User,
    action: str,
) -> bool:
    """读取安装资源的 RBAC 授权结果。"""
    checker = getattr(request.app.state, "permission_checker", None)
    return user.is_superuser or bool(
        checker and checker(session, user, "nginx_install", action)
    )


def _page_context(
    query,
    session: Session,
    page: int,
    per_page: int,
) -> dict:
    """执行安装历史分页并生成公共分页上下文。"""
    total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    pages = max(1, ceil(total / per_page))
    current = min(page, pages)
    return {
        "items": session.scalars(
            query.limit(per_page).offset((current - 1) * per_page)
        ).all(),
        "pagination": {
            "page": current,
            "pages": pages,
            "total": total,
            "per_page": per_page,
            "per_page_options": _PAGE_SIZE_OPTIONS,
        },
    }


def _search_terms(value: str) -> List[str]:
    """拆分安装历史搜索词并按 AND 语义应用。"""
    return [term.strip()[:100] for term in re.split(r"[,，]", value or "") if term.strip()]


def _status_context(task: Task) -> dict:
    """为任务状态生成历史列表使用的标签文案和颜色。"""
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


def _node_install_items(session: Session) -> List[dict]:
    """读取安装向导使用的节点、凭证、分组和门禁摘要。"""
    nodes = session.scalars(
        select(Node)
        .options(joinedload(Node.credential), joinedload(Node.groups))
        .where(Node.is_deleted.is_(False))
        .order_by(Node.hostname.asc(), Node.id.asc())
    ).unique().all()
    items = []
    for node in nodes:
        credential = node.credential
        reason = ""
        if node.is_locked:
            reason = "节点已锁定"
        elif node.status != "online":
            reason = "节点非在线状态"
        elif credential is None or not credential.is_enabled:
            reason = "无可用 SSH 凭证"
        items.append(
            {
                "id": node.id,
                "hostname": node.hostname,
                "ip": node.ip,
                "port": node.port,
                "status": node.status,
                "locked": node.is_locked,
                "has_credential": bool(credential and credential.is_enabled),
                "credential_username": credential.username if credential else "",
                "nginx_available": node.nginx_available,
                "nginx_version": node.nginx_version,
                "groups": ", ".join(group.name for group in node.groups),
                "disabled_reason": reason,
                "can_select": not reason,
            }
        )
    return items


@router.get("/nginx-install/", include_in_schema=False)
def nginx_install_home(
    request: Request,
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> Response:
    """显示 Nginx 安装统计、最近批次和向导入口。"""
    can_read = _has_install_permission(request, session, user, "read")
    can_create = _has_install_permission(request, session, user, "create")
    if not can_read and not can_create:
        raise PermissionDenied(PERM_DENIED_TITLE, PERM_DENIED_MESSAGE)
    since = datetime.utcnow() - timedelta(days=7)
    recent_query = (
        select(NginxInstallRun)
        .join(Task, Task.id == NginxInstallRun.task_id)
        .options(joinedload(NginxInstallRun.task))
        .order_by(NginxInstallRun.created_at.desc(), NginxInstallRun.id.desc())
    )
    if not can_read:
        recent_query = recent_query.where(Task.trigger_user_id == user.id)
    recent_limit = read_setting(session, "dashboard.recent_tasks_count", 20)
    recent_runs = session.scalars(recent_query.limit(recent_limit)).all()
    running_query = select(func.count()).select_from(Task).where(
        Task.operation_type == "nginx_install",
        Task.status.in_(("pending", "running")),
    )
    failed_query = select(func.count()).select_from(Task).where(
        Task.operation_type == "nginx_install",
        Task.status == "failed",
        Task.created_at >= since,
    )
    if not can_read:
        running_query = running_query.where(Task.trigger_user_id == user.id)
        failed_query = failed_query.where(Task.trigger_user_id == user.id)
    context = {
        "package_count": int(
            session.scalar(select(func.count()).select_from(NginxSourcePackage)) or 0
        ),
        "module_package_count": int(
            session.scalar(select(func.count()).select_from(NginxModulePackage)) or 0
        ),
        "running_count": int(session.scalar(running_query) or 0),
        "failed_7d_count": int(session.scalar(failed_query) or 0),
        "recent_runs": recent_runs,
        "can_create": can_create,
        "can_read": can_read,
    }
    return render_page(request, "nginx_install/index.html", context, user, session)


@router.get("/nginx-install/center/", include_in_schema=False)
def nginx_install_center(
    request: Request,
    user: User = Depends(require_permission("nginx_install", "create")),
    session: Session = Depends(get_session),
) -> Response:
    """渲染三步安装向导及目标节点、源码包和模块包。"""
    nodes = _node_install_items(session)
    packages = session.scalars(
        select(NginxSourcePackage)
        .options(joinedload(NginxSourcePackage.creator))
        .order_by(NginxSourcePackage.created_at.desc(), NginxSourcePackage.id.desc())
    ).all()
    module_packages = session.scalars(
        select(NginxModulePackage)
        .options(joinedload(NginxModulePackage.creator))
        .order_by(NginxModulePackage.created_at.desc(), NginxModulePackage.id.desc())
    ).all()
    recent_runs = session.scalars(
        select(NginxInstallRun)
        .join(Task, Task.id == NginxInstallRun.task_id)
        .options(joinedload(NginxInstallRun.task))
        .order_by(NginxInstallRun.created_at.desc(), NginxInstallRun.id.desc())
        .limit(read_setting(session, "dashboard.recent_tasks_count", 20))
    ).all()
    return render_page(
        request,
        "nginx_install/center.html",
        {
            "nodes": nodes,
            "packages": packages,
            "module_packages": module_packages,
            "recent_runs": recent_runs,
            "default_work_dir": read_setting(session, "upgrade.default_work_dir", "/tmp/nginx-upgrade"),
            "default_make_jobs": read_setting(session, "upgrade.make_jobs_default", 4),
            "default_prefix": read_setting(session, "install.default_prefix", "/opt/app"),
            "default_user": read_setting(session, "install.default_user", "root"),
            "default_group": read_setting(session, "install.default_group", "root"),
            "default_listen_port": read_setting(session, "install.default_listen_port", 80),
            "batch_max_count": read_setting(session, "node.batch_max_count", 3),
            "builtin_modules": BUILTIN_ADD_MODULES,
            "default_modules": default_install_modules(),
            "can_read": _has_install_permission(request, session, user, "read"),
            "nodes_json": _json_for_script(nodes),
            "module_packages_json": _json_for_script(
                [
                    {
                        "id": item.id,
                        "name": item.name,
                        "version": item.version,
                    }
                    for item in module_packages
                ]
            ),
        },
        user,
        session,
    )


def _json_for_script(value: object) -> str:
    """序列化供安装向导脚本读取的数据并转义 HTML 特殊分隔符。"""
    import json

    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


@router.get("/nginx-install/history/", include_in_schema=False)
def nginx_install_history(
    request: Request,
    search: str = Query("", max_length=300),
    status: str = Query("", max_length=20),
    page: int = Query(1, ge=1),
    per_page: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=50),
    user: User = Depends(require_permission("nginx_install", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """按节点、版本、路径、批次和状态筛选安装历史。"""
    query = (
        select(NginxInstallRun)
        .join(Task, Task.id == NginxInstallRun.task_id)
        .options(joinedload(NginxInstallRun.task))
        .order_by(NginxInstallRun.created_at.desc(), NginxInstallRun.id.desc())
    )
    if status == "running":
        query = query.where(Task.status.in_(("pending", "running")))
    elif status in _STATUS_LABELS:
        query = query.where(Task.status == status)
    for term in _search_terms(search):
        pattern = "%{}%".format(term)
        query = query.where(
            or_(
                NginxInstallRun.node_hostname.ilike(pattern),
                NginxInstallRun.node_ip.ilike(pattern),
                NginxInstallRun.target_version.ilike(pattern),
                NginxInstallRun.target_prefix.ilike(pattern),
                NginxInstallRun.batch_number.ilike(pattern),
            )
        )
    result = _page_context(query, session, page, per_page)
    runs = result.pop("items")
    user_ids = {
        run.task.trigger_user_id
        for run in runs
        if run.task.trigger_user_id is not None
    }
    users = {
        row.id: row.username
        for row in session.scalars(select(User).where(User.id.in_(user_ids))).all()
    } if user_ids else {}
    result.update(
        {
            "runs": runs,
            "operator_names": users,
            "search": search,
            "status_filter": status,
            "status_choices": [
                ("", "全部状态"),
                ("running", "进行中"),
                ("success", _STATUS_LABELS["success"]),
                ("failed", _STATUS_LABELS["failed"]),
                ("cancelled", _STATUS_LABELS["cancelled"]),
            ],
            "page_size_options": _PAGE_SIZE_OPTIONS,
        }
    )
    return render_page(request, "nginx_install/history.html", result, user, session)


@router.get("/nginx-install/task/{task_id}/log/", include_in_schema=False)
def nginx_install_task_log(
    request: Request,
    task_id: int = ApiPath(..., ge=1),
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> Response:
    """显示单节点安装配置快照、任务状态和完整日志。"""
    run = session.scalars(
        select(NginxInstallRun)
        .join(Task, Task.id == NginxInstallRun.task_id)
        .options(joinedload(NginxInstallRun.task))
        .where(NginxInstallRun.task_id == task_id)
    ).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="安装任务不存在")
    task = run.task
    can_read = _has_install_permission(request, session, user, "read")
    can_create = _has_install_permission(request, session, user, "create")
    if not can_read and not (can_create and task.trigger_user_id == user.id):
        raise PermissionDenied(PERM_DENIED_TITLE, PERM_DENIED_MESSAGE)
    logs = session.scalars(
        select(TaskLog)
        .where(TaskLog.task_id == task_id)
        .order_by(TaskLog.id.asc())
        .limit(1000)
    ).all()
    return render_page(
        request,
        "nginx_install/task_log.html",
        {
            "run": run,
            "task": task,
            "logs": logs,
            "phase_label": _PHASE_LABELS.get(run.phase, run.phase),
            "status_context": _status_context(task),
            "configure_display": "./configure \\\n{}".format(
                run.target_configure_opts
            ),
            "can_cancel": (
                can_create
                and task.status in ("pending", "running")
                and run.phase
                in ("pending", "checking_tools", "uploading_package", "extracting_package", "preparing_modules")
            ),
        },
        user,
        session,
    )


@router.post(
    "/api/nginx-install/configure-preview",
    response_model=InstallConfigureResponse,
    summary="预览 Nginx 安装 configure 参数",
    description="校验全新安装前缀、账户、监听端口、官方模块和第三方模块参数。",
    responses=api_error_responses((400, 401, 403, 422, 500)),
)
def install_configure_preview(
    payload: InstallConfigureRequest,
    user: User = Depends(require_permission("nginx_install", "create")),
) -> InstallConfigureResponse:
    """返回已校验的 configure 参数字符串和安装路径。"""
    try:
        values = payload.model_dump(exclude_none=True)
        options = build_install_configure_opts(
            values["target_prefix"],
            values["added_modules"],
            values["added_third_party"],
            values["remote_work_dir"],
            values["nginx_user"],
            values["nginx_group"],
            values["extra_opts"],
        )
        return InstallConfigureResponse(
            target_configure_opts=options,
            paths=derive_paths_from_prefix(values["target_prefix"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/api/nginx-install/tasks",
    response_model=InstallBatchResponse,
    summary="创建 Nginx 全新安装批次",
    description=(
        "每个可执行节点创建一条 nginx_install 统一任务并排入异步执行器。"
        "安装门禁仅要求节点在线、未锁定且关联 SSH 凭证启用；检测到已有 Nginx 时允许继续并提示覆盖风险。"
    ),
    responses=api_error_responses((400, 401, 403, 409, 422, 500)),
)
def create_install_tasks(
    request: Request,
    payload: InstallBatchRequest,
    user: User = Depends(require_permission("nginx_install", "create")),
    session: Session = Depends(get_session),
) -> InstallBatchResponse:
    """全量验证安装参数并创建异步批次。"""
    batch_limit = read_setting(session, "node.batch_max_count", 3)
    if len(payload.node_ids) > batch_limit:
        raise HTTPException(status_code=400, detail="单次最多选择 {} 台节点".format(batch_limit))
    session.rollback()
    try:
        values = payload.model_dump(exclude_none=True)
        values["target_prefix"] = values["target_prefix"].strip() or "/opt/app"
        values["remote_work_dir"] = values["remote_work_dir"].strip()
        values["added_modules"] = list(dict.fromkeys(values["added_modules"]))
        values["added_third_party"] = [
            item.model_dump(exclude_none=True)
            for item in payload.added_third_party
        ]
        values["target_configure_opts"] = build_install_configure_opts(
            values["target_prefix"],
            values["added_modules"],
            values["added_third_party"],
            values["remote_work_dir"],
            values["nginx_user"],
            values["nginx_group"],
            values["extra_opts"],
        )
        result = create_install_batch(
            request.app.state.database.session_factory,
            request.app.state.task_executor,
            request.app.state.credential_encryption_key,
            request.app.state.settings.upgrade_package_dir,
            user.id,
            values,
            batch_limit=batch_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return InstallBatchResponse(
        batch_number=result["batch_number"],
        task_ids=result["task_ids"],
        skipped=result["skipped"],
        message="已创建 {} 个安装任务，批次 {}".format(
            len(result["task_ids"]), result["batch_number"]
        ),
    )


@router.get(
    "/api/nginx-install/batches/{batch_number}",
    response_model=InstallBatchProgressResponse,
    summary="读取 Nginx 安装批次进度",
    description="返回每节点任务的持久化状态、阶段、进度、同步结果和日志入口。",
    responses=api_error_responses((401, 403, 404, 422, 500)),
)
def get_install_batch_progress(
    request: Request,
    batch_number: str = ApiPath(..., min_length=14, max_length=32),
    user: User = Depends(require_authenticated_user),
) -> InstallBatchProgressResponse:
    """返回当前用户可见的安装批次实时状态。"""
    session_factory = request.app.state.database.session_factory
    with session_scope(session_factory) as session:
        can_read = _has_install_permission(request, session, user, "read")
        can_create = _has_install_permission(request, session, user, "create")
        if not can_read and not can_create:
            raise PermissionDenied(PERM_DENIED_TITLE, PERM_DENIED_MESSAGE)
        owner_id = None if can_read or user.is_superuser else user.id
    try:
        result = load_install_batch(
            session_factory, batch_number, owner_user_id=owner_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return InstallBatchProgressResponse(**result)
