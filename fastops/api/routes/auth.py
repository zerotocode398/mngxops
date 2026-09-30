"""FastAPI 登录与退出路由。"""

from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from fastops.core.config import get_settings
from fastops.core.security import create_session_token
from fastops.main_templates import templates
from fastops.services.users import authenticate_user

router = APIRouter(tags=["认证"])


def _safe_next(value: str) -> str:
    """限制登录后跳转地址只能是站内路径。"""
    value = (value or "/fastapi").strip()
    if not value.startswith("/") or value.startswith("//"):
        return "/fastapi"
    return value


@router.get("/login", response_class=HTMLResponse, summary="登录页面")
async def login_page(request: Request, next: str = "/fastapi"):
    """渲染 FastAPI 登录页面。"""
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "next_url": _safe_next(next), "error": ""},
    )


@router.post("/login", response_class=HTMLResponse, summary="提交登录")
async def login_submit(request: Request):
    """处理 FastAPI 登录提交。"""
    body = (await request.body()).decode("utf-8")
    data = parse_qs(body)
    username = (data.get("username") or [""])[0]
    password = (data.get("password") or [""])[0]
    next_url = _safe_next((data.get("next") or ["/fastapi"])[0])
    user = authenticate_user(username, password)
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "next_url": next_url,
                "error": "用户名或密码错误",
            },
            status_code=400,
        )
    settings = get_settings()
    response = RedirectResponse(next_url, status_code=302)
    response.set_cookie(
        settings.session_cookie,
        create_session_token(user.id),
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout", summary="退出登录")
async def logout():
    """清理 FastAPI 登录 cookie。"""
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(get_settings().session_cookie)
    return response

