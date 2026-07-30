from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from apps.nodes.models import Node
from apps.configs.models import ConfigNodeBinding
from apps.releases.models import TaskCenterTask
from apps.releases.task_result import format_task_center_summary
from apps.users.permissions import user_has_permission
from utils.setting_service import get_setting


def _dashboard_limit(key, default=20):
    """读取仪表盘列表条数上限"""
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
    # 仅有 nodes.update 时：本人触发的批量测/配置同步
    if user_has_permission(user, "nodes", "update"):
        return qs.filter(
            operation_type__in=["node_batch_test", "config_batch_sync"],
            trigger_user=user,
        )
    return qs.none()


def _dashboard_stats(user):
    """汇总首页统计卡数字"""
    node_count = Node.objects.count()
    online_count = Node.objects.filter(status="online").count()
    offline_count = node_count - online_count
    pending_push_count = ConfigNodeBinding.objects.filter(
        sync_status="modified", node__is_deleted=False
    ).count()

    task_qs = _task_center_queryset_for_user(user)
    running_count = task_qs.filter(status="running").count()
    since = timezone.now() - timedelta(days=7)
    failed_7d_count = task_qs.filter(status="failed", created_at__gte=since).count()

    return {
        "node_count": node_count,
        "online_count": online_count,
        "offline_count": offline_count,
        "pending_push_count": pending_push_count,
        "running_count": running_count,
        "failed_7d_count": failed_7d_count,
    }


@login_required
def index(request):
    """仪表盘首页：统计概览与最近任务中心记录"""
    stats = _dashboard_stats(request.user)
    recent_limit = _dashboard_limit("dashboard.recent_tasks_count", 20)

    recent_tasks = list(
        _task_center_queryset_for_user(request.user).order_by("-created_at")[:recent_limit]
    )
    # 注入列表摘要（目标 + 结果），对齐任务中心
    for task in recent_tasks:
        primary, secondary = format_task_center_summary(task)
        task.summary_primary = primary
        task.summary_secondary = secondary

    context = {
        **stats,
        "recent_tasks": recent_tasks,
    }
    return render(request, "dashboard/index.html", context)


@login_required
def stats_api(request):
    """统计卡片轮询 API：返回轻量级统计数据供前端轮询"""
    return JsonResponse(_dashboard_stats(request.user))
