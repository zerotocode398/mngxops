"""提供 Nginx 启停操作台、批次历史和受保护的 JSON API。"""

import json
import re
from datetime import datetime
from math import ceil
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path as ApiPath, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from ngxops.accounts.models import User
from ngxops.api.contracts import api_error_responses
from ngxops.database.session import get_session
from ngxops.nginx_service.services import (
    ACTION_LABELS,
    create_service_task,
)
from ngxops.nodes.models import Node, NodeGroup
from ngxops.security.dependencies import require_permission
from ngxops.settings.service import read_setting
from ngxops.tasks.models import Task, TaskLog
from ngxops.ui import render_page


router = APIRouter(tags=["nginx_service"])
_BATCH_MAX_COUNT = 3
_PAGE_SIZES = (10, 15, 30, 50)
_DEFAULT_PAGE_SIZE = 15
_STATUS_LABELS = {
    "pending": "等待执行",
    "running": "执行中",
    "success": "成功",
    "failed": "失败",
    "cancelled": "已取消",
}
_STATUS_CHOICES = (
    ("", "全部状态"),
    ("running", "进行中"),
    ("success", "成功"),
    ("failed", "失败"),
    ("cancelled", "已取消"),
)


class ServiceNodeItem(BaseModel):
    """描述启停节点选择器可见的非敏感信息和门禁结果。"""

    id: int
    hostname: str
    ip: str
    port: int
    status: str
    locked: bool
    nginx_available: Optional[bool]
    nginx_version: str
    groups: List[str]
    has_credential: bool
    credential_username: str
    disabled_reason: str
    can_select: bool


class ServiceNodeListResponse(BaseModel):
    """描述启停节点选择器的筛选结果。"""

    success: bool = True
    data: List[ServiceNodeItem]
    total: int
    batch_max_count: int


class ServiceExecuteRequest(BaseModel):
    """描述一次启停操作和其目标节点。"""

    action: Literal["start", "stop", "reload", "restart"]
    node_ids: List[int] = Field(..., min_length=1, max_length=50)


class SkippedServiceNode(BaseModel):
    """描述因状态门禁未加入启停批次的节点。"""

    id: int
    hostname: str
    ip: str
    reason: str


class ServiceExecuteResponse(BaseModel):
    """描述已创建的启停任务及跳过的节点。"""

    success: bool = True
    message: str
    task_id: int
    batch_number: str
    skipped: List[SkippedServiceNode]


class ServiceTaskLogItem(BaseModel):
    """描述启停任务的一条增量日志。"""

    id: int
    level: str
    message: str
    created_at: datetime


class ServiceTaskProgressResponse(BaseModel):
    """描述启停任务当前状态、逐节点结果和增量日志。"""

    success: bool = True
    task_id: int
    status: Literal["pending", "running", "success", "failed", "cancelled"]
    progress: int
    detail: str
    action: str
    action_label: str
    batch_number: str
    result_tree: Any = None
    logs: List[ServiceTaskLogItem]
    next_log_id: int
    has_more_logs: bool


def _search_terms(value: str) -> List[str]:
    """按逗号拆分节点和启停历史的 AND 搜索词。"""
    return [term.strip()[:100] for term in re.split(r"[,，]", value or "") if term.strip()]


def _disabled_reason(node: Node) -> str:
    """返回节点不满足启停操作门禁时的首个原因。"""
    if node.is_locked:
        return "节点已锁定"
    if node.status != "online":
        return "SSH 非在线状态"
    if node.nginx_available is not True:
        return "未检测到可用的 Nginx"
    credential = node.credential
    if credential is None or not credential.is_enabled:
        return "没有可用的 SSH 凭证"
    return ""


def _node_item(node: Node) -> ServiceNodeItem:
    """将节点 ORM 对象转换为不含凭证明文的选择器项。"""
    credential = node.credential
    reason = _disabled_reason(node)
    return ServiceNodeItem(
        id=node.id,
        hostname=node.hostname,
        ip=node.ip,
        port=node.port,
        status=node.status,
        locked=node.is_locked,
        nginx_available=node.nginx_available,
        nginx_version=node.nginx_version,
        groups=[group.name for group in node.groups],
        has_credential=bool(credential and credential.is_enabled),
        credential_username=credential.username if credential else "",
        disabled_reason=reason,
        can_select=not reason,
    )


