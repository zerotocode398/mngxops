"""系统设置上下文处理器测试。"""

from django.test import TestCase, RequestFactory

from apps.settings.context_processors import system_runtime_settings


class TestSystemRuntimeSettings(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_returns_poll_interval(self):
        request = self.factory.get("/")
        result = system_runtime_settings(request)
        self.assertIn("sys_poll_interval_ms", result)
        self.assertIsInstance(result["sys_poll_interval_ms"], int)
        self.assertGreaterEqual(result["sys_poll_interval_ms"], 1000)

    def test_returns_dashboard_refresh(self):
        request = self.factory.get("/")
        result = system_runtime_settings(request)
        self.assertIn("sys_dashboard_refresh_ms", result)
        self.assertIsInstance(result["sys_dashboard_refresh_ms"], int)
        self.assertGreaterEqual(result["sys_dashboard_refresh_ms"], 5000)

    def test_returns_positive_values(self):
        request = self.factory.get("/")
        result = system_runtime_settings(request)
        self.assertGreater(result["sys_poll_interval_ms"], 0)
        self.assertGreater(result["sys_dashboard_refresh_ms"], 0)
