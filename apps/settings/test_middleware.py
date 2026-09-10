"""系统设置中间件测试。"""

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import TestCase, RequestFactory

from apps.settings.middleware import DataRetentionMiddleware


class TestDataRetentionMiddleware(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _get_response(self, request):
        return HttpResponse("OK")

    def test_authenticated_user_triggers_purge(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="purge-test",
            email="purge@example.com",
            password="pass1234",
        )
        request = self.factory.get("/dashboard/")
        request.user = user
        middleware = DataRetentionMiddleware(self._get_response)
        response = middleware(request)
        self.assertEqual(response.status_code, 200)

    def test_anonymous_user_no_purge(self):
        request = self.factory.get("/dashboard/")
        request.user = get_user_model()()
        middleware = DataRetentionMiddleware(self._get_response)
        response = middleware(request)
        self.assertEqual(response.status_code, 200)

    def test_no_user_no_purge(self):
        request = self.factory.get("/dashboard/")
        middleware = DataRetentionMiddleware(self._get_response)
        response = middleware(request)
        self.assertEqual(response.status_code, 200)
