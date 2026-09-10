"""用户模块上下文处理器测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory

from apps.users.context_processors import perm_denied_alert
from apps.users.permissions import SESSION_PERM_DENIED_KEY


class TestPermDeniedAlert(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="ctx-test",
            email="ctx@example.com",
            password="pass1234",
        )

    def test_no_alert_in_session(self):
        request = self.factory.get("/")
        request.user = self.user
        request.session = {}
        result = perm_denied_alert(request)
        self.assertIsNone(result["perm_denied_alert"])

    def test_alert_in_session(self):
        request = self.factory.get("/")
        request.user = self.user
        request.session = {
            SESSION_PERM_DENIED_KEY: {
                "title": "权限不足",
                "message": "您没有访问该页面的权限",
            }
        }
        result = perm_denied_alert(request)
        self.assertIsNotNone(result["perm_denied_alert"])
        self.assertEqual(result["perm_denied_alert"]["title"], "权限不足")

    def test_alert_empty_title_and_message(self):
        request = self.factory.get("/")
        request.user = self.user
        request.session = {SESSION_PERM_DENIED_KEY: {"title": "", "message": ""}}
        result = perm_denied_alert(request)
        self.assertIsNone(result["perm_denied_alert"])

    def test_alert_only_title(self):
        request = self.factory.get("/")
        request.user = self.user
        request.session = {
            SESSION_PERM_DENIED_KEY: {"title": "禁止访问", "message": ""}
        }
        result = perm_denied_alert(request)
        self.assertIsNotNone(result["perm_denied_alert"])
        self.assertEqual(result["perm_denied_alert"]["title"], "禁止访问")

    def test_session_cleared_after_pop(self):
        request = self.factory.get("/")
        request.user = self.user
        request.session = {
            SESSION_PERM_DENIED_KEY: {"title": "测试", "message": "测试消息"}
        }
        perm_denied_alert(request)
        self.assertNotIn(SESSION_PERM_DENIED_KEY, request.session)
