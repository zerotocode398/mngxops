"""提供本地账户初始化与管理员密码重置操作。"""

import re
from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from ngxops.accounts.models import User
from ngxops.accounts.passwords import make_password, validate_new_password
from ngxops.accounts.service import clear_login_fail_lock
from ngxops.database.session import session_scope


_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,150}$")


def validate_username(username: str) -> Optional[str]:
    """返回登录用户名的格式错误，格式有效时返回空值。"""
    if not _USERNAME_PATTERN.fullmatch(username):
        return (
            "Username must use ASCII letters, numbers, underscores, or hyphens "
            "(1-150 characters)."
        )
    return None


def create_first_superuser(
    session_factory: sessionmaker,
    username: str,
    password: str,
) -> None:
    """仅在已迁移且没有账户时创建首个超级管理员。"""
    username_error = validate_username(username)
    if username_error:
        raise ValueError(username_error)
    password_error = validate_new_password(password, username)
    if password_error:
        raise ValueError(password_error)

    with session_scope(session_factory) as db_session:
        db_session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        try:
            applied_versions = db_session.execute(
                text("SELECT version FROM ngxops_schema_migrations")
            ).scalars().all()
        except SQLAlchemyError as exc:
            db_session.rollback()
            raise ValueError(
                "Database migrations are incomplete. Run 'database init' first."
            ) from exc
        try:
            if not {1, 2, 3}.issubset(set(applied_versions)):
                raise ValueError(
                    "Database migrations are incomplete. Run 'database init' first."
                )
            if db_session.scalar(select(func.count()).select_from(User)):
                raise ValueError(
                    "An account already exists; first-admin creation is disabled."
                )
            db_session.add(
                User(
                    username=username,
                    password=make_password(password),
                    is_superuser=True,
                    is_staff=True,
                    is_active=True,
                )
            )
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise


def reset_superuser_password(
    session_factory: sessionmaker,
    username: str,
    password: str,
) -> None:
    """重置指定超级管理员密码并撤销其旧会话。"""
    username_error = validate_username(username)
    if username_error:
        raise ValueError(username_error)
    password_error = validate_new_password(password, username)
    if password_error:
        raise ValueError(password_error)

    with session_scope(session_factory) as db_session:
        db_session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        try:
            applied_versions = db_session.execute(
                text("SELECT version FROM ngxops_schema_migrations")
            ).scalars().all()
        except SQLAlchemyError as exc:
            db_session.rollback()
            raise ValueError(
                "Database migrations are incomplete. Run 'database upgrade' first."
            ) from exc
        if not {1, 2, 3}.issubset(set(applied_versions)):
            db_session.rollback()
            raise ValueError(
                "Database migrations are incomplete. Run 'database upgrade' first."
            )

        user = db_session.scalars(
            select(User).where(User.username == username)
        ).one_or_none()
        if user is None:
            db_session.rollback()
            raise ValueError("The requested account does not exist.")
        if not user.is_superuser:
            db_session.rollback()
            raise ValueError("Password reset is limited to superuser accounts.")

        user.password = make_password(password)
        clear_login_fail_lock(db_session, user.id)
        db_session.execute(
            text("DELETE FROM ngxops_sessions WHERE user_id = :user_id"),
            {"user_id": user.id},
        )
        db_session.commit()
