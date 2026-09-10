"""数据保留模块测试。"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from utils.data_retention import (
    purge_expired_data,
    _retention_days,
    _RETENTION_KEYS,
    _ACTIVE_STATUSES,
    _UPGRADE_ACTIVE_STATUSES,
    maybe_run_daily_purge,
)


class TestActiveStatuses(TestCase):
    def test_task_center_active(self):
        self.assertIn("pending", _ACTIVE_STATUSES)
        self.assertIn("running", _ACTIVE_STATUSES)

    def test_upgrade_active(self):
        self.assertIn("pending", _UPGRADE_ACTIVE_STATUSES)
        self.assertIn("compiling", _UPGRADE_ACTIVE_STATUSES)
        self.assertIn("upgrading", _UPGRADE_ACTIVE_STATUSES)


class TestRetentionKeys(TestCase):
    def test_all_keys_present(self):
        self.assertIn("task_center", _RETENTION_KEYS)
        self.assertIn("release_history", _RETENTION_KEYS)
        self.assertIn("audit_log", _RETENTION_KEYS)
        self.assertIn("login_log", _RETENTION_KEYS)
        self.assertIn("upgrade_task", _RETENTION_KEYS)


class TestRetentionDays(TestCase):
    @patch("utils.data_retention.get_setting")
    def test_positive(self, mock_get):
        mock_get.return_value = "30"
        self.assertEqual(_retention_days("test.key"), 30)

    @patch("utils.data_retention.get_setting")
    def test_zero(self, mock_get):
        mock_get.return_value = "0"
        self.assertEqual(_retention_days("test.key"), 0)

    @patch("utils.data_retention.get_setting")
    def test_negative_clamped(self, mock_get):
        mock_get.return_value = "-5"
        self.assertEqual(_retention_days("test.key"), 0)

    @patch("utils.data_retention.get_setting")
    def test_invalid(self, mock_get):
        mock_get.return_value = "abc"
        self.assertEqual(_retention_days("test.key"), 0)

    @patch("utils.data_retention.get_setting")
    def test_none(self, mock_get):
        mock_get.return_value = None
        self.assertEqual(_retention_days("test.key"), 0)


class TestPurgeExpiredData(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="purge-test",
            email="purge@example.com",
            password="pass1234",
        )

    @patch("utils.data_retention.get_setting")
    def test_all_zero_skips(self, mock_get):
        mock_get.return_value = "0"
        result = purge_expired_data()
        self.assertEqual(result["task_center"], 0)
        self.assertEqual(result["release_history"], 0)
        self.assertEqual(result["audit_log"], 0)
        self.assertEqual(result["login_log"], 0)
        self.assertEqual(result["upgrade_task"], 0)

    @patch("utils.data_retention.get_setting")
    def test_returns_dict(self, mock_get):
        mock_get.return_value = "90"
        result = purge_expired_data()
        self.assertIsInstance(result, dict)
        for key in (
            "task_center",
            "release_history",
            "audit_log",
            "login_log",
            "upgrade_task",
        ):
            self.assertIn(key, result)

    @patch("utils.data_retention.get_setting")
    def test_cleans_old_task_center(self, mock_get):
        from apps.releases.models import TaskCenterTask

        def side_effect(key, default=None):
            if "retention" in str(key):
                return "1"
            return "0"

        mock_get.side_effect = side_effect

        task = TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            source_batch="UG-OLD",
            trigger_user=self.user,
            status="success",
        )
        TaskCenterTask.objects.filter(pk=task.pk).update(
            created_at=timezone.now() - timedelta(days=10),
        )
        result = purge_expired_data()
        self.assertGreaterEqual(result["task_center"], 1)

    @patch("utils.data_retention.get_setting")
    def test_skips_active_tasks(self, mock_get):
        from apps.releases.models import TaskCenterTask

        def side_effect(key, default=None):
            if "task_center" in str(key):
                return "1"
            return "0"

        mock_get.side_effect = side_effect

        TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            source_batch="UG-RUNNING",
            trigger_user=self.user,
            status="running",
            created_at=timezone.now() - timedelta(days=10),
        )
        result = purge_expired_data()
        self.assertEqual(result["task_center"], 0)


class TestMaybeRunDailyPurge(TestCase):
    @patch("django.core.cache.cache")
    def test_first_run_today(self, mock_cache):
        mock_cache.get.return_value = None
        mock_cache.add.return_value = True
        result = maybe_run_daily_purge()
        self.assertTrue(result)

    @patch("django.core.cache.cache")
    def test_already_run_today(self, mock_cache):
        from django.utils import timezone

        today = timezone.localdate().isoformat()
        mock_cache.get.return_value = today
        result = maybe_run_daily_purge()
        self.assertFalse(result)

    @patch("django.core.cache.cache")
    def test_lock_failed(self, mock_cache):
        mock_cache.get.return_value = None
        mock_cache.add.return_value = False
        result = maybe_run_daily_purge()
        self.assertFalse(result)
