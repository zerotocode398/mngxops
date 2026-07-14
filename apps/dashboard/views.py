from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

from apps.nodes.models import Node, NodeGroup
from apps.configs.models import ConfigNodeBinding
from apps.releases.models import ReleaseTask


@login_required
def index(request):
    """仪表盘首页：展示系统概览统计、最近发布任务和下发失败告警"""
    node_count = Node.objects.count()
    online_count = Node.objects.filter(status="online").count()
    offline_count = node_count - online_count
    node_group_count = NodeGroup.objects.count()
    release_task_count = ReleaseTask.objects.count()
    # 待推送：本地有修改但未推送的绑定数
    pending_push_count = ConfigNodeBinding.objects.filter(sync_status="modified").count()
    # 冲突：本地与远程均被修改产生冲突的绑定数
    conflict_count = ConfigNodeBinding.objects.filter(sync_status="conflict").count()

    # 最近发布任务（最多10条），预加载关联数据减少查询
    recent_tasks = ReleaseTask.objects.select_related(
        "config", "version", "operator", "node"
    ).order_by("-created_at")[:10]

    # 下发失败的绑定列表（最多10条）
    failed_configs = (
        ConfigNodeBinding.objects.filter(sync_status="failed")
        .select_related("config", "node")
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
    pending_push_count = ConfigNodeBinding.objects.filter(sync_status="modified").count()
    conflict_count = ConfigNodeBinding.objects.filter(sync_status="conflict").count()

    return JsonResponse({
        "node_count": node_count,
        "online_count": online_count,
        "offline_count": offline_count,
        "pending_push_count": pending_push_count,
        "conflict_count": conflict_count,
    })
