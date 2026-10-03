"""将认证、权限与 CSRF 异常转换为页面或 JSON 响应。"""

from typing import Optional
from urllib.parse import quote, urlsplit

from fastapi import Request
from fastapi.responses import RedirectResponse, Response

from ngxops.api.contracts import api_error_response, is_json_api_request
from ngxops.security.dependencies import PERM_DENIED_MESSAGE
from ngxops.security.errors import (
    AuthenticationRequired,
    CsrfViolation,
    PermissionDenied,
)
from ngxops.security.sessions import PERMISSION_ALERT_KEY


def is_json_request(request: Request) -> bool:
    """识别 AJAX、JSON Accept 和约定的 JSON API 路径。"""
    return is_json_api_request(request)


def _same_origin_referer(request: Request) -> Optional[str]:
    """只返回同源且不同于当前请求路径的 Referer。"""
    referer = request.headers.get("referer", "")
    if not referer:
        return None
    try:
        parsed = urlsplit(referer)
    except ValueError:
        return None
    if parsed.scheme.lower() != request.url.scheme.lower():
        return None
    if parsed.netloc.lower() != request.url.netloc.lower():
        return None
    if parsed.path == request.url.path:
        return None
    return referer


def _forbidden_html(
    request: Request,
    title: str,
    message: str,
) -> Response:
    """使用 Jinja2 返回统一的 HTML 403 页面。"""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        name="errors/403.html",
        request=request,
        context={"title": title, "message": message},
        status_code=403,
    )


async def handle_authentication_required(
    request: Request,
    exc: AuthenticationRequired,
) -> Response:
    """将未登录请求转换为 JSON 401 或登录页跳转。"""
    next_url = request.url.path
    if request.url.query:
        next_url += "?" + request.url.query
    login_url = "/login/?next={}".format(quote(next_url, safe=""))
    if is_json_request(request):
        return api_error_response(
            401,
            "登录已过期，请重新登录",
            redirect=login_url,
        )
    return RedirectResponse(
        url=login_url,
        status_code=302,
    )


async def handle_permission_denied(
    request: Request,
    exc: PermissionDenied,
) -> Response:
    """将无权请求转换为 JSON 403、来源页提示或 Jinja2 403。"""
    if is_json_request(request):
        return api_error_response(403, exc.message)

    request.session[PERMISSION_ALERT_KEY] = {
        "title": exc.title,
        "message": exc.message or PERM_DENIED_MESSAGE,
    }
    referer = _same_origin_referer(request)
    if referer:
        return RedirectResponse(url=referer, status_code=302)
    return _forbidden_html(request, exc.title, exc.message)


async def handle_csrf_violation(
    request: Request,
    exc: CsrfViolation,
) -> Response:
    """返回 CSRF 校验失败的 JSON 或 Jinja2 页面。"""
    message = "请求验证失败，请刷新页面后重试。"
    if is_json_request(request):
        return api_error_response(403, message)
    return _forbidden_html(request, "请求验证失败", message)
