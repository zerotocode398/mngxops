"""dashboard 模块单元测试（仪表盘统计计算）"""

import pytest
from django.utils import timezone
from datetime import timedelta

from apps.dashboard.views import _dashboard_stats, _dashboard_limit
from django.core.cache import cache


@pytest.mark.django_db
class TestDashboardStats:
    """_dashboard_stats 统计计算"""

    def test_empty_stats(self, admin_user):
        stats = _dashboard_stats(admin_user)
        assert stats["node_count"] == 0
        assert stats["online_count"] == 0
        assert stats["offline_count"] == 0
        assert stats["pending_push_count"] == 0
        assert stats["running_count"] == 0
        assert stats["failed_7d_count"] == 0

    def test_counts_nodes(self, admin_user, online_node, offline_node):
        stats = _dashboard_stats(admin_user)
        assert stats["node_count"] == 2
        assert stats["online_count"] == 1
        assert stats["offline_count"] == 1

    def test_counts_running_tasks(self, admin_user):
        from apps.releases.models import TaskCenterTask

        TaskCenterTask.objects.create(
            operation_type="node_ssh_test",
            status="running",
            trigger_user=admin_user,
        )
        stats = _dashboard_stats(admin_user)
        assert stats["running_count"] == 1

    def test_counts_failed_7d_tasks(self, admin_user):
        from apps.releases.models import TaskCenterTask

        task = TaskCenterTask.objects.create(
            operation_type="node_ssh_test",
            status="failed",
            trigger_user=admin_user,
        )
        TaskCenterTask.objects.filter(pk=task.pk).update(
            created_at=timezone.now() - timedelta(days=3),
        )
        stats = _dashboard_stats(admin_user)
        assert stats["failed_7d_count"] == 1

    def test_ignores_old_failed_tasks(self, admin_user):
        from apps.releases.models import TaskCenterTask

        task = TaskCenterTask.objects.create(
            operation_type="node_ssh_test",
            status="failed",
            trigger_user=admin_user,
        )
        TaskCenterTask.objects.filter(pk=task.pk).update(
            created_at=timezone.now() - timedelta(days=30),
        )
        stats = _dashboard_stats(admin_user)
        assert stats["failed_7d_count"] == 0

    def test_counts_pending_push(self, admin_user, online_node):
        from apps.configs.models import Config, ConfigNodeBinding

        config = Config.objects.create(name="app.conf", created_by=admin_user)
        ConfigNodeBinding.objects.create(
            config=config,
            node=online_node,
            remote_path="/etc/nginx/conf.d/app.conf",
            content="server { listen 80; }",
            sync_status="modified",
            created_by=admin_user,
        )
        stats = _dashboard_stats(admin_user)
        assert stats["pending_push_count"] == 1

    def test_ignores_synced_bindings(self, admin_user, online_node):
        from apps.configs.models import Config, ConfigNodeBinding

        config = Config.objects.create(name="app.conf", created_by=admin_user)
        ConfigNodeBinding.objects.create(
            config=config,
            node=online_node,
            remote_path="/etc/nginx/conf.d/app.conf",
            content="server { listen 80; }",
            sync_status="synced",
            created_by=admin_user,
        )
        stats = _dashboard_stats(admin_user)
        assert stats["pending_push_count"] == 0


class TestDashboardLimit:
    """_dashboard_limit 条数读取"""

    def test_returns_default(self):
        cache.delete("system_setting:limit.key.a")
        result = _dashboard_limit("limit.key.a", 10)
        assert result == 10

    def test_returns_integer(self):
        cache.delete("system_setting:limit.key.b")
        result = _dashboard_limit("limit.key.b", "20")
        assert result == 20

    def test_invalid_default_falls_back(self):
        cache.delete("system_setting:limit.key.c")
        result = _dashboard_limit("limit.key.c", "abc")
        assert result == "abc"
