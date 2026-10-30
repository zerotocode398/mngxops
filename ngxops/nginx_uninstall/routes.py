"""提供 Nginx 卸载操作台、历史页面和结构化 API。"""

import json
import re
from math import ceil
from types import SimpleNamespace
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload
from starlette.responses import Response

from ngxops.accounts.models import User
from ngxops.api.contracts import api_error_responses
from ngxops.audit.service import request_client_ip
from ngxops.database.session import get_session, session_scope
from ngxops.lifecycle import summarize_lifecycle_runs
from ngxops.nginx_uninstall.models import NginxUninstallRun
from ngxops.nginx_uninstall.services import (
    _BATCH_LIMIT,
    create_uninstall_batch,
    load_uninstall_batch,
    preview_nodes,
)
from ngxops.nodes.models import Node
from ngxops.security.dependencies import require_authenticated_user, require_permission
from ngxops.security.errors import PermissionDenied
from ngxops.settings.service import read_setting
from ngxops.tasks.models import NginxOperationLog, Task, TaskLog
from ngxops.tasks.presentation import format_task_datetime
from ngxops.ui import render_page


router = APIRouter(tags=["nginx_uninstall"])
_PAGE_SIZE_OPTIONS = (10, 15, 30, 50)
_DEFAULT_PAGE_SIZE = 15
_STATUS_LABELS = {
    "pending": "等待执行",
    "running": "执行中",
    "success": "卸载成功",
    "failed": "卸载失败",
    "cancelled": "已取消",
}


def _batch_limit(session: Session) -> int:
    """读取并规范化系统设置中的批量节点上限。"""
    try:
        value = int(read_setting(session, "node.batch_max_count", _BATCH_LIMIT))
        return min(50, max(1, value))
    except (TypeError, ValueError):
        return _BATCH_LIMIT


class UninstallNodeItem(BaseModel):
    """描述卸载向导节点选择器中的一项。"""

    id: int
    hostname: str
    ip: str
    port: int
    groups: List[str]
    nginx_path: str
    nginx_version: str
    nginx_available: Optional[bool]
    status: str
    can_select: bool
    disabled_reason: str


class UninstallNodesResponse(BaseModel):
    """描述符合搜索条件的节点选择器结果。"""

    success: bool = True
    nodes: List[UninstallNodeItem]
    total: int
    batch_max_count: int


class PreviewRequest(BaseModel):
    """描述卸载路径预览的目标节点。"""

    node_ids: List[int] = Field(..., min_length=1, max_length=50)


class PreviewPath(BaseModel):
    """描述一个待确认删除路径及其选择规则。"""

    key: str
    label: str
    path: str
    kind: str
    required: bool
    checked: bool
    editable: bool
    package_owned: bool = False


class PreviewNode(BaseModel):
    """描述单节点卸载来源、运行状态和预览路径。"""

    id: int
    hostname: str = ""
    ip: str = ""
    nginx_path: str = ""
    nginx_available: Optional[bool] = None
    eligible: bool = False
    gate_message: str = ""
    prefix: str = ""
    prefix_source: str = ""
    paths: List[PreviewPath] = Field(default_factory=list)
    running: bool = False
    running_error: str = ""
    install_origin: str = "unknown"
    package_manager: str = ""
    package_name: str = ""
    manage_mode: str = "unknown"
    manage_unit: str = ""
    can_manage_systemd: bool = False
    credential_username: str = ""
    backup_path: str = ""
    work_dir: str = ""
    modules_dir: str = ""
    shallow_prefix: bool = False
    foreign_prefix_paths: List[str] = Field(default_factory=list)


class PreviewResponse(BaseModel):
    """描述多节点卸载预览的结果。"""

    success: bool = True
    nodes: List[PreviewNode]
    batch_max_count: int


class SelectedPath(BaseModel):
    """描述用户确认的一个 Nginx configure 路径。"""

    key: str = Field(..., min_length=1, max_length=100)
    path: str = Field(..., min_length=1, max_length=500)


