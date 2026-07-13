from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

from apps.nodes.models import Node, NodeGroup
from apps.configs.models import Config
from apps.releases.models import ReleaseTask


@login_required
def index(request):
    """仪表盘首页：展示系统概览统计、最近发布任务和下发失败告警"""
    node_count = Node.objects.count()
    online_count = Node.objects.filter(status="online").count()
    offline_count = node_count - online_count
    node_group_count = NodeGroup.objects.count()
    release_task_count = ReleaseTask.objects.count()
    # 待推送：sync_status 为 pending 的配置数
    pending_push_count = Config.objects.filter(sync_status="pending").count()
    # 冲突：当前数据模型中暂无 conflict 状态，预留为 0
    conflict_count = 0

    # 最近发布任务（最多10条），预加载关联数据减少查询
    recent_tasks = ReleaseTask.objects.select_related(
        "config", "version", "operator", "node"
    ).order_by("-created_at")[:10]

    # 下发失败的配置列表（最多10条）
    failed_configs = (
        Config.objects.filter(sync_status="failed")
        .prefetch_related("nodes")
        .order_by("-updated_at")[:10]
    )

    context = {
        "node_count": node_count,
        "online_count": online_count,
        "offline_count": offline_count,
        "node_group_count": node_group_count,
        "release_task_count": release_task_count,
        "pending_push_count": pending_push_count,
        "conflict_count": conflict_count,
        "recent_tasks": recent_tasks,
        "failed_configs": failed_configs,
    }
    return render(request, "dashboard/index.html", context)


@login_required
def stats_api(request):
    """统计卡片轮询 API：返回轻量级统计数据供前端 30 秒轮询"""
    node_count = Node.objects.count()
    online_count = Node.objects.filter(status="online").count()
    offline_count = node_count - online_count
    pending_push_count = Config.objects.filter(sync_status="pending").count()
    conflict_count = 0

    return JsonResponse({
        "node_count": node_count,
        "online_count": online_count,
        "offline_count": offline_count,
        "pending_push_count": pending_push_count,
        "conflict_count": conflict_count,
    })
