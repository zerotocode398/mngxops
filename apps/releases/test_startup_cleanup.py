"""启动清理任务测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.releases.startup_cleanup import (
    cleanup_stale_running_tasks,
    RESTART_DETAIL,
)
from apps.releases.models import TaskCenterTask, ReleaseTask
from apps.upgrade.models import NginxUpgradeTask
from apps.nginx_install.models import NginxInstallTask
from apps.nginx_uninstall.models import NginxUninstallTask
from apps.nodes.models import Node


class TestCleanupStaleRunningTasks(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="cleanup-test",
            email="cleanup@example.com",
            password="pass1234",
        )
        self.node = Node.objects.create(
            hostname="cleanup-node",
            ip="10.0.0.1",
            status="online",
            created_by=self.user,
        )

    def test_cleanup_no_stale_tasks(self):
        total = cleanup_stale_running_tasks()
        self.assertEqual(total, 0)

    def test_cleanup_pending_task_center(self):
        task = TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            source_batch="UG-0001",
            trigger_user=self.user,
            status="pending",
        )
        total = cleanup_stale_running_tasks()
        self.assertGreaterEqual(total, 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.detail, RESTART_DETAIL)

    def test_cleanup_running_task_center(self):
        task = TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            source_batch="UG-0002",
            trigger_user=self.user,
            status="running",
            progress=50,
        )
        total = cleanup_stale_running_tasks()
        self.assertGreaterEqual(total, 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.progress, 100)

    def test_cleanup_pending_release_task(self):
        from apps.configs.models import Config

        config = Config.objects.create(
            name="test-config",
            default_remote_path="/etc/test",
            created_by=self.user,
        )
        ReleaseTask.objects.create(
            node=self.node,
            config=config,
            batch_number="REL-0001",
            status="pending",
            operator=self.user,
        )
        total = cleanup_stale_running_tasks()
        self.assertGreaterEqual(total, 1)

    def test_cleanup_respects_terminal_status(self):
        task = TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            source_batch="UG-0003",
            trigger_user=self.user,
            status="success",
        )
        cleanup_stale_running_tasks()
        task.refresh_from_db()
        self.assertEqual(task.status, "success")