class UninstallNodeInput(BaseModel):
    """描述单节点卸载来源、路径选择和清理选项。"""

    id: int = Field(..., ge=1)
    install_origin: str
    package_manager: str = ""
    package_name: str = ""
    prefix: str = ""
    remove_backup: bool = False
    remove_workdir: bool = False
    remove_modules: bool = False
    stop_if_running: bool = True
    extra_paths: List[SelectedPath] = Field(default_factory=list, max_length=100)
    acknowledged_foreign_prefix_paths: List[str] = Field(default_factory=list, max_length=100)


class UninstallCreateRequest(BaseModel):
    """描述卸载批次中各节点的最终确认选择。"""

    nodes: List[UninstallNodeInput] = Field(..., min_length=1, max_length=50)


class SkippedUninstallNode(BaseModel):
    """描述未能加入卸载批次的节点及原因。"""

    id: int
    hostname: str
    reason: str


class UninstallCreateResponse(BaseModel):
    """描述已创建的卸载任务批次。"""

    success: bool = True
    batch_number: str
    task_ids: List[int]
    skipped: List[SkippedUninstallNode]
    message: str


class UninstallBatchTask(BaseModel):
    """描述批次内单节点卸载任务的实时状态。"""

    task_id: int
    node_id: Optional[int]
    hostname: str
    ip: str
    status: str
    progress: int
    detail: str
    finished: bool


class UninstallBatchResponse(BaseModel):
    """描述卸载批次中的任务状态集合。"""

    success: bool = True
    batch_number: str
    tasks: List[UninstallBatchTask]
    finished: bool
    total: int
    success_count: int
    fail_count: int
    progress: int


class UninstallLogItem(BaseModel):
    """描述卸载任务日志的一行。"""

    id: int
    task_id: int
    level: str
    message: str
    created_at: str


class UninstallTaskDetailResponse(BaseModel):
    """描述卸载任务状态、结果树和增量日志。"""

    success: bool = True
    id: int
    status: str
    detail: str
    progress: int
    result_tree: Optional[dict]
    logs: List[UninstallLogItem]
    next_log_id: int
    has_more_logs: bool
    batch_active: bool = False


def _has_permission(request: Request, session: Session, user: User, action: str) -> bool:
    """读取当前用户对 Nginx 卸载资源的授权结果。"""
    checker = getattr(request.app.state, "permission_checker", None)
    return user.is_superuser or bool(checker and checker(session, user, "nginx_uninstall", action))


def _node_disabled_reason(node: Node, active_ids: set) -> str:
    """返回节点不能加入卸载目标时的说明。"""
    if node.is_locked:
        return "节点已锁定"
    if node.status != "online":
        return "SSH 非在线"
    if node.credential is None or not node.credential.is_enabled:
        return "无可用凭证"
    if node.nginx_available is not True:
        return "未检测到 Nginx"
    if node.id in active_ids:
        return "卸载任务进行中"
    return ""


def _page_context(query, session: Session, page: int, per_page: int) -> dict:
    """执行历史列表分页并构造公共分页上下文。"""
    total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    pages = max(1, ceil(total / per_page))
    current = min(page, pages)
    return {
        "items": session.scalars(query.limit(per_page).offset((current - 1) * per_page)).all(),
        "pagination": {
            "page": current,
            "pages": pages,
            "total": total,
            "per_page": per_page,
            "per_page_options": _PAGE_SIZE_OPTIONS,
        },
    }


def _search_terms(value: str) -> List[str]:
    """拆分历史筛选中的逗号分隔关键词。"""
    return [term.strip()[:100] for term in re.split(r"[,，]", value or "") if term.strip()]


def _operator_names(session: Session, runs: List[NginxUninstallRun]) -> dict:
    """批量读取卸载任务的操作人名称。"""
    user_ids = {
        run.task.trigger_user_id
        for run in runs
        if run.task and run.task.trigger_user_id is not None
    }
    if not user_ids:
        return {}
    return dict(
        session.execute(
            select(User.id, User.username).where(User.id.in_(user_ids))
        ).all()
    )


