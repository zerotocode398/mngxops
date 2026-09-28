from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from apps.nodes.models import Node
from apps.configs.models import ConfigNodeBinding
from apps.releases.models import TaskCenterTask
from apps.releases.task_result import format_task_center_summary
from apps.users.permissions import (
    user_has_permission,
    task_center_limited_ops_for_user,
    user_can_access_limited_task_center,
)
from utils.setting_service import get_recent_tasks_limit, get_setting


def _dashboard_limit(key, default=20):
    """读取仪表盘列表条数上限（兼容旧调用；最近任务请用 get_recent_tasks_limit）"""
    if key == "dashboard.recent_tasks_count":
        return get_recent_tasks_limit(default)
    try:
        return max(1, int(get_setting(key, str(default)) or default))
    except (TypeError, ValueError):
        return default


def _task_center_queryset_for_user(user):
    """按任务中心权限返回 TaskCenter 查询集（与列表可见范围对齐）"""
    qs = TaskCenterTask.objects.select_related("trigger_user")
    can_read_release = user_has_permission(user, "releases", "read")
    if can_read_release:
        return qs
    allowed = task_center_limited_ops_for_user(user)
    if not allowed:
        return qs.none()
    return qs.filter(operation_type__in=allowed, trigger_user=user)


def _dashboard_stats(user):
    """汇总首页统计卡数字"""
    node_count = Node.objects.count()
    online_count = Node.objects.filter(status="online").count()
    offline_count = Node.objects.filter(status="offline").count()
    unknown_count = Node.objects.filter(status="unknown").count()
    pending_push_count = ConfigNodeBinding.objects.filter(
        sync_status="modified", node__is_deleted=False
    ).count()
    config_base = ConfigNodeBinding.objects.filter(node__is_deleted=False)
    config_total = config_base.count()
    config_synced = config_base.filter(sync_status="synced").count()
    config_orphaned = config_base.filter(sync_status="orphaned").count()
    config_failed = config_base.filter(sync_status="failed").count()
    config_marked_deleted = config_base.filter(sync_status="marked_deleted").count()

    task_qs = _task_center_queryset_for_user(user)
    running_count = task_qs.filter(status="running").count()
    since = timezone.now() - timedelta(days=7)
    task_7d_total = task_qs.filter(created_at__gte=since).count()
    task_7d_success = task_qs.filter(status="success", created_at__gte=since).count()
    failed_7d_count = task_qs.filter(status="failed", created_at__gte=since).count()

    return {
        "node_count": node_count,
        "online_count": online_count,
        "offline_count": offline_count,
        "unknown_count": unknown_count,
        "pending_push_count": pending_push_count,
        "config_total": config_total,
        "config_synced": config_synced,
        "config_orphaned": config_orphaned,
        "config_failed": config_failed,
        "config_marked_deleted": config_marked_deleted,
        "running_count": running_count,
        "task_7d_total": task_7d_total,
        "task_7d_success": task_7d_success,
        "failed_7d_count": failed_7d_count,
    }


@login_required
def index(request):
    """仪表盘首页：统计概览与最近任务中心记录"""
    stats = _dashboard_stats(request.user)
    recent_limit = _dashboard_limit("dashboard.recent_tasks_count", 20)

    recent_tasks = list(
        _task_center_queryset_for_user(request.user).order_by("-created_at")[
            :recent_limit
        ]
    )
    # 注入列表摘要（目标 + 结果），对齐任务中心
    for task in recent_tasks:
        primary, secondary = format_task_center_summary(task)
        task.summary_primary = primary
        task.summary_secondary = secondary

    context = {
        **stats,
        "recent_tasks": recent_tasks,
        "can_access_task_center": (
            user_has_permission(request.user, "releases", "read")
            or user_can_access_limited_task_center(request.user)
        ),
    }
    return render(request, "dashboard/index.html", context)


@login_required
def stats_api(request):
    """统计卡片轮询 API：返回轻量级统计数据供前端轮询"""
    return JsonResponse(_dashboard_stats(request.user))
