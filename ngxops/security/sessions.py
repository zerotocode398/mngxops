"""提供可撤销的服务端会话与浏览器 Cookie 管理。"""

import hashlib
import hmac
import json
import logging
import secrets
from collections.abc import Iterator, MutableMapping
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from ngxops.database.session import session_scope
from ngxops.logging_setup import log_exception


logger = logging.getLogger(__name__)
SESSION_COOKIE_NAME = "sessionid"
SESSION_KEY_LENGTH = 43
SESSION_MAX_AGE_SECONDS = 14 * 24 * 60 * 60
CSRF_COOKIE_NAME = "csrftoken"
CSRF_COOKIE_MAX_AGE_SECONDS = 365 * 24 * 60 * 60
PERMISSION_ALERT_KEY = "mngxops_perm_denied"
_MISSING = object()


def consume_permission_alert(session: MutableMapping[str, Any]) -> Optional[Dict[str, str]]:
    """读取并移除一次性无权提示，供 Jinja2 公共布局使用。"""
    value = session.pop(PERMISSION_ALERT_KEY, None)
    if not isinstance(value, dict):
        return None
    title = value.get("title")
    message = value.get("message")
    if not isinstance(title, str) or not isinstance(message, str):
        return None
    return {"title": title, "message": message}


class ServerSession(MutableMapping[str, Any]):
    """保存单个请求可修改的服务端会话数据。"""

    def __init__(
        self,
        session_key: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        invalid_cookie: bool = False,
    ) -> None:
        """初始化会话键、数据和变更状态。"""
        self.session_key = session_key
        self.data = dict(data or {})
        self.old_session_key: Optional[str] = None
        self.modified = False
        self.deleted = False
        self.invalid_cookie = invalid_cookie

    def __getitem__(self, key: str) -> Any:
        """读取会话中的一项数据。"""
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        """写入会话数据并标记会话已修改。"""
        self.data[key] = value
        self.modified = True
        self.deleted = False

    def __delitem__(self, key: str) -> None:
        """删除会话数据并标记会话已修改。"""
        del self.data[key]
        self.modified = True

    def __iter__(self) -> Iterator[str]:
        """返回会话数据键的迭代器。"""
        return iter(self.data)

    def __len__(self) -> int:
        """返回会话数据项数量。"""
        return len(self.data)

    def clear(self) -> None:
        """清空数据并安排删除当前服务端会话。"""
        if self.session_key:
            self.old_session_key = self.session_key
        self.session_key = None
        self.data.clear()
        self.modified = True
        self.deleted = True

    def pop(self, key: str, default: Any = _MISSING) -> Any:
        """删除会话数据并返回原值或默认值。"""
        if key not in self.data:
            if default is _MISSING:
                raise KeyError(key)
            return default
        self.modified = True
        return self.data.pop(key)

    def update(self, other: Any = (), **kwargs: Any) -> None:
        """批量更新会话数据并记录变更。"""
        values = dict(other, **kwargs)
        if values:
            self.data.update(values)
            self.modified = True
            self.deleted = False

    def cycle_key(self) -> None:
        """轮换会话键但保留当前数据以防止会话固定。"""
        if self.session_key:
            self.old_session_key = self.session_key
        self.session_key = None
        self.modified = True
        self.deleted = False


def _load_session(session_factory: sessionmaker, session_key: str) -> Optional[Dict[str, Any]]:
    """读取未过期会话并清理无效记录。"""
    with session_scope(session_factory) as db_session:
        row = db_session.execute(
            text(
                "SELECT session_data, "
                "expires_at > CURRENT_TIMESTAMP AS is_valid "
                "FROM ngxops_sessions WHERE session_key = :session_key"
            ),
            {"session_key": session_key},
        ).first()
        if row is None:
            return None
        if not row[1]:
            db_session.execute(
                text("DELETE FROM ngxops_sessions WHERE session_key = :session_key"),
                {"session_key": session_key},
            )
            db_session.commit()
            return None

        try:
            data = json.loads(row[0])
        except (TypeError, ValueError):
            data = None
        if not isinstance(data, dict):
            db_session.execute(
                text("DELETE FROM ngxops_sessions WHERE session_key = :session_key"),
                {"session_key": session_key},
            )
            db_session.commit()
            return None
        return data


