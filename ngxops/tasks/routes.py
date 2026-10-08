"""提供统一任务中心页面和受限任务查看范围。"""

import json
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from ngxops.accounts.models import User
from ngxops.database.session import get_session
from ngxops.security.dependencies import require_authenticated_user
from ngxops.security.errors import PermissionDenied
from ngxops.tasks.api import _task_permissions, _visible_task_query
from ngxops.tasks.models import OPERATION_TYPES, Task, TaskLog
from ngxops.tasks.presentation import (
    OPERATION_LABELS,
    RESULT_FIELD_LABELS,
    STATUS_LABELS,
    format_execution_duration,
    format_task_summary,
    task_search_filters,
)
from ngxops.ui import render_page


router = APIRouter(tags=["tasks"])
PAGE_SIZE_OPTIONS = (10, 15, 30, 50)
DEFAULT_PAGE_SIZE = 15
HIDDEN_OPERATION_TYPES = frozenset(
    ("config_drift_check", "config_glob_preview")
)


def _filtered_conditions(
    search: str,
    status: str,
    operation_type: str,
) -> list:
    """构造统一列表和 HTML 页面使用的任务筛选条件。"""
    conditions = task_search_filters(search)
    if status in STATUS_LABELS:
        conditions.append(Task.status == status)
    if operation_type in OPERATION_TYPES:
        conditions.append(Task.operation_type == operation_type)
    return conditions


def _can_cancel_task(
    task: Task,
    upgrade_phase: str = "",
    can_execute_upgrade: bool = False,
) -> bool:
    """按任务状态、升级阶段和当前权限决定是否展示取消操作。"""
    if task.status not in ("pending", "running"):
        return False
    if task.operation_type == "nginx_upgrade":
        return can_execute_upgrade and upgrade_phase in (
            "pending",
            "fetching_config",
            "uploading_package",
        )
    if task.operation_type == "nginx_rollback":
        return False
    return True


def _task_access(request: Request, session: Session, user: User):
    """解析任务中心的全量或本人受限任务范围。"""
    access = _task_permissions(
        request,
        session,
        user,
        include_poll_permissions=True,
    )
    if not access[2]:
        raise PermissionDenied("无访问权限", "当前账号没有查看任务的权限。")
    return access


@router.get(
    "/tasks/",
    response_class=Response,
    summary="任务中心",
    include_in_schema=False,
)
def task_center(
    request: Request,
    search: str = Query("", max_length=200),
    status: str = Query("", max_length=20),
    operation_type: str = Query("", max_length=40),
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=50),
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> Response:
    """渲染带分页、权限过滤和关键词搜索的统一任务列表。"""
    can_read_all, allowed_operations, _ = _task_access(request, session, user)
    conditions = _filtered_conditions(search, status, operation_type)
    if can_read_all:
        query = select(Task)
        count_query = select(func.count()).select_from(Task)
    else:
        query = select(Task).where(
            Task.operation_type.in_(allowed_operations),
            Task.trigger_user_id == user.id,
        )
        count_query = select(func.count()).select_from(Task).where(
            Task.operation_type.in_(allowed_operations),
            Task.trigger_user_id == user.id,
        )
    for condition in conditions:
        query = query.where(condition)
        count_query = count_query.where(condition)

    total = int(session.scalar(count_query) or 0)
    pages = max(1, int(ceil(total / float(per_page))))
    page = min(page, pages)
    tasks = session.scalars(
        query.order_by(Task.created_at.desc(), Task.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    user_ids = {task.trigger_user_id for task in tasks if task.trigger_user_id}
    usernames = {}
    if user_ids:
        usernames = dict(
            session.execute(
                select(User.id, User.username).where(User.id.in_(user_ids))
            ).all()
        )
    checker = getattr(request.app.state, "permission_checker", None)
    can_execute_upgrade = user.is_superuser or bool(
        checker and checker(session, user, "upgrade", "execute")
    )
    can_execute_uninstall = user.is_superuser or bool(
        checker and checker(session, user, "nginx_uninstall", "execute")
    )
    rows = []
    upgrade_phases = {}
    upgrade_task_ids = [
        task.id for task in tasks if task.operation_type == "nginx_upgrade"
    ]
    if upgrade_task_ids:
        from ngxops.upgrade.models import NginxUpgradeRun

        upgrade_phases = dict(
            session.execute(
                select(NginxUpgradeRun.task_id, NginxUpgradeRun.phase).where(
                    NginxUpgradeRun.task_id.in_(upgrade_task_ids)
                )
            ).all()
        )
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
                "username": usernames.get(task.trigger_user_id, "-"),
                "can_cancel": _can_cancel_task(
                    task,
                    upgrade_phases.get(task.id, ""),
                    can_execute_upgrade,
                ) and (task.operation_type != "nginx_uninstall" or can_execute_uninstall),
            }
        )
    can_publish = user.is_superuser or bool(
        checker and checker(session, user, "releases", "publish")
    )

    type_choices = [
        (operation, OPERATION_LABELS.get(operation, operation))
        for operation in OPERATION_TYPES
        if operation not in HIDDEN_OPERATION_TYPES
    ]
    return render_page(
        request,
        "tasks/center.html",
        context={
            "rows": rows,
            "search": search,
            "status_filter": status,
            "operation_type_filter": operation_type,
            "operation_type_choices": type_choices,
            "status_choices": STATUS_LABELS.items(),
            "has_any_filter": bool(search or status or operation_type),
            "can_read_releases": can_read_all,
            "can_publish": can_publish,
            "page_path": request.url.path,
            "pagination": {
                "page": page,
                "pages": pages,
                "total": total,
                "per_page": per_page,
                "per_page_options": PAGE_SIZE_OPTIONS,
            },
        },
        user=user,
        db_session=session,
    )


