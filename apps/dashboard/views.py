import sys
import django

from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.shortcuts import render
from django.utils import timezone

from apps.nodes.models import Node, NodeGroup
from apps.configs.models import ConfigVersion
from apps.releases.models import ReleaseTask

User = get_user_model()


@login_required
def index(request):
    now = timezone.now()
    node_count = Node.objects.count()
    online_count = Node.objects.filter(status="online").count()
    node_group_count = NodeGroup.objects.count()
    config_version_count = ConfigVersion.objects.count()
    release_task_count = ReleaseTask.objects.count()

    recent_nodes = Node.objects.select_related("credential").order_by("-updated_at")[
        :10
    ]

    recent_tasks = ReleaseTask.objects.select_related(
        "config", "version", "operator"
    ).order_by("-created_at")[:10]

    context = {
        "node_count": node_count,
        "online_count": online_count,
        "node_group_count": node_group_count,
        "config_version_count": config_version_count,
        "release_task_count": release_task_count,
        "recent_nodes": recent_nodes,
        "recent_tasks": recent_tasks,
        "current_time": now,
        "django_version": django.get_version(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "last_login_text": (
            timezone.localtime(request.user.last_login).strftime("%Y-%m-%d %H:%M")
            if request.user.last_login
            else "首次登录"
        ),
    }
    return render(request, "dashboard/index.html", context)