def _read_task(
    task_id: int,
    after_log_id: int,
    session: Session,
    owner_user_id: Optional[int] = None,
) -> Optional[dict]:
    """读取任务状态、结果树和游标之后的日志。"""
    task = session.get(Task, task_id)
    if task is None or task.operation_type != "nginx_uninstall":
        return None
    run = session.scalar(
        select(NginxUninstallRun).where(NginxUninstallRun.task_id == task_id)
    )
    task_ids = [task_id]
    if run is not None:
        task_query = (
            select(NginxUninstallRun.task_id)
            .join(Task, Task.id == NginxUninstallRun.task_id)
            .where(
                NginxUninstallRun.batch_number == run.batch_number,
                Task.operation_type == task.operation_type,
            )
        )
        if owner_user_id is not None:
            task_query = task_query.where(Task.trigger_user_id == owner_user_id)
        task_ids = sorted(set(session.scalars(task_query).all()) | {task_id})
    batch_active = bool(
        session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.id.in_(task_ids), Task.status.in_(("pending", "running")))
        )
    )
    has_detail_logs = session.scalar(
        select(func.count())
        .select_from(NginxOperationLog)
        .where(
            NginxOperationLog.operation_type == "nginx_uninstall",
            NginxOperationLog.task_id.in_(task_ids),
        )
    )
    log_model = NginxOperationLog if has_detail_logs or batch_active else TaskLog
    query = select(log_model).where(
        log_model.task_id.in_(task_ids), log_model.id > after_log_id
    )
    if log_model is NginxOperationLog:
        query = query.where(log_model.operation_type == "nginx_uninstall")
    rows = session.scalars(query.order_by(log_model.id.asc()).limit(201)).all()
    has_more = len(rows) > 200
    logs = rows[:200]
    try:
        result_tree = json.loads(task.result_tree_json) if task.result_tree_json else None
    except (TypeError, ValueError):
        result_tree = None
    return {
        "success": True,
        "id": task.id,
        "status": task.status,
        "detail": task.detail,
        "progress": task.progress,
        "result_tree": result_tree,
        "logs": [
            {
                "id": row.id,
                "task_id": row.task_id,
                "node_id": getattr(row, "node_id", None),
                "level": row.level,
                "message": row.message,
                "created_at": row.created_at.isoformat() if row.created_at else "",
            }
            for row in logs
        ],
        "next_log_id": logs[-1].id if logs else after_log_id,
        "has_more_logs": has_more,
        "batch_active": batch_active,
    }