@router.get(
    "/tasks/{task_id}/",
    response_class=Response,
    summary="任务详情",
    include_in_schema=False,
)
def task_detail(
    request: Request,
    task_id: int,
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> Response:
    """渲染任务状态、结构化结果树和增量任务日志。"""
    can_read_all, allowed_operations, _ = _task_access(request, session, user)
    task = _visible_task_query(
        session, task_id, user, can_read_all, allowed_operations
    )
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    result_tree = None
    if task.result_tree_json:
        try:
            result_tree = json.loads(task.result_tree_json)
        except (TypeError, ValueError):
            result_tree = None
    log_rows = session.scalars(
        select(TaskLog)
        .where(TaskLog.task_id == task.id)
        .order_by(TaskLog.id.asc())
        .limit(51)
    ).all()
    has_more_logs = len(log_rows) > 50
    logs = log_rows[:50]
    targets = _target_nodes(task.target_hostnames, task.target_ips)
    target_configs = [
        item.strip()
        for item in (task.target_configs or "").split(",")
        if item.strip()
    ]
    trigger_username = "-"
    if task.trigger_user_id:
        trigger_username = session.scalar(
            select(User.username).where(User.id == task.trigger_user_id)
        ) or "-"
    upgrade_phase = ""
    if task.operation_type == "nginx_upgrade":
        from ngxops.upgrade.models import NginxUpgradeRun

        upgrade_phase = session.scalar(
            select(NginxUpgradeRun.phase).where(
                NginxUpgradeRun.task_id == task.id
            )
        ) or ""
    checker = getattr(request.app.state, "permission_checker", None)
    can_execute_upgrade = user.is_superuser or bool(
        checker and checker(session, user, "upgrade", "execute")
    )
    can_execute_uninstall = user.is_superuser or bool(
        checker and checker(session, user, "nginx_uninstall", "execute")
    )
    summary = result_tree.get("summary", {}) if isinstance(result_tree, dict) else {}
    return render_page(
        request,
        "tasks/detail.html",
        context={
            "task": task,
            "operation_label": OPERATION_LABELS.get(
                task.operation_type, task.operation_type
            ),
            "status_label": STATUS_LABELS.get(task.status, task.status),
            "targets": targets,
            "target_node_count": len(targets),
            "target_configs": target_configs[:50],
            "target_config_count": len(target_configs),
            "trigger_username": trigger_username,
            "can_read_releases": can_read_all,
            "result_tree": result_tree,
            "result_summary": summary,
            "result_field_labels": RESULT_FIELD_LABELS,
            "logs": logs,
            "log_cursor": max((row.id for row in logs), default=0),
            "has_more_logs": has_more_logs,
            "execution_duration": format_execution_duration(
                task.started_at, task.finished_at
            ),
            "is_release_type": task.operation_type
            in ("release_publish", "release_rollback"),
            "is_config_sync_type": task.operation_type == "config_batch_sync",
            "can_cancel": _can_cancel_task(
                task, upgrade_phase, can_execute_upgrade
            ) and (task.operation_type != "nginx_uninstall" or can_execute_uninstall),
        },
        user=user,
        db_session=session,
    )


def _target_nodes(hostnames: str, ips: str) -> list:
    """把任务目标主机名和 IP 按顺序合并展示。"""
    names = [item.strip() for item in (hostnames or "").split(",")]
    addresses = [item.strip() for item in (ips or "").split(",")]
    targets = []
    for index in range(max(len(names), len(addresses))):
        hostname = names[index] if index < len(names) else ""
        ip = addresses[index] if index < len(addresses) else ""
        target = "{} ({})".format(ip, hostname) if ip and hostname else hostname or ip
        if target:
            targets.append(target)
    return targets