def _save_session(
    session_factory: sessionmaker,
    session: ServerSession,
) -> Optional[str]:
    """删除旧会话并持久化变更后的 JSON 会话数据。"""
    session_key = session.session_key or secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(seconds=SESSION_MAX_AGE_SECONDS)
    expires_at_text = expires_at.strftime("%Y-%m-%d %H:%M:%S.%f")
    user_id = session.data.get("user_id")
    try:
        user_id = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        user_id = None

    with session_scope(session_factory) as db_session:
        with db_session.begin():
            if session.old_session_key:
                db_session.execute(
                    text("DELETE FROM ngxops_sessions WHERE session_key = :session_key"),
                    {"session_key": session.old_session_key},
                )
            if session.deleted or not session.data:
                if session.session_key:
                    db_session.execute(
                        text(
                            "DELETE FROM ngxops_sessions "
                            "WHERE session_key = :session_key"
                        ),
                        {"session_key": session.session_key},
                    )
                return None

            session_data = json.dumps(
                session.data,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            db_session.execute(
                text(
                    "INSERT INTO ngxops_sessions "
                    "(session_key, user_id, session_data, expires_at) "
                    "VALUES (:session_key, :user_id, :session_data, :expires_at) "
                    "ON CONFLICT(session_key) DO UPDATE SET "
                    "user_id = excluded.user_id, "
                    "session_data = excluded.session_data, "
                    "expires_at = excluded.expires_at"
                ),
                {
                    "session_key": session_key,
                    "user_id": user_id,
                    "session_data": session_data,
                    "expires_at": expires_at_text,
                },
            )
    session.session_key = session_key
    session.old_session_key = None
    session.modified = False
    session.deleted = False
    return session_key


def _delete_session(session_factory: sessionmaker, session: ServerSession) -> None:
    """撤销会话当前键和轮换前的旧键。"""
    session_keys = {key for key in (session.session_key, session.old_session_key) if key}
    if not session_keys:
        return
    with session_scope(session_factory) as db_session:
        with db_session.begin():
            for session_key in session_keys:
                db_session.execute(
                    text("DELETE FROM ngxops_sessions WHERE session_key = :session_key"),
                    {"session_key": session_key},
                )


def _is_valid_session_key(session_key: str) -> bool:
    """限制会话查询键为本服务生成的 URL 安全随机格式。"""
    return (
        len(session_key) == SESSION_KEY_LENGTH
        and session_key.isascii()
        and all(character.isalnum() or character in "-_" for character in session_key)
    )


class SessionMiddleware(BaseHTTPMiddleware):
    """加载服务端会话并为浏览器签发 CSRF Cookie。"""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """在请求前加载会话，并在响应前保存变更。"""
        if request.url.path == "/health":
            return await call_next(request)
        settings = request.app.state.settings
        secret_key = request.app.state.secret_key
        if not secret_key:
            return JSONResponse(
                status_code=503,
                content={"detail": "安全密钥尚未初始化"},
            )

        raw_session_key = request.cookies.get(SESSION_COOKIE_NAME)
        valid_session_key = bool(
            raw_session_key and _is_valid_session_key(raw_session_key)
        )
        session_factory = request.app.state.database.session_factory
        try:
            session_data = None
            if valid_session_key:
                session_data = await run_in_threadpool(
                    _load_session,
                    session_factory,
                    raw_session_key,
                )
        except SQLAlchemyError as exc:
            log_exception(logger, "会话数据读取失败", exc)
            return JSONResponse(
                status_code=503,
                content={"detail": "会话服务暂不可用"},
            )

        valid_session_data = bool(session_data)
        session = ServerSession(
            session_key=raw_session_key if valid_session_data else None,
            data=session_data,
            invalid_cookie=bool(raw_session_key and not valid_session_data),
        )
        request.scope["session"] = session
        csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME, "")
        csrf_token = csrf_cookie if _valid_csrf_cookie(csrf_cookie, secret_key) else ""
        if not csrf_token:
            csrf_token = _new_csrf_token(secret_key)
        request.state.csrf_token = csrf_token

        try:
            response = await call_next(request)
            modified = session.modified or session.deleted
            if session.modified or session.deleted:
                if session.deleted and not session.data:
                    await run_in_threadpool(_delete_session, session_factory, session)
                    session_key = None
                else:
                    session_key = await run_in_threadpool(
                        _save_session,
                        session_factory,
                        session,
                    )
            else:
                session_key = session.session_key
        except SQLAlchemyError as exc:
            log_exception(logger, "会话数据写入失败", exc)
            return JSONResponse(
                status_code=503,
                content={"detail": "会话服务暂不可用"},
            )

        secure = settings.https_only
        if session_key and modified:
            response.set_cookie(
                SESSION_COOKIE_NAME,
                session_key,
                max_age=SESSION_MAX_AGE_SECONDS,
                httponly=True,
                secure=secure,
                samesite="lax",
                path="/",
            )
        elif not session_key and (
            raw_session_key or session.invalid_cookie or session.deleted
        ):
            response.delete_cookie(
                SESSION_COOKIE_NAME,
                httponly=True,
                secure=secure,
                samesite="lax",
                path="/",
            )

        if not _valid_csrf_cookie(csrf_cookie, secret_key):
            response.set_cookie(
                CSRF_COOKIE_NAME,
                csrf_token,
                max_age=CSRF_COOKIE_MAX_AGE_SECONDS,
                httponly=False,
                secure=secure,
                samesite="lax",
                path="/",
            )
        return response


def _csrf_signature(nonce: str, secret_key: str) -> str:
    """计算 CSRF nonce 的服务端 HMAC 签名。"""
    return hmac.new(
        secret_key.encode("utf-8"),
        ("csrf:" + nonce).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _new_csrf_token(secret_key: str) -> str:
    """生成随机且不可伪造的 CSRF Cookie 值。"""
    nonce = secrets.token_urlsafe(32)
    return nonce + "." + _csrf_signature(nonce, secret_key)


def _valid_csrf_cookie(token: str, secret_key: str) -> bool:
    """验证 CSRF Cookie 的 HMAC 签名。"""
    if len(token) > 256:
        return False
    try:
        nonce, signature = token.rsplit(".", 1)
    except ValueError:
        return False
    return bool(nonce) and hmac.compare_digest(
        signature,
        _csrf_signature(nonce, secret_key),
    )
