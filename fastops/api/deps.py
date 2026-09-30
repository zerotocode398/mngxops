"""FastAPI 依赖函数。"""

from typing import Optional

from fastapi import HTTPException, Request, Security, status
from fastapi.responses import RedirectResponse
from fastapi.security import APIKeyCookie

from fastops.core.config import get_settings
from fastops.core.security import read_session_token
from fastops.services.users import FastUser, get_user_by_id, user_has_permission

session_cookie = APIKeyCookie(
    name=get_settings().session_cookie,
    auto_error=False,
    description="登录成功后写入的 FastAPI 签名会话 Cookie。",
)


def _user_from_token(token: Optional[str]) -> Optional[FastUser]:
    """从签名 cookie token 解析当前用户。"""
    if not token:
        return None
    user_id = read_session_token(token)
    if not user_id:
        return None
    return get_user_by_id(user_id)


def get_current_user(request: Request) -> Optional[FastUser]:
    """从签名 cookie 中读取当前用户。"""
    return _user_from_token(request.cookies.get(get_settings().session_cookie))


def require_user(token: Optional[str] = Security(session_cookie)) -> FastUser:
    """要求 API 调用已登录，否则返回 401。"""
    user = _user_from_token(token)
    if user:
        return user
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="请先登录",
    )


def require_page_user(request: Request):
    """页面请求未登录时跳转登录页。"""
    user = get_current_user(request)
    if user:
        return user
    next_url = str(request.url.path)
    if request.url.query:
        next_url = f"{next_url}?{request.url.query}"
    return RedirectResponse(f"/login?next={next_url}", status_code=302)


def ensure_permission(user: FastUser, resource: str, action: str) -> None:
    """校验用户权限，无权时抛出 403。"""
    if not user_has_permission(user, resource, action):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前账号没有使用该功能的权限",
        )
