"""定义账户认证与权限模块共用的用户身份模型。"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ngxops.database.base import Base


class User(Base):
    """映射与原项目 Django User 核心字段兼容的账户身份。"""

    __tablename__ = "auth_user"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    password: Mapped[str] = mapped_column(String(128), nullable=False)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_superuser: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    first_name: Mapped[str] = mapped_column(
        String(150), nullable=False, default="", server_default=""
    )
    last_name: Mapped[str] = mapped_column(
        String(150), nullable=False, default="", server_default=""
    )
    email: Mapped[str] = mapped_column(
        String(254), nullable=False, default="", server_default=""
    )
    is_staff: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    date_joined: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    @property
    def is_authenticated(self) -> bool:
        """表明该对象代表已经从数据库加载的账户。"""
        return True

    @property
    def is_anonymous(self) -> bool:
        """表明该对象不是匿名账户。"""
        return False

    def __repr__(self) -> str:
        """返回不包含密码摘要的账户标识。"""
        return "User(id={!r}, username={!r})".format(self.id, self.username)


class LoginFailureState(Base):
    """保存单个账户连续失败次数和临时锁定截止时间。"""

    __tablename__ = "ngxops_login_failure_states"
    __table_args__ = (
        CheckConstraint(
            "failed_login_count >= 0",
            name="ck_ngxops_login_failure_count_nonnegative",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("auth_user.id", ondelete="CASCADE"), primary_key=True
    )
    failed_login_count: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )
    login_locked_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )


class LoginLog(Base):
    """记录成功和失败的登录尝试，不保存提交的密码。"""

    __tablename__ = "ngxops_login_logs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'failed')",
            name="ck_ngxops_login_log_status",
        ),
        CheckConstraint(
            "fail_reason IN ('', 'user_not_found', 'wrong_password', "
            "'user_locked', 'user_inactive')",
            name="ck_ngxops_login_log_reason",
        ),
        Index("ix_ngxops_login_logs_created_at", "created_at"),
        Index("ix_ngxops_login_logs_username", "username"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(150), nullable=False)
    ip: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    fail_reason: Mapped[str] = mapped_column(
        String(50), nullable=False, default="", server_default=""
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
