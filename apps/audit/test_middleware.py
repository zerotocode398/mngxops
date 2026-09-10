"""审计中间件测试。"""

import json
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import TestCase, RequestFactory

from apps.audit.middleware import (
    CurrentUserMiddleware,
    AjaxErrorMiddleware,
    get_current_request,
    get_current_user,
)


class TestCurrentUserMiddleware(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="middleware-test",
            email="mw@example.com",
            password="pass1234",
        )

    def _get_response(self, request):
        return HttpResponse("OK")

    def test_user_is_set_on_request(self):
        request = self.factory.get("/accounts/login/")
        request.user = self.user

        captured_user = []

        def get_response(req):
            captured_user.append(get_current_user())
            return HttpResponse("OK")

        middleware = CurrentUserMiddleware(get_response)
        middleware(request)
        self.assertEqual(captured_user[0], self.user)

    def test_anonymous_user_not_returned(self):
        request = self.factory.get("/accounts/login/")
        request.user = get_user_model()()
        middleware = CurrentUserMiddleware(self._get_response)
        middleware(request)
        self.assertIsNone(get_current_user())

    def test_request_cleared_after_response(self):
        request = self.factory.get("/accounts/login/")
        request.user = self.user
        middleware = CurrentUserMiddleware(self._get_response)
        middleware(request)
        self.assertIsNone(get_current_request())


class TestAjaxErrorMiddleware(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _get_response(self, request):
        return HttpResponse("OK")

    def test_non_ajax_request_passes(self):
        request = self.factory.get("/some/url/")
        middleware = AjaxErrorMiddleware(self._get_response)
        response = middleware(request)
        self.assertEqual(response.status_code, 200)

    def test_ajax_failed_request(self):
        request = self.factory.get("/some/url/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        request.user = get_user_model()()
        middleware = AjaxErrorMiddleware(self._get_response)
        response = middleware(request)
        self.assertEqual(response.status_code, 200)

    def test_ajax_redirect_returns_401(self):
        request = self.factory.get(
            "/protected/", HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        request.user = get_user_model()()

        def _redirect_response(req):
            from django.http import HttpResponseRedirect

            return HttpResponseRedirect("/accounts/login/")

        middleware = AjaxErrorMiddleware(_redirect_response)
        response = middleware(request)
        self.assertEqual(response.status_code, 401)
        resp_data = json.loads(response.content)
        self.assertFalse(resp_data["success"])
        self.assertIn("redirect", resp_data)

    def test_ajax_403_returns_json(self):
        request = self.factory.get(
            "/forbidden/", HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        request.user = get_user_model()()

        def _forbidden_response(req):
            return HttpResponse("CSRF failed", status=403)

        middleware = AjaxErrorMiddleware(_forbidden_response)
        response = middleware(request)
        self.assertEqual(response.status_code, 403)
        resp_data = json.loads(response.content)
        self.assertFalse(resp_data["success"])

    def test_is_ajax_false_without_header(self):
        request = self.factory.get("/")
        self.assertFalse(AjaxErrorMiddleware._is_ajax(request))
