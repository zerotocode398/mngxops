"""校验表单与 JSON 写请求的 CSRF 来源和令牌。"""

import hmac
from typing import Optional
from urllib.parse import urlsplit

from fastapi import Request

from ngxops.security.errors import CsrfViolation
from ngxops.security.sessions import CSRF_COOKIE_NAME, _valid_csrf_cookie


SAFE_METHODS = frozenset(("GET", "HEAD", "OPTIONS", "TRACE"))
CSRF_FIELD_NAMES = ("csrf_token", "csrfmiddlewaretoken")


def _origin_value(value: str, allow_url_path: bool = False) -> Optional[str]:
    """将 URL 规范为可比较的 scheme、host 和非默认端口。"""
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return None
    if (
        scheme not in ("http", "https")
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or (
            not allow_url_path
            and (parsed.path not in ("", "/") or parsed.query or parsed.fragment)
        )
    ):
        return None
    if ":" in hostname and not hostname.startswith("["):
        hostname = "[{}]".format(hostname)
    default_port = 80 if scheme == "http" else 443
    authority = (
        hostname
        if port is None or port == default_port
        else "{}:{}".format(hostname, port)
    )
    return "{}://{}".format(scheme, authority)


def _is_trusted_origin(
    request: Request,
    value: str,
    allow_url_path: bool = False,
) -> bool:
    """判断来源是否与请求同源或列入可信来源配置。"""
    normalized = _origin_value(value, allow_url_path=allow_url_path)
    if normalized is None:
        return False
    current_origin = _origin_value(str(request.base_url))
    if normalized == current_origin:
        return True
    settings = request.app.state.settings
    trusted_origins = {
        origin
        for origin in (
            _origin_value(item)
            for item in settings.csrf_trusted_origins
        )
        if origin is not None
    }
    return normalized in trusted_origins


async def require_csrf(request: Request) -> None:
    """校验所有非安全 HTTP 方法的 CSRF Cookie、表单令牌和来源。"""
    if request.method.upper() in SAFE_METHODS:
        return

    secret_key = request.app.state.secret_key
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
    if not secret_key or not _valid_csrf_cookie(cookie_token, secret_key):
        raise CsrfViolation()

    submitted_token = request.headers.get("x-csrftoken") or request.headers.get(
        "x-csrf-token", ""
    )
    if not submitted_token:
        content_type = request.headers.get("content-type", "").lower()
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            try:
                form = await request.form()
            except Exception as exc:
                raise CsrfViolation() from exc
            for field_name in CSRF_FIELD_NAMES:
                value = form.get(field_name)
                if isinstance(value, str):
                    submitted_token = value
                    break

    if not hmac.compare_digest(submitted_token, cookie_token):
        raise CsrfViolation()

    origin = request.headers.get("origin")
    if origin:
        if not _is_trusted_origin(request, origin):
            raise CsrfViolation()
        return

    referer = request.headers.get("referer")
    if referer and not _is_trusted_origin(request, referer, allow_url_path=True):
        raise CsrfViolation()
    if request.app.state.settings.https_only and not referer:
        raise CsrfViolation()
