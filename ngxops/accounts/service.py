"""实现账户登录、连续失败锁定和密码修改事务。"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy import case, delete, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from ngxops.audit.service import prepare_audit_session
from ngxops.accounts.models import LoginFailureState, LoginLog, User
from ngxops.accounts.passwords import (
    burn_unknown_user_password_check,
    check_password,
    make_password,
    validate_new_password,
)
from ngxops.database.session import session_scope
from ngxops.settings.service import read_setting


DEFAULT_FAIL_COUNT = 5
DEFAULT_LOCK_MINUTES = 15
MAX_LOGIN_NAME_LENGTH = 150
MAX_LOGIN_IP_LENGTH = 50
MAX_USER_AGENT_LENGTH = 2048


@dataclass(frozen=True)
class LoginResult:
    """描述登录结果及成功账户标识。"""

    status: str
    user_id: Optional[int] = None


@dataclass(frozen=True)
class PasswordChangeResult:
    """描述密码修改是否成功及字段校验错误。"""

    success: bool
    errors: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LoginSessionConflict:
    """描述当前账户已有的异设备会话。"""

    ip: str
    user_agent: str


def authenticate_and_log(
    session_factory: sessionmaker,
    username: str,
    password: str,
    ip: str,
    user_agent: str,
) -> LoginResult:
    """验证账户凭证并原子写入登录状态、锁定和登录日志。"""
    snapshot = _load_login_snapshot(session_factory, username)
    if snapshot is None:
        burn_unknown_user_password_check(password)
        password_valid = False
    elif not snapshot[2]:
        password_valid = False
    else:
        password_valid = check_password(password, snapshot[1])

    with session_scope(session_factory) as db_session:
        db_session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        try:
            fail_count = read_setting(
                db_session, "auth.login_fail_lock_count", DEFAULT_FAIL_COUNT
            )
            lock_minutes = read_setting(
                db_session, "auth.login_fail_lock_minutes", DEFAULT_LOCK_MINUTES
            )
            result = _record_login_attempt(
                db_session,
                username,
                password,
                password_valid,
                snapshot,
                ip,
                user_agent,
                fail_count,
                lock_minutes,
            )
            db_session.commit()
            return result
        except Exception:
            db_session.rollback()
            raise


def _load_login_snapshot(
    session_factory: sessionmaker,
    username: str,
) -> Optional[tuple]:
    """读取登录校验所需的账户标识、密码摘要和启用状态。"""
    with session_scope(session_factory) as db_session:
        user = db_session.scalars(
            select(User).where(User.username == username)
        ).one_or_none()
        if user is None:
            return None
        return user.id, user.password, user.is_active


def _record_login_attempt(
    db_session: Session,
    username: str,
    password: str,
    password_valid: bool,
    snapshot: Optional[tuple],
    ip: str,
    user_agent: str,
    fail_count: int,
    lock_minutes: int,
) -> LoginResult:
    """在已取得 SQLite 写锁后校验最新账户状态并写入结果。"""
    user = db_session.scalars(
        select(User).where(User.username == username)
    ).one_or_none()
    now = datetime.utcnow()
    status = "invalid"
    reason = "user_not_found"
    user_id = None

    if user is not None:
        user_id = user.id
        if not user.is_active:
            status = "inactive"
            reason = "user_inactive"
        else:
            state = db_session.get(LoginFailureState, user.id)
            if (
                state is not None
                and state.login_locked_until is not None
                and state.login_locked_until > now
            ):
                status = "locked"
                reason = "user_locked"
            else:
                valid = password_valid
                if snapshot is None or snapshot[0] != user.id:
                    valid = check_password(password, user.password)
                elif snapshot[1] != user.password:
                    valid = check_password(password, user.password)
                if valid:
                    clear_login_fail_lock(db_session, user.id)
                    return LoginResult(status="success", user_id=user.id)
                status = "invalid"
                reason = "wrong_password"
                failed_count = record_login_failure(
                    db_session, user.id, now, fail_count, lock_minutes
                )
                if failed_count >= fail_count:
                    status = "locked"

    db_session.add(
        _make_login_log(username, ip, user_agent, "failed", reason)
    )
    return LoginResult(status=status, user_id=user_id)


def find_login_session_conflict(
    session_factory: sessionmaker,
    user_id: int,
    device_id: str,
) -> Optional[LoginSessionConflict]:
    """清理同设备旧会话并返回仍有效的异设备会话。"""
    with session_scope(session_factory) as db_session:
        db_session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        rows = db_session.execute(
            text(
                "SELECT session_key, session_data FROM ngxops_sessions "
                "WHERE user_id = :user_id AND expires_at > CURRENT_TIMESTAMP"
            ),
            {"user_id": user_id},
        ).all()
        conflict = None
        for session_key, session_data in rows:
            try:
                data = json.loads(session_data)
            except (TypeError, ValueError):
                data = {}
            if data.get("device_id") == device_id:
                db_session.execute(
                    text(
                        "DELETE FROM ngxops_sessions "
                        "WHERE session_key = :session_key"
                    ),
                    {"session_key": session_key},
                )
                continue
            if conflict is None:
                conflict = LoginSessionConflict(
                    ip=str(data.get("last_login_ip") or "未知")[:MAX_LOGIN_IP_LENGTH],
                    user_agent=str(data.get("last_login_agent") or ""),
                )
        db_session.commit()
        return conflict


def complete_login(
    session_factory: sessionmaker,
    user_id: int,
    ip: str,
    user_agent: str,
    revoke_existing_sessions: bool = False,
) -> bool:
    """记录已确认登录并可选地撤销该账户现存的其他会话。"""
    with session_scope(session_factory) as db_session:
        db_session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        try:
            user = db_session.get(User, user_id)
            if user is None or not user.is_active:
                db_session.rollback()
                return False
            if revoke_existing_sessions:
                db_session.execute(
                    text("DELETE FROM ngxops_sessions WHERE user_id = :user_id"),
                    {"user_id": user_id},
                )
            user.last_login = datetime.utcnow()
            db_session.add(
                _make_login_log(user.username, ip, user_agent, "success", "")
            )
            db_session.commit()
            return True
        except Exception:
            db_session.rollback()
            raise


def _make_login_log(
    username: str,
    ip: str,
    user_agent: str,
    status: str,
    fail_reason: str,
) -> LoginLog:
    """创建裁剪长度且不包含凭证明文的登录日志记录。"""
    return LoginLog(
        username=username[:MAX_LOGIN_NAME_LENGTH],
        ip=ip[:MAX_LOGIN_IP_LENGTH],
        user_agent=user_agent[:MAX_USER_AGENT_LENGTH],
        status=status,
        fail_reason=fail_reason,
    )


def record_login_failure(
    db_session: Session,
    user_id: int,
    now: Optional[datetime] = None,
    fail_threshold: int = DEFAULT_FAIL_COUNT,
    lock_minutes: int = DEFAULT_LOCK_MINUTES,
) -> int:
    """增加连续失败次数并在达到阈值时写入临时锁定截止时间。"""
    current_time = now or datetime.utcnow()
    locked_until = current_time + timedelta(minutes=lock_minutes)
    expired_lock = (
        LoginFailureState.login_locked_until.is_not(None)
        & (LoginFailureState.login_locked_until <= current_time)
    )
    next_count = case(
        (expired_lock, 1),
        else_=LoginFailureState.failed_login_count + 1,
    )
    next_locked_until = case(
        (expired_lock, None),
        (
            LoginFailureState.failed_login_count + 1 >= fail_threshold,
            locked_until,
        ),
        else_=None,
    )
    statement = sqlite_insert(LoginFailureState).values(
        user_id=user_id,
        failed_login_count=1,
        login_locked_until=None,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[LoginFailureState.user_id],
        set_={
            "failed_login_count": next_count,
            "login_locked_until": next_locked_until,
        },
    )
    db_session.execute(statement)
    state = db_session.get(LoginFailureState, user_id, populate_existing=True)
    return state.failed_login_count if state is not None else 0


def clear_login_fail_lock(db_session: Session, user_id: int) -> None:
    """清除指定用户的连续失败次数和临时锁定状态。"""
    db_session.execute(
        delete(LoginFailureState).where(LoginFailureState.user_id == user_id)
    )


def change_password(
    session_factory: sessionmaker,
    user_id: int,
    old_password: str,
    new_password: str,
    confirmation: str,
    client_ip: str = "",
) -> PasswordChangeResult:
    """校验旧密码和新密码并撤销该用户所有已有服务端会话。"""
    with session_scope(session_factory) as db_session:
        user = db_session.get(User, user_id)
        if user is None or not user.is_active:
            return PasswordChangeResult(
                success=False,
                errors={"__all__": "登录状态已失效，请重新登录。"},
            )
        current_hash = user.password
        username = user.username

    errors = _validate_password_change(
        old_password,
        new_password,
        confirmation,
        current_hash,
        username,
    )
    if errors:
        return PasswordChangeResult(success=False, errors=errors)
    new_hash = make_password(new_password)

    with session_scope(session_factory) as db_session:
        prepare_audit_session(db_session, actor_id=user_id, ip=client_ip)
        db_session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        try:
            user = db_session.get(User, user_id)
            if user is None or not user.is_active:
                db_session.rollback()
                return PasswordChangeResult(
                    success=False,
                    errors={"__all__": "登录状态已失效，请重新登录。"},
                )
            if user.password != current_hash and not check_password(
                old_password, user.password
            ):
                db_session.rollback()
                return PasswordChangeResult(
                    success=False,
                    errors={"old_password": "旧密码不正确。"},
                )
            user.password = new_hash
            clear_login_fail_lock(db_session, user.id)
            db_session.execute(
                text("DELETE FROM ngxops_sessions WHERE user_id = :user_id"),
                {"user_id": user.id},
            )
            db_session.commit()
            return PasswordChangeResult(success=True)
        except Exception:
            db_session.rollback()
            raise


def _validate_password_change(
    old_password: str,
    new_password: str,
    confirmation: str,
    current_hash: str,
    username: str,
) -> Dict[str, str]:
    """返回旧密码、新密码一致性和强度校验错误。"""
    errors = {}
    if not old_password:
        errors["old_password"] = "请输入旧密码。"
    elif not check_password(old_password, current_hash):
        errors["old_password"] = "旧密码不正确。"
    if not new_password:
        errors["new_password1"] = "请输入新密码。"
    else:
        validation_error = validate_new_password(new_password, username)
        if validation_error:
            errors["new_password1"] = validation_error
    if not confirmation:
        errors["new_password2"] = "请再次输入新密码。"
    elif new_password != confirmation:
        errors["new_password2"] = "两次输入的新密码不一致。"
    return errors