@router.get("/nginx/uninstall/", include_in_schema=False)
def nginx_uninstall_home(
    request: Request,
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> Response:
    """显示 Nginx 卸载统计、最近任务和向导入口。"""
    if not _has_permission(request, session, user, "read"):
        raise PermissionDenied("无访问权限", "当前账号没有查看 Nginx 卸载的权限。")
    since = func.datetime("now", "-7 days")
    recent_query = (
        select(NginxUninstallRun)
        .join(Task, Task.id == NginxUninstallRun.task_id)
        .options(joinedload(NginxUninstallRun.task))
        .order_by(NginxUninstallRun.created_at.desc(), NginxUninstallRun.id.desc())
    )
    recent_runs = session.scalars(
        recent_query.limit(read_setting(session, "dashboard.recent_tasks_count", 20))
    ).all()
    running_count = int(
        session.scalar(
            select(func.count()).select_from(Task).where(
                Task.operation_type == "nginx_uninstall", Task.status.in_(("pending", "running"))
            )
        ) or 0
    )
    success_count = int(
        session.scalar(
            select(func.count()).select_from(Task).where(
                Task.operation_type == "nginx_uninstall", Task.status == "success", Task.created_at >= since
            )
        ) or 0
    )
    failed_count = int(
        session.scalar(
            select(func.count()).select_from(Task).where(
                Task.operation_type == "nginx_uninstall", Task.status == "failed", Task.created_at >= since
            )
        ) or 0
    )
    total_count = int(
        session.scalar(
            select(func.count()).select_from(Task).where(
                Task.operation_type == "nginx_uninstall"
            )
        )
        or 0
    )
    return render_page(
        request,
        "nginx_uninstall/index.html",
        {
            "recent_runs": recent_runs,
            "operator_names": _operator_names(session, recent_runs),
            "running_count": running_count,
            "success_7d_count": success_count,
            "failed_7d_count": failed_count,
            "total_count": total_count,
            "can_execute": _has_permission(request, session, user, "execute"),
        },
        user,
        session,
    )


@router.get("/nginx/uninstall/center/", include_in_schema=False)
def nginx_uninstall_center(
    request: Request,
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> Response:
    """渲染 Nginx 卸载三步向导和近期任务。"""
    if not _has_permission(request, session, user, "read"):
        raise PermissionDenied("无访问权限", "当前账号没有查看 Nginx 卸载的权限。")
    active_node_ids = set(
        session.scalars(
            select(NginxUninstallRun.node_id)
            .join(Task, Task.id == NginxUninstallRun.task_id)
            .where(Task.status.in_(("pending", "running")))
        ).all()
    )
    candidate_nodes = session.scalars(
        select(Node)
        .options(joinedload(Node.credential), selectinload(Node.groups))
        .where(
            Node.is_deleted.is_(False),
            Node.is_locked.is_(False),
            Node.status == "online",
            Node.nginx_available.is_(True),
        )
        .order_by(Node.hostname.asc(), Node.id.asc())
    ).unique().all()
    nodes = [
        node for node in candidate_nodes
        if node.credential is not None
        and node.credential.is_enabled
        and node.id not in active_node_ids
    ]
    return render_page(
        request,
        "nginx_uninstall/center.html",
        {
            "nodes": nodes,
            "batch_max_count": _batch_limit(session),
            "can_execute": _has_permission(request, session, user, "execute"),
        },
        user,
        session,
    )


@router.get("/nginx/uninstall/history/", include_in_schema=False)
def nginx_uninstall_history(
    request: Request,
    search: str = Query("", max_length=300),
    status: str = Query("", max_length=20),
    page: int = Query(1, ge=1),
    per_page: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=50),
    user: User = Depends(require_permission("nginx_uninstall", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """按节点、路径、批次和状态筛选卸载历史。"""
    query = (
        select(NginxUninstallRun)
        .join(Task, Task.id == NginxUninstallRun.task_id)
        .outerjoin(User, User.id == Task.trigger_user_id)
        .options(joinedload(NginxUninstallRun.task))
        .order_by(NginxUninstallRun.created_at.desc(), NginxUninstallRun.id.desc())
    )
    if status == "running":
        query = query.where(Task.status.in_(("pending", "running")))
    elif status in _STATUS_LABELS:
        query = query.where(Task.status == status)
    for term in _search_terms(search):
        pattern = "%{}%".format(term)
        query = query.where(
            or_(
                NginxUninstallRun.node_hostname.ilike(pattern),
                NginxUninstallRun.node_ip.ilike(pattern),
                NginxUninstallRun.resolved_prefix.ilike(pattern),
                NginxUninstallRun.package_name.ilike(pattern),
                NginxUninstallRun.batch_number.ilike(pattern),
                User.username.ilike(pattern),
            )
        )
    result = _page_context(query, session, page, per_page)
    runs = result.pop("items")
    for run in runs:
        try:
            run.display_options = json.loads(run.options_json or "{}")
        except (TypeError, ValueError):
            run.display_options = {}
    result.update(
        {
            "runs": runs,
            "operator_names": _operator_names(session, runs),
            "search": search,
            "status_filter": status,
            "status_choices": [("", "全部状态"), ("running", "进行中")]
            + [(key, value) for key, value in _STATUS_LABELS.items() if key not in ("pending", "running")],
        }
    )
    return render_page(request, "nginx_uninstall/history.html", result, user, session)


@router.get("/nginx/uninstall/task/{task_id}/log/", include_in_schema=False)
def nginx_uninstall_task_log(
    request: Request,
    task_id: int = Path(..., ge=1),
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> Response:
    """显示卸载任务参数、结果树和完整日志。"""
    run = session.scalar(
        select(NginxUninstallRun)
        .options(joinedload(NginxUninstallRun.task))
        .where(NginxUninstallRun.task_id == task_id)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="卸载任务不存在")
    can_read = _has_permission(request, session, user, "read")
    can_execute = _has_permission(request, session, user, "execute")
    if not can_read and not (can_execute and run.task.trigger_user_id == user.id):
        raise PermissionDenied("无访问权限", "当前账号没有查看该卸载任务的权限。")
    batch_query = (
        select(NginxUninstallRun)
        .join(Task, Task.id == NginxUninstallRun.task_id)
        .options(joinedload(NginxUninstallRun.task), joinedload(NginxUninstallRun.node))
        .where(
            NginxUninstallRun.batch_number == run.batch_number,
            Task.operation_type == run.task.operation_type,
        )
        .order_by(NginxUninstallRun.task_id.asc())
    )
    if not can_read:
        batch_query = batch_query.where(Task.trigger_user_id == user.id)
    batch_runs = session.scalars(batch_query).all()
    batch_task_ids = [item.task_id for item in batch_runs]
    batch_active = any(
        item.task.status in ("pending", "running") for item in batch_runs
    )
    logs = session.scalars(
        select(NginxOperationLog)
        .where(
            NginxOperationLog.operation_type == "nginx_uninstall",
            NginxOperationLog.task_id.in_(batch_task_ids),
        )
        .order_by(NginxOperationLog.id.asc())
    ).all()
    if not logs and not batch_active:
        logs = session.scalars(
            select(TaskLog)
            .where(TaskLog.task_id.in_(batch_task_ids))
            .order_by(TaskLog.id.asc())
        ).all()
    try:
        result_tree = json.loads(run.task.result_tree_json) if run.task.result_tree_json else None
    except (TypeError, ValueError):
        result_tree = None
    batch_summary = summarize_lifecycle_runs(batch_runs)
    batch_details = []
    for batch_run in batch_runs:
        try:
            item_options = json.loads(batch_run.options_json or "{}")
        except (TypeError, ValueError):
            item_options = {}
        try:
            item_tree = json.loads(batch_run.task.result_tree_json) if batch_run.task.result_tree_json else {}
        except (TypeError, ValueError):
            item_tree = {}
        result_nodes = item_tree.get("nodes", []) if isinstance(item_tree, dict) else []
        node_result = result_nodes[0] if result_nodes and isinstance(result_nodes[0], dict) else {}
        deletion_paths = []
        if batch_run.install_origin == "source" and batch_run.resolved_prefix:
            deletion_paths.append(batch_run.resolved_prefix)
        deletion_paths.extend(
            entry.get("path", "")
            for entry in item_options.get("extra_paths", [])
            if isinstance(entry, dict) and entry.get("path")
        )
        if item_options.get("remove_backup") and item_options.get("backup_path"):
            deletion_paths.append(item_options["backup_path"])
        if item_options.get("remove_workdir") and item_options.get("work_dir"):
            deletion_paths.append(item_options["work_dir"])
        if item_options.get("remove_modules") and item_options.get("work_dir"):
            deletion_paths.append(item_options["work_dir"].rstrip("/") + "/nginx-modules")
        batch_details.append(
            {
                "run": batch_run,
                "options": item_options,
                "node_result": node_result,
                "deletion_paths": list(dict.fromkeys(deletion_paths)),
            }
        )
    primary_result = next(
        (item["node_result"] for item in batch_details if item["run"].task_id == run.task_id),
        {},
    )
    current_versions = []
    for item in batch_details:
        version = str(
            item["node_result"].get("version")
            or (item["run"].node.nginx_version if item["run"].node else "")
            or ""
        ).strip()
        if version and version not in current_versions:
            current_versions.append(version)
    current_version = ", ".join(current_versions) or "-"
    install_origins = []
    for item in batch_details:
        item_run = item["run"]
        origin = (
            "源码"
            if item_run.install_origin == "source"
            else {"rpm": "yum", "deb": "apt"}.get(item_run.package_manager, "包管理器")
        )
        if origin not in install_origins:
            install_origins.append(origin)
    install_origin = "、".join(install_origins) or "-"
    page_user = SimpleNamespace(
        id=user.id,
        username=user.username,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_staff=user.is_staff,
    )
    return render_page(
        request,
        "nginx_uninstall/task_log.html",
        {
            "run": run,
            "task": run.task,
            "batch_runs": batch_runs,
            "batch_details": batch_details,
            "batch_summary": batch_summary,
            "batch_active": batch_active,
            "logs": logs,
            "log_cursor": max((item.id for item in logs), default=0),
            "result_tree": result_tree,
            "primary_result": primary_result,
            "current_version": current_version,
            "install_origin": install_origin,
            "format_task_datetime": format_task_datetime,
            "status_label": _STATUS_LABELS.get(run.task.status, run.task.status),
            "can_cancel": can_execute and run.task.status in ("pending", "running"),
        },
        page_user,
        session,
    )


@router.get(
    "/api/nginx/uninstall/nodes",
    response_model=UninstallNodesResponse,
    summary="列出可筛选的 Nginx 卸载节点",
    description="返回节点 SSH/Nginx 状态和卸载选择门禁，不返回凭证明文。",
    responses=api_error_responses((401, 403, 422, 500)),
)
def list_uninstall_nodes(
    request: Request,
    search: str = Query("", max_length=200),
    user: User = Depends(require_permission("nginx_uninstall", "read")),
    session: Session = Depends(get_session),
) -> UninstallNodesResponse:
    """返回搜索匹配的活动节点和卸载门禁原因。"""
    query = (
        select(Node)
        .options(joinedload(Node.credential), selectinload(Node.groups))
        .where(
            Node.is_deleted.is_(False),
            Node.status == "online",
            Node.nginx_available.is_(True),
        )
        .order_by(Node.hostname.asc(), Node.id.asc())
    )
    term = search.strip()[:200]
    if term:
        pattern = "%{}%".format(term)
        query = query.where(or_(Node.hostname.ilike(pattern), Node.ip.ilike(pattern)))
    nodes = session.scalars(query.limit(500)).unique().all()
    active_ids = set(
        session.scalars(
            select(NginxUninstallRun.node_id)
            .join(Task, Task.id == NginxUninstallRun.task_id)
            .where(Task.status.in_(("pending", "running")))
        ).all()
    )
    items = []
    for node in nodes:
        reason = _node_disabled_reason(node, active_ids)
        items.append(
            UninstallNodeItem(
                id=node.id,
                hostname=node.hostname,
                ip=node.ip,
                port=node.port,
                groups=[group.name for group in node.groups],
                nginx_path=node.nginx_path or "",
                nginx_version=node.nginx_version or "",
                nginx_available=node.nginx_available,
                status=node.status,
                can_select=not reason,
                disabled_reason=reason,
            )
        )
    return UninstallNodesResponse(
        nodes=items,
        total=len(items),
        batch_max_count=_batch_limit(session),
    )


@router.post(
    "/api/nginx/uninstall/preview",
    response_model=PreviewResponse,
    summary="预览 Nginx 卸载路径和安装来源",
    description="探测 nginx -V、rpm/dpkg 包归属、systemd 托管、运行态及可选清理目录。",
    responses=api_error_responses((400, 401, 403, 422, 500)),
)
def preview_uninstall(
    request: Request,
    payload: PreviewRequest,
    user: User = Depends(require_permission("nginx_uninstall", "execute")),
    session: Session = Depends(get_session),
) -> PreviewResponse:
    """并发探测目标节点并返回逐节点的删除范围。"""
    try:
        result = preview_nodes(
            request.app.state.database.session_factory,
            request.app.state.credential_encryption_key,
            payload.node_ids,
            _batch_limit(session),
            read_setting(session, "upgrade.default_work_dir", "/tmp/nginx-upgrade"),
            read_setting(
                session,
                "release.backup_dir",
                "/opt/ngxops/backups",
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PreviewResponse(**result)


@router.post(
    "/api/nginx/uninstall/tasks",
    status_code=202,
    response_model=UninstallCreateResponse,
    summary="创建 Nginx 卸载批次",
    description="根据确认后的逐节点路径选择创建统一异步任务；包安装仅调用包管理器 remove。",
    responses=api_error_responses((400, 401, 403, 409, 422, 500)),
)
def create_uninstall_tasks(
    request: Request,
    payload: UninstallCreateRequest,
    user: User = Depends(require_permission("nginx_uninstall", "execute")),
    session: Session = Depends(get_session),
) -> UninstallCreateResponse:
    """校验卸载目标和路径后创建异步任务。"""
    batch_limit = _batch_limit(session)
    if len(payload.nodes) > batch_limit:
        raise HTTPException(status_code=400, detail="单次最多选择 {} 台节点".format(batch_limit))
    session.rollback()
    try:
        items = [item.model_dump(exclude_none=True) for item in payload.nodes]
        result = create_uninstall_batch(
            request.app.state.database.session_factory,
            request.app.state.task_executor,
            request.app.state.credential_encryption_key,
            user.id,
            items,
            batch_limit=batch_limit,
            trigger_ip=request_client_ip(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UninstallCreateResponse(
        batch_number=result["batch_number"],
        task_ids=result["task_ids"],
        skipped=result["skipped"],
        message="已创建 {} 个卸载任务，批次 {}".format(len(result["task_ids"]), result["batch_number"]),
    )


@router.get(
    "/api/nginx/uninstall/batches/{batch_number}",
    response_model=UninstallBatchResponse,
    summary="读取 Nginx 卸载批次进度",
    description="返回每个节点任务持久化的状态、步骤和真实进度。",
    responses=api_error_responses((401, 403, 404, 422, 500)),
)
def get_uninstall_batch(
    request: Request,
    batch_number: str = Path(..., min_length=14, max_length=32),
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> UninstallBatchResponse:
    """读取批次进度并限制执行权限用户只能查看本人批次。"""
    can_read = _has_permission(request, session, user, "read")
    can_execute = _has_permission(request, session, user, "execute")
    if not can_read and not can_execute:
        raise PermissionDenied("无访问权限", "当前账号没有查看卸载进度的权限。")
    rows = load_uninstall_batch(request.app.state.database.session_factory, batch_number)
    if not can_read:
        task_ids = {row["task_id"] for row in rows}
        owned = set(
            session.scalars(
                select(Task.id).where(Task.id.in_(task_ids), Task.trigger_user_id == user.id)
            ).all()
        ) if task_ids else set()
        rows = [row for row in rows if row["task_id"] in owned]
    if not rows:
        raise HTTPException(status_code=404, detail="卸载批次不存在")
    total = len(rows)
    finished = sum(1 for row in rows if row["finished"])
    success = sum(1 for row in rows if row["status"] == "success")
    failed = sum(1 for row in rows if row["status"] in ("failed", "cancelled"))
    progress = int(sum(row["progress"] for row in rows) / total) if total else 0
    return UninstallBatchResponse(
        batch_number=batch_number,
        tasks=rows,
        finished=finished == total,
        total=total,
        success_count=success,
        fail_count=failed,
        progress=progress,
    )


@router.get(
    "/api/nginx/uninstall/tasks/{task_id}",
    response_model=UninstallTaskDetailResponse,
    summary="读取 Nginx 卸载任务详情和日志",
    description="返回脱敏结果树和游标之后的追加式任务日志。",
    responses=api_error_responses((401, 403, 404, 422, 500)),
)
def get_uninstall_task_detail(
    request: Request,
    task_id: int = Path(..., ge=1),
    after_log_id: int = Query(0, ge=0),
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> UninstallTaskDetailResponse:
    """读取任务详情并按卸载查看权限限制访问范围。"""
    task = session.get(Task, task_id)
    if task is None or task.operation_type != "nginx_uninstall":
        raise HTTPException(status_code=404, detail="卸载任务不存在")
    can_read = _has_permission(request, session, user, "read")
    can_execute = _has_permission(request, session, user, "execute")
    if not can_read and not (can_execute and task.trigger_user_id == user.id):
        raise PermissionDenied("无访问权限", "当前账号没有查看该卸载任务的权限。")
    detail = _read_task(
        task_id,
        after_log_id,
        session,
        owner_user_id=None if can_read else user.id,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="卸载任务不存在")
    return UninstallTaskDetailResponse(**detail)
