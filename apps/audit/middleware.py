import threading
from django.http import JsonResponse

_thread_locals = threading.local()


def get_current_request():
    return getattr(_thread_locals, "request", None)


def get_current_user():
    req = get_current_request()
    if req and hasattr(req, "user") and req.user.is_authenticated:
        return req.user
    return None


class CurrentUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _thread_locals.request = request
        response = self.get_response(request)
        _thread_locals.request = None
        return response


class AjaxErrorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not self._is_ajax(request):
            return response

        content_type = response.get("Content-Type", "")
        if "application/json" in content_type or "text/javascript" in content_type:
            return response

        if response.status_code == 302 or response.status_code == 301:
            return JsonResponse(
                {
                    "success": False,
                    "message": "登录已过期，请重新登录",
                    "redirect": response.get("Location", "/accounts/login/"),
                },
                status=401,
            )

        if response.status_code == 403:
            content = response.content.decode("utf-8", errors="replace").strip()
            return JsonResponse(
                {
                    "success": False,
                    "message": content or "CSRF 验证失败，请刷新页面后重试",
                },
                status=403,
            )

        return response

    @staticmethod
    def _is_ajax(request):
        return request.headers.get("X-Requested-With") == "XMLHttpRequest"
