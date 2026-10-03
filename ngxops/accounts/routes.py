"""提供登录、登出、个人中心和修改密码页面。"""

from types import SimpleNamespace
from typing import Optional
from urllib.parse import urlsplit
import uuid
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from ngxops.accounts.service import (
    LoginResult,
    authenticate_and_log,
    change_password,
    complete_login,
    find_login_session_conflict,
)
from ngxops.audit.service import request_client_ip
from ngxops.accounts.models import User
from ngxops.database.session import get_session, session_scope
from ngxops.security.dependencies import (
    get_current_user,
    login_user,
    logout_user,
    require_authenticated_user,
)
from ngxops.ui import render_page


router = APIRouter(tags=["accounts"])
_DISPLAY_TIMEZONE = timezone(timedelta(hours=8))


@router.get("/login/", include_in_schema=False)
async def login_page(
    request: Request,
    user: Optional[User] = Depends(get_current_user),
) -> Response:
    """显示登录表单或将已登录用户送回首页。"""
    if user is not None:
        return RedirectResponse("/", status_code=302)
    next_url = _safe_next_url(request.query_params.get("next", ""))
    return _render_login(request, next_url=next_url)


@router.post("/login/", include_in_schema=False)
async def submit_login(request: Request) -> Response:
    """校验登录表单并按账户状态返回提示或建立会话。"""
    form = await request.form()
    if form.get("confirm_kick") == "1":
        return await _confirm_kick_login(request)
    raw_username = form.get("username", "")
    raw_password = form.get("password", "")
    username = raw_username.strip() if isinstance(raw_username, str) else ""
    password = raw_password if isinstance(raw_password, str) else ""
    next_url = _safe_next_url(
        form.get("next", "")
        if isinstance(form.get("next", ""), str)
        else request.query_params.get("next", "")
    )
    client_ip, user_agent = _request_metadata(request)
    if len(password) > 4096:
        password = ""

    result = await run_in_threadpool(
        authenticate_and_log,
        request.app.state.database.session_factory,
        username,
        password,
        client_ip,
        user_agent,
    )
    if result.status == "success" and result.user_id is not None:
        device_id = _device_id(request)
        conflict = await run_in_threadpool(
            find_login_session_conflict,
            request.app.state.database.session_factory,
            result.user_id,
            device_id,
        )
        if conflict is not None:
            request.session.update(
                {
                    "pending_login_user_id": result.user_id,
                    "pending_login_ip": client_ip,
                    "pending_login_agent": user_agent,
                    "pending_device_id": device_id,
                    "pending_login_next": next_url,
                    "conflict_ip": conflict.ip,
                    "conflict_agent": _format_agent(conflict.user_agent),
                }
            )
            return _render_login(
                request,
                username=username[:100],
                next_url=next_url,
                show_conflict=True,
                conflict_ip=conflict.ip,
                conflict_agent=_format_agent(conflict.user_agent),
            )
        finalized = await run_in_threadpool(
            complete_login,
            request.app.state.database.session_factory,
            result.user_id,
            client_ip,
            user_agent,
        )
        if finalized:
            _establish_session(request, result.user_id, device_id, client_ip, user_agent)
            response = RedirectResponse(next_url or "/", status_code=302)
            _set_device_cookie(response, request, device_id)
            return response
        result = LoginResult(status="inactive")

    error_type, error_message = _login_error(result)
    return _render_login(
        request,
        error_type=error_type,
        error_message=error_message,
        username=username[:100],
        next_url=next_url,
        status_code=200,
    )


@router.post("/logout/", include_in_schema=False)
async def logout_page(request: Request) -> Response:
    """撤销当前服务端会话并返回登录页。"""
    logout_user(request)
    return RedirectResponse("/login/", status_code=302)


