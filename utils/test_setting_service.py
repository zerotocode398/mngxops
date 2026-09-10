"""系统设置服务测试。"""

from unittest.mock import patch, MagicMock

from django.test import TestCase

from utils.setting_service import (
    get_setting,
    get_recent_tasks_limit,
    refresh_setting_cache,
    _cast_value,
    _defaults,
)


class TestCastValue(TestCase):
    def test_integer(self):
        self.assertEqual(_cast_value("42", "integer"), 42)
        self.assertEqual(_cast_value("0", "integer"), 0)

    def test_boolean_true(self):
        self.assertTrue(_cast_value("true", "boolean"))
        self.assertTrue(_cast_value("1", "boolean"))
        self.assertTrue(_cast_value("yes", "boolean"))

    def test_boolean_false(self):
        self.assertFalse(_cast_value("false", "boolean"))
        self.assertFalse(_cast_value("0", "boolean"))
        self.assertFalse(_cast_value("no", "boolean"))

    def test_string(self):
        self.assertEqual(_cast_value("hello", "string"), "hello")

    def test_unknown_type(self):
        self.assertEqual(_cast_value("hello", "unknown"), "hello")


class TestGetSetting(TestCase):
    @patch("utils.setting_service.cache")
    def test_cache_hit(self, mock_cache):
        mock_cache.get.return_value = "cached_value"
        result = get_setting("test.key")
        self.assertEqual(result, "cached_value")

    @patch("utils.setting_service.cache")
    def test_cache_miss_db_fallback(self, mock_cache):
        mock_cache.get.return_value = None
        with patch("apps.settings.models.SystemSetting.objects.get") as mock_get:
            mock_obj = MagicMock()
            mock_obj.value = "42"
            mock_obj.type = "integer"
            mock_get.return_value = mock_obj
            result = get_setting("test.key")
            self.assertEqual(result, 42)

    @patch("utils.setting_service.cache")
    def test_cache_miss_db_error(self, mock_cache):
        mock_cache.get.return_value = None
        with patch("apps.settings.models.SystemSetting.objects.get") as mock_get:
            mock_get.side_effect = Exception("not found")
            result = get_setting("test.key", default="fallback")
            self.assertEqual(result, "fallback")


class TestGetRecentTasksLimit(TestCase):
    @patch("utils.setting_service.get_setting")
    def test_default(self, mock_get):
        mock_get.return_value = "20"
        result = get_recent_tasks_limit()
        self.assertEqual(result, 20)

    @patch("utils.setting_service.get_setting")
    def test_custom(self, mock_get):
        mock_get.return_value = "50"
        result = get_recent_tasks_limit(default=20)
        self.assertEqual(result, 50)

    @patch("utils.setting_service.get_setting")
    def test_minimum_one(self, mock_get):
        mock_get.return_value = "0"
        result = get_recent_tasks_limit(default=20)
        self.assertEqual(result, 1)

    @patch("utils.setting_service.get_setting")
    def test_invalid(self, mock_get):
        mock_get.return_value = "abc"
        result = get_recent_tasks_limit(default=20)
        self.assertEqual(result, 20)


class TestRefreshSettingCache(TestCase):
    @patch("utils.setting_service.cache")
    def test_single_key(self, mock_cache):
        refresh_setting_cache("test.key")
        mock_cache.delete.assert_called_once_with("system_setting:test.key")

    @patch("utils.setting_service.cache")
    def test_all_keys(self, mock_cache):
        refresh_setting_cache()
        mock_cache.delete_many.assert_called_once()