def _history_query(search: str, status: str):
    """构造启停任务历史的状态和关键词查询。"""
    query = select(Task).where(Task.operation_type == "nginx_service_control")
    if status == "running":
        query = query.where(Task.status.in__(("pending", "running")))
    elif status in _STATUS_LABELS:
        query = query.where(Task.status == status)
    for term in _search_terms(search):
        pattern = "%{}%".format(term)
        query = query.where(
            or_(
                Task.target_hostnames.ilike(pattern),
                Task.target_ips.ilike(pattern),
                Task.target_configs.ilike(pattern),
                Task.detail.ilike(pattern),
                Task.source_batch.ilike(pattern),
            )
        )
    return query


def _page_items(session: Session, query, page: int, per_page: int) -> dict:
    """执行分页查询并生成全局分页组件上下文。"""
    total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    pages = max(1, int(ceil(total / float(per_page))))
    current_page = min(page, pages)
    items = session.scalars(
        query.order_by(Task.created_at.desc(), Task.id.desc())
        .offset((current_page - 1) * per_page)
        .limit(per_page)
    ).all()
    return {
        "items": items,
        "pagination": {
            "page": current_page,
            "pages": pages,
            "total": total,
            "per_page": per_page,
            "per_page_options": _PAGE_SIZES,
        },
    }


def _operator_names(session: Session, tasks: List[Task]) -> dict:
    """批量读取历史任务操作人名称。"""
    user_ids = {task.trigger_user_id for task in tasks if task.trigger_user_id}
    if not user_ids:
        return {}
    return dict(session.execute(select(User.id, User.username).where(User.id.in_(user_ids))).all())


def _read_result_tree(value: Optional[str]) -> Any:
    """读取任务结果树并容忍历史无效 JSON。"""
    try:
        return json.loads(value) if value else None
    except (TypeError, ValueError):
        return None