async def _confirm_kick_login(request: Request) -> Response:
    """确认撤销其他设备会话并完成暂存的登录。"""
    try:
        user_id = int(request.session.get("pending_login_user_id"))
    except (TypeError, ValueError):
        user_id = 0
    ip = str(request.session.get("pending_login_ip", ""))
    user_agent = str(request.session.get("pending_login_agent", ""))
    device_id = str(request.session.get("pending_device_id", ""))
    next_url = _safe_next_url(str(request.session.get("pending_login_next", "")))
    if user_id < 1 or not device_id:
        logout_user(request)
        return _render_login(
            request,
            error_type="auth_failed",
            error_message="确认已过期，请重新登录",
        )
    completed = await run_in_threadpool(
        complete_login,
        request.app.state.database.session_factory,
        user_id,
        ip,
        user_agent,
        True,
    )
    if not completed:
        logout_user(request)
        return _render_login(
            request,
            error_type="user_disabled",
            error_message="用户已停用，请重新登录",
        )
    _establish_session(request, user_id, device_id, ip, user_agent)
    response = RedirectResponse(next_url or "/", status_code=302)
    _set_device_cookie(response, request, device_id)
    return response


@router.get("/profile/", include_in_schema=False)
def profile_page(
    request: Request,
    user: User = Depends(require_authenticated_user),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示当前账户的只读个人资料。"""
    return render_page(
        request,
        "accounts/profile.html",
        context={
            "user": user,
            "joined_at": _format_datetime(user.date_joined),
            "last_login_display": _format_datetime(user.last_login, "从未登录"),
        },
        user=user,
        db_session=db_session,
    )


@router.get("/password/change/", include_in_schema=False)
def password_change_page(
    request: Request,
    user: User = Depends(require_authenticated_user),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示当前账户的修改密码表单。"""
    return _render_password_change(request, user, db_session)


@router.post("/password/change/", include_in_schema=False)
async def submit_password_change(
    request: Request,
    user: User = Depends(require_authenticated_user),
) -> Response:
    """验证修改密码表单并在成功后轮换当前会话。"""
    form = await request.form()
    values = {
        name: form.get(name, "") if isinstance(form.get(name, ""), str) else ""
        for name in ("old_password", "new_password1", "new_password2")
    }
    result = await run_in_threadpool(
        change_password,
        request.app.state.database.session_factory,
        user.id,
        values["old_password"],
        values["new_password1"],
        values["new_password2"],
        _request_metadata(request)[0],
    )
    if result.success:
        logout_user(request)
        login_user(request, SimpleNamespace(id=user.id, is_active=True))
        device_id = _device_id(request)
        client_ip, user_agent = _request_metadata(request)
        _store_session_metadata(request, device_id, client_ip, user_agent)
        response = RedirectResponse("/profile/", status_code=302)
        _set_device_cookie(response, request, device_id)
        return response
    return await run_in_threadpool(
        _render_password_change_with_factory,
        request,
        request.app.state.database.session_factory,
        user.id,
        result.errors,
    )


def _render_login(
    request: Request,
    error_type: str = "",
    error_message: str = "",
    username: str = "",
    next_url: str = "",
    status_code: int = 200,
    show_conflict: bool = False,
    conflict_ip: str = "",
    conflict_agent: str = "",
) -> Response:
    """渲染独立登录页并提供签名 CSRF 表单令牌。"""
    return request.app.state.templates.TemplateResponse(
        name="accounts/login.html",
        request=request,
        context={
            "csrf_token": getattr(request.state, "csrf_token", ""),
            "error_type": error_type,
            "error_message": error_message,
            "username": username,
            "next_url": next_url,
            "show_conflict": show_conflict,
            "conflict_ip": conflict_ip,
            "conflict_agent": conflict_agent,
        },
        status_code=status_code,
    )


def _render_password_change(
    request: Request,
    user: User,
    db_session: Session,
    errors: Optional[dict] = None,
) -> Response:
    """渲染修改密码页且不回填任何密码字段。"""
    return render_page(
        request,
        "accounts/password_change.html",
        context={"errors": errors or {}},
        user=user,
        db_session=db_session,
    )


def _render_password_change_with_factory(
    request: Request,
    session_factory: sessionmaker,
    user_id: int,
    errors: Optional[dict] = None,
) -> Response:
    """在线程池使用独立 Session 渲染改密失败页面。"""
    with session_scope(session_factory) as db_session:
        user = db_session.get(User, user_id)
        if user is None or not user.is_active:
            return RedirectResponse("/login/", status_code=302)
        return _render_password_change(request, user, db_session, errors)


def _request_metadata(request: Request) -> tuple:
    """提取用于登录日志的客户端地址和浏览器标识。"""
    client_ip = request_client_ip(request) or "0.0.0.0"
    return client_ip, request.headers.get("user-agent", "")


def _device_id(request: Request) -> str:
    """读取有效的浏览器设备标识或生成新的随机标识。"""
    value = request.cookies.get("device_id", "")
    if len(value) == 32 and all(char in "0123456789abcdef" for char in value):
        return value
    return uuid.uuid4().hex


def _establish_session(
    request: Request,
    user_id: int,
    device_id: str,
    ip: str,
    user_agent: str,
) -> None:
    """轮换会话键并保存非敏感设备和登录展示信息。"""
    login_user(request, SimpleNamespace(id=user_id, is_active=True))
    _store_session_metadata(request, device_id, ip, user_agent)


def _store_session_metadata(
    request: Request,
    device_id: str,
    ip: str,
    user_agent: str,
) -> None:
    """将设备标识和登录来源保存到服务端会话以支持冲突提示。"""
    request.session["device_id"] = device_id
    request.session["last_login_ip"] = ip[:50]
    request.session["last_login_agent"] = user_agent[:2048]


def _set_device_cookie(response: Response, request: Request, device_id: str) -> None:
    """保存一年期 HttpOnly 设备标识 Cookie。"""
    response.set_cookie(
        "device_id",
        device_id,
        max_age=365 * 24 * 60 * 60,
        httponly=True,
        secure=request.app.state.settings.https_only,
        samesite="lax",
        path="/",
    )


def _format_agent(agent: str) -> str:
    """把浏览器 User-Agent 简化为登录冲突提示。"""
    if not agent:
        return "未知"
    if "Edg/" in agent:
        browser = "Edge"
    elif "Firefox/" in agent:
        browser = "Firefox"
    elif "Chrome/" in agent:
        browser = "Chrome"
    elif "Safari/" in agent:
        browser = "Safari"
    else:
        browser = "未知浏览器"
    if "Windows" in agent:
        system = "Windows"
    elif "Mac OS X" in agent or "macOS" in agent:
        system = "macOS"
    elif "Android" in agent:
        system = "Android"
    elif "iPhone" in agent or "iPad" in agent:
        system = "iOS"
    elif "Linux" in agent:
        system = "Linux"
    else:
        system = ""
    return "{} ({})".format(browser, system) if system else browser


def _login_error(result: LoginResult) -> tuple:
    """将认证服务结果转换为登录页错误类型和中文提示。"""
    if result.status == "inactive":
        return "user_disabled", "用户已锁定，请联系管理员"
    if result.status == "locked":
        return "account_locked", "登录失败次数过多，请等待锁定时间到期或联系管理员解锁。"
    return "auth_failed", "用户名或密码错误"


def _safe_next_url(value: str) -> str:
    """只接受站内绝对路径，拒绝外部地址和协议相对地址。"""
    candidate = value.strip()
    if not candidate or not candidate.startswith("/") or candidate.startswith("//"):
        return ""
    if "\\" in candidate or any(ord(char) < 32 for char in candidate):
        return ""
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return ""
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return ""
    return candidate[:2048]


def _format_datetime(value, empty: str = "") -> str:
    """以项目统一格式显示可选账户时间。"""
    if not value:
        return empty
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
