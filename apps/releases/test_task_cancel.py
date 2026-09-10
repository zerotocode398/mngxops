"""任务取消功能测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.releases.task_cancel import (
    is_cancelled,
    mark_cancelled,
    update_if_active,
    finish_if_active,
    ACTIVE_STATUSES,
)
from apps.releases.models import TaskCenterTask


class TestIsCancelled(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="cancel-test",
            email="cancel@example.com",
            password="pass1234",
        )

    def test_not_cancelled(self):
        task = TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            source_batch="UG-001",
            trigger_user=self.user,
            status="running",
        )
        self.assertFalse(is_cancelled(task.pk))

    def test_is_cancelled(self):
        task = TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            source_batch="UG-002",
            trigger_user=self.user,
            status="cancelled",
        )
        self.assertTrue(is_cancelled(task.pk))

    def test_none_id(self):
        self.assertFalse(is_cancelled(None))
        self.assertFalse(is_cancelled(""))


class TestMarkCancelled(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="mark-cancel",
            email="mark-cancel@example.com",
            password="pass1234",
        )

    def test_mark_pending(self):
        task = TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            source_batch="UG-003",
            trigger_user=self.user,
            status="pending",
        )
        result = mark_cancelled(task.pk)
        self.assertTrue(result)
        task.refresh_from_db()
        self.assertEqual(task.status, "cancelled")
        self.assertEqual(task.progress, 100)

    def test_mark_running(self):
        task = TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            source_batch="UG-004",
            trigger_user=self.user,
            status="running",
            progress=50,
        )
        result = mark_cancelled(task.pk)
        self.assertTrue(result)
        task.refresh_from_db()
        self.assertEqual(task.status, "cancelled")
        self.assertEqual(task.progress, 100)

    def test_mark_already_terminal(self):
        task = TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            source_batch="UG-005",
            trigger_user=self.user,
            status="success",
        )
        result = mark_cancelled(task.pk)
        self.assertFalse(result)

    def test_mark_custom_detail(self):
        task = TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            source_batch="UG-006",
            trigger_user=self.user,
            status="running",
        )
        mark_cancelled(task.pk, detail="自定义取消原因", result="自定义结果")
        task.refresh_from_db()
        self.assertEqual(task.detail, "自定义取消原因")


class TestUpdateIfActive(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="update-active",
            email="update-active@example.com",
            password="pass1234",
        )

    def test_update_running(self):
        task = TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            source_batch="UG-007",
            trigger_user=self.user,
            status="running",
            progress=50,
        )
        updated = update_if_active(task.pk, progress=75)
        self.assertEqual(updated, 1)
        task.refresh_from_db()
        self.assertEqual(task.progress, 75)

    def test_update_skips_cancelled(self):
        task = TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            source_batch="UG-008",
            trigger_user=self.user,
            status="cancelled",
            progress=90,
        )
        updated = update_if_active(task.pk, progress=100)
        self.assertEqual(updated, 0)

    def test_finish_if_active_running(self):
        task = TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            source_batch="UG-009",
            trigger_user=self.user,
            status="running",
        )
        updated = finish_if_active(task.pk, status="success")
        self.assertEqual(updated, 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "success")

    def test_active_statuses(self):
        self.assertIn("pending", ACTIVE_STATUSES)
        self.assertIn("running", ACTIVE_STATUSES)