@router.get("/nginx/service/", include_in_schema=False)
def nginx_service_home(
    request: Request,
    user: User = Depends(require_permission("nginx_service", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """渲染启停操作台及最近任务。"""
    recent = session.scalars(
        select(Task)
        .where(Task.operation_type == "nginx_service_control")
        .order_by(Task.created_at.desc(), Task.id.desc())
        .limit(read_setting(session, "dashboard.recent_tasks_count", 20))
    ).all()
    active_task = next((task for task in recent if task.status in ("pending", "running")), None)
    return render_page(
        request,
        "nginx_service/index.html",
        {
            "batch_max_count": read_setting(session, "node.batch_max_count", _BATCH_MAX_COUNT),
            "can_operate": request.app.state.permission_checker(
                session, user, "nginx_service", "operate"
            ),
            "recent_tasks": recent,
            "operator_names": _operator_names(session, recent),
            "active_task": active_task,
            "action_labels": ACTION_LABELS,
            "status_labels": _STATUS_LABELS,
            "status_classes": {
                "pending": "secondary",
                "running": "info",
                "success": "success",
                "failed": "danger",
                "cancelled": "secondary",
            },
        },
        user,
        session,
    )


@router.get("/nginx/service/history/", include_in_schema=False)
def nginx_service_history(
    request: Request,
    search: str = Query("", max_length=300),
    status: str = Query("", max_length=20),
    page: int = Query(1, ge=1),
    per_page: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=50),
    user: User = Depends(require_permission("nginx_service", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """渲染支持搜索、状态筛选和分页的启停历史。"""
    result = _page_items(session, _history_query(search, status), page, per_page)
    tasks = result.pop("items")
    result.update(
        {
            "tasks": tasks,
            "operator_names": _operator_names(session, tasks),
            "action_labels": ACTION_LABELS,
            "status_labels": _STATUS_LABELS,
            "status_classes": {
                "pending": "secondary",
                "running": "info",
                "success": "success",
                "failed": "danger",
                "cancelled": "secondary",
            },
            "search": search,
            "status_filter": status,
            "status_choices": _STATUS_CHOICES,
        }
    )
    return render_page(request, "nginx_service/history.html", result, user, session)


@router.get("/nginx/service/task/{task_id}/log/", include_in_schema=False)
def nginx_service_task_log(
    request: Request,
    task_id: int = ApiPath(..., ge=1),
    user: User = Depends(require_permission("nginx_service", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """渲染单批启停任务信息和完整日志。"""
    task = session.scalar(
        select(Task).where(
            Task.id == task_id,
            Task.operation_type == "nginx_service_control",
        )
    )
    if task is None:
        raise HTTPException(status_code=404, detail="启停任务不存在")
    logs = session.scalars(
        select(TaskLog).where(TaskLog.task_id == task.id).order_by(TaskLog.id.asc())
    ).all()
    result_tree = _read_result_tree(task.result_tree_json)
    targets = []
    names = [part.strip() for part in task.target_hostnames.split(",") if part.strip()]
    ips = [part.strip() for part in task.target_ips.split(",") if part.strip()]
    for index, hostname in enumerate(names):
        ip = ips[index] if index < len(ips) else ""
        targets.append("{} ({})".format(hostname, ip) if ip else hostname)
    return render_page(
        request,
        "nginx_service/task_log.html",
        {
            "task": task,
            "action_label": ACTION_LABELS.get(task.target_configs, task.target_configs),
            "status_label": _STATUS_LABELS.get(task.status, task.status),
            "status_class": {
                "pending": "secondary",
                "running": "info",
                "success": "success",
                "failed": "danger",
                "cancelled": "secondary",
            }.get(task.status, "secondary"),
            "can_view_task_center": user.is_superuser
            or request.app.state.permission_checker(
                session, user, "nginx_service", "operate"
            ),
            "targets": targets,
            "result_tree": result_tree,
            "logs": logs,
            "log_cursor": max((entry.id for entry in logs), default=0),
        },
        user,
        session,
    )


@router.get(
    "/api/nginx/service/nodes",
    response_model=ServiceNodeListResponse,
    summary="查询启停节点选择器数据",
    description="返回节点、Nginx 状态、分组和启停门禁原因，不返回 SSH 凭证明文。",
    responses=api_error_responses((401, 403, 422, 500)),
)
def list_service_nodes(
    search: str = Query("", max_length=200),
    user: User = Depends(require_permission("nginx_service", "read")),
    session: Session = Depends(get_session),
) -> ServiceNodeListResponse:
    """返回按主机名、IP 或节点组搜索的启停节点。"""
    query = (
        select(Node)
        .options(joinedload(Node.credential), joinedload(Node.groups))
        .where(Node.is_deleted.is_(False))
        .order_by(Node.hostname.asc(), Node.id.asc())
    )
    for term in _search_terms(search):
        pattern = "%{}%".format(term)
        query = query.where(
            or_(
                Node.hostname.ilike(pattern),
                Node.ip.ilike(pattern),
                Node.groups.any(NodeGroup.name.ilike(pattern)),
            )
        )
    nodes = session.scalars(query.limit(500)).unique().all()
    total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    return ServiceNodeListResponse(
        data=[_node_item(node) for node in nodes],
        total=total,
        batch_max_count=read_setting(session, "node.batch_max_count", _BATCH_MAX_COUNT),
    )


@router.post(
    "/api/nginx/service/execute",
    response_model=ServiceExecuteResponse,
    summary="创建 Nginx 启停批次",
    description="检查在线节点、凭证和 Nginx 可用状态，随后将符合条件的节点交给统一任务执行器。",
    responses=api_error_responses((400, 401, 403, 422, 500)),
)
def execute_service_action(
    payload: ServiceExecuteRequest,
    request: Request,
    user: User = Depends(require_permission("nginx_service", "operate")),
    session: Session = Depends(get_session),
) -> ServiceExecuteResponse:
    """校验目标节点后异步创建启停任务。"""
    batch_limit = read_setting(session, "node.batch_max_count", _BATCH_MAX_COUNT)
    if len(payload.node_ids) > batch_limit:
        raise HTTPException(status_code=400, detail="单次最多选择 {} 台节点".format(batch_limit))
    if len(set(payload.node_ids)) != len(payload.node_ids):
        raise HTTPException(status_code=400, detail="节点 ID 不能重复")
    nodes = session.scalars(
        select(Node)
        .options(joinedload(Node.credential))
        .where(Node.id.in_(payload.node_ids), Node.is_deleted.is_(False))
        .order_by(Node.id.asc())
    ).unique().all()
    node_by_id = {node.id: node for node in nodes}
    eligible = []
    skipped = []
    for node_id in payload.node_ids:
        node = node_by_id.get(node_id)
        if node is None:
            skipped.append(
                SkippedServiceNode(
                    id=node_id,
                    hostname="",
                    ip="",
                    reason="节点不存在或已删除",
                )
            )
            continue
        reason = _disabled_reason(node)
        if reason:
            skipped.append(
                SkippedServiceNode(
                    id=node.id,
                    hostname=node.hostname,
                    ip=node.ip,
                    reason=reason,
                )
            )
        else:
            eligible.append(node)
    if not eligible:
        reasons = [
            "{}：{}".format(node.hostname or node.id, node.reason)
            for node in skipped[:5]
        ]
        detail = "没有可执行启停操作的节点"
        if reasons:
            detail += "：" + "；".join(reasons)
        raise HTTPException(status_code=400, detail=detail)

    eligible_ids = [node.id for node in eligible]
    eligible_hostnames = [node.hostname for node in eligible]
    eligible_ips = [node.ip for node in eligible]
    session.rollback()
    task_id, batch_number = create_service_task(
        request.app.state.database.session_factory,
        request.app.state.task_executor,
        encryption_key=request.app.state.credential_encryption_key,
        node_ids=eligible_ids,
        hostnames=eligible_hostnames,
        ips=eligible_ips,
        action=payload.action,
        trigger_user_id=user.id,
    )
    message = "已创建 Nginx {} 任务（{} 台）".format(
        ACTION_LABELS[payload.action], len(eligible)
    )
    if skipped:
        message += "；已跳过 {} 台".format(len(skipped))
    return ServiceExecuteResponse(
        message=message,
        task_id=task_id,
        batch_number=batch_number,
        skipped=skipped,
    )


@router.get(
    "/api/nginx/service/tasks/{task_id}",
    response_model=ServiceTaskProgressResponse,
    summary="读取 Nginx 启停进度和日志",
    description="返回逐节点任务结果和按日志 ID 增量读取的执行日志。",
    responses=api_error_responses((401, 403, 404, 422, 500)),
)
def get_service_task_progress(
    task_id: int = ApiPath(..., ge=1),
    after_log_id: int = Query(0, ge=0),
    log_limit: int = Query(200, ge=1, le=500),
    user: User = Depends(require_permission("nginx_service", "read")),
    session: Session = Depends(get_session),
) -> ServiceTaskProgressResponse:
    """返回单个启停任务的状态、结果树和增量日志。"""
    task = session.scalar(
        select(Task).where(
            Task.id == task_id,
            Task.operation_type == "nginx_service_control",
        )
    )
    if task is None:
        raise HTTPException(status_code=404, detail="启停任务不存在")
    rows = session.scalars(
        select(TaskLog)
        .where(TaskLog.task_id == task_id, TaskLog.id > after_log_id)
        .order_by(TaskLog.id.asc())
        .limit(log_limit + 1)
    ).all()
    selected_logs = rows[:log_limit]
    return ServiceTaskProgressResponse(
        task_id=task.id,
        status=task.status,
        progress=task.progress,
        detail=task.detail,
        action=task.target_configs,
        action_label=ACTION_LABELS.get(task.target_configs, task.target_configs),
        batch_number=task.source_batch,
        result_tree=_read_result_tree(task.result_tree_json),
        logs=[
            ServiceTaskLogItem(
                id=row.id,
                level=row.level,
                message=row.message,
                created_at=row.created_at,
            )
            for row in selected_logs
        ],
        next_log_id=selected_logs[-1].id if selected_logs else after_log_id,
        has_more_logs=len(rows) > log_limit,
    )
