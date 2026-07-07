from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.nodes.models import Node, NodeGroup
from apps.configs.models import Config, ConfigVersion
from apps.releases.models import ReleaseTask


@login_required
def index(request):
    node_count = Node.objects.count()
    online_count = Node.objects.filter(status="online").count()
    offline_count = node_count - online_count
    node_group_count = NodeGroup.objects.count()
    config_version_count = ConfigVersion.objects.count()
    release_task_count = ReleaseTask.objects.count()
    config_failed_count = Config.objects.filter(sync_status="failed").count()

    recent_nodes = Node.objects.select_related("credential").order_by("-updated_at")[
        :10
    ]

    recent_tasks = ReleaseTask.objects.select_related(
        "config", "version", "operator"
    ).order_by("-created_at")[:10]

    failed_configs = (
        Config.objects.filter(sync_status="failed")
        .select_related("node")
        .order_by("-updated_at")[:10]
    )

    context = {
        "node_count": node_count,
        "online_count": online_count,
        "offline_count": offline_count,
        "node_group_count": node_group_count,
        "config_version_count": config_version_count,
        "release_task_count": release_task_count,
        "config_failed_count": config_failed_count,
        "recent_nodes": recent_nodes,
        "recent_tasks": recent_tasks,
        "failed_configs": failed_configs,
    }
    return render(request, "dashboard/index.html", context)
