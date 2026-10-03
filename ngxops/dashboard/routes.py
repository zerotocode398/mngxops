"""提供首页统计、最近任务和统计轮询接口。"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ngxops.accounts.models import User
from ngxops.api.contracts import api_error_responses
from ngxops.configs.models import ConfigBinding
from ngxops.database.session import get_session
from ngxops.nodes.models import Node
from ngxops.security.dependencies import require_authenticated_user
from ngxops.settings.service import read_setting
from ngxops.tasks.api import _task_permissions
from ngxops.tasks.models import Task
from ngxops.tasks.presentation import (
    OPERATION_LABELS,
    STATUS_LABELS,
    format_task_summary,
)
from ngxops.ui import render_page


router = APIRouter(tags=["dashboard"])
_SUMMARY_BATCH_TYPES = frozenset(
    (
        "release_publish",
        "release_rollback",
        "nginx_upgrade",
        "nginx_rollback",
        "nginx_install",
        "nginx_service_control",
        "nginx_uninstall",
    )
)


class DashboardStatsResponse(BaseModel):
    """描述仪表盘统计卡片的轮询数据。"""

    success: bool = True
    node_count: int
    online_count: int
    offline_count: int
    unknown_count: int
    config_total: int
    config_synced: int
    pending_push_count: int
    config_orphaned: int
    config_failed: int
    config_marked_deleted: int
    task_7d_total: int
    task_7d_success: int
    failed_7d_count: int
    running_count: int


def _has_permission(
    request: Request,
    session: Session,
    user: User,
    resource: str,
    action: str,
) -> bool:
    """通过应用注册的 RBAC 解析器判断单项权限。"""
    if user.is_superuser:
        return True
    checker = getattr(request.app.state, "permission_checker", None)
    return bool(checker and checker(session, user, resource, action))


def _task_filters(
    request: Request,
    session: Session,
    user: User,
) -> Tuple[List[Any], bool]:
    """返回首页任务统计与列表共用的可见范围条件。"""
    can_read_all, allowed_operations, can_access = _task_permissions(
        request,
        session,
        user,
        include_poll_permissions=True,
    )
    if can_read_all:
        return [], can_access
    return [
        Task.operation_type.in_(allowed_operations),
        Task.trigger_user_id == user.id,
    ], can_access


def _dashboard_stats(
    request: Request,
    session: Session,
    user: User,
    task_filters: Optional[List[Any]] = None,
) -> Dict[str, int]:
    """按活动资产和当前用户可见任务范围汇总首页数字。"""
    node_statuses = dict(
        session.execute(
            select(Node.status, func.count(Node.id))
            .where(Node.is_deleted.is_(False))
            .group_by(Node.status)
        ).all()
    )
    config_statuses = dict(
        session.execute(
            select(ConfigBinding.sync_status, func.count(ConfigBinding.id))
            .join(Node, Node.id == ConfigBinding.node_id)
            .where(Node.is_deleted.is_(False))
            .group_by(ConfigBinding.sync_status)
        ).all()
    )
    if task_filters is None:
        task_filters, _can_access_task_center = _task_filters(
            request, session, user
        )
    task_statuses = dict(
        session.execute(
            select(Task.status, func.count(Task.id))
            .where(*task_filters)
            .group_by(Task.status)
        ).all()
    )
    since = datetime.utcnow() - timedelta(days=7)
    recent_task_statuses = dict(
        session.execute(
            select(Task.status, func.count(Task.id))
            .where(*task_filters, Task.created_at >= since)
            .group_by(Task.status)
        ).all()
    )
    return {
        "node_count": sum(node_statuses.values()),
        "online_count": node_statuses.get("online", 0),
        "offline_count": node_statuses.get("offline", 0),
        "unknown_count": node_statuses.get("unknown", 0),
        "config_total": sum(config_statuses.values()),
        "config_synced": config_statuses.get("synced", 0),
        "pending_push_count": config_statuses.get("not_synced", 0)
        + config_statuses.get("modified", 0),
        "config_orphaned": config_statuses.get("orphaned", 0),
        "config_failed": config_statuses.get("failed", 0),
        "config_marked_deleted": config_statuses.get("marked_deleted", 0),
        "task_7d_total": sum(recent_task_statuses.values()),
        "task_7d_success": recent_task_statuses.get("success", 0),
        "failed_7d_count": recent_task_statuses.get("failed", 0),
        "running_count": task_statuses.get("running", 0),
    }


def _recent_task_rows(
    session: Session,
    task_filters: List[Any],
    can_access: bool,
    limit: int,
) -> List[Dict[str, Any]]:
    """整理有权查看的近期任务摘要和操作人名称。"""
    if not can_access:
        return []
    tasks = session.scalars(
        select(Task)
        .where(*task_filters)
        .order_by(Task.created_at.desc(), Task.id.desc())
        .limit(limit)
    ).all()
    user_ids = {task.trigger_user_id for task in tasks if task.trigger_user_id}
    usernames = {}
    if user_ids:
        usernames = dict(
            session.execute(
                select(User.id, User.username).where(User.id.in_(user_ids))
            ).all()
        )
    rows = []
    for task in tasks:
        primary, secondary = format_task_summary(task)
        rows.append(
            {
                "task": task,
                "operation_label": OPERATION_LABELS.get(
                    task.operation_type, task.operation_type
                ),
                "status_label": STATUS_LABELS.get(task.status, task.status),
                "summary_primary": primary,
                "summary_secondary": secondary,
                "summary_is_batch": task.operation_type in _SUMMARY_BATCH_TYPES,
                "username": usernames.get(task.trigger_user_id, "-"),
            }
        )
    return rows


@router.get(
    "/",
    response_class=Response,
    summary="仪表盘首页",
    include_in_schema=False,
)
def dashboard_home(
    request: Request,
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> Response:
    """显示系统统计、权限快捷入口和最近任务。"""
    task_filters, can_access_task_center = _task_filters(request, session, user)
    stats = _dashboard_stats(request, session, user, task_filters)
    recent_limit = read_setting(session, "dashboard.recent_tasks_count", 20)
    recent_rows = _recent_task_rows(
        session,
        task_filters,
        can_access_task_center,
        recent_limit,
    )
    can_read_nodes = _has_permission(request, session, user, "nodes", "read")
    can_read_configs = _has_permission(request, session, user, "configs", "read")
    can_publish = (
        _has_permission(request, session, user, "releases", "read")
        or _has_permission(request, session, user, "releases", "publish")
    )
    refresh_interval = read_setting(
        session, "system.dashboard_refresh_interval", 30
    )
    return render_page(
        request,
        "dashboard/index.html",
        context={
            **stats,
            "recent_rows": recent_rows,
            "can_read_nodes": can_read_nodes,
            "can_read_configs": can_read_configs,
            "can_create_nodes": _has_permission(
                request, session, user, "nodes", "create"
            ),
            "can_publish": can_publish,
            "can_access_task_center": can_access_task_center,
            "dashboard_refresh_ms": refresh_interval * 1000,
        },
        user=user,
        db_session=session,
    )


@router.get(
    "/api/stats/",
    response_model=DashboardStatsResponse,
    summary="读取仪表盘统计",
    description="统计活动节点、配置绑定和当前会话可见范围内的任务。",
    responses=api_error_responses((401, 500)),
)
def dashboard_stats(
    request: Request,
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> DashboardStatsResponse:
    """返回供首页定时刷新的统计卡片数据。"""
    return DashboardStatsResponse(**_dashboard_stats(request, session, user))
