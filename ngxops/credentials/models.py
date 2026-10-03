"""映射加密保存的 SSH 凭证。"""

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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ngxops.accounts.models import User
from ngxops.credentials.crypto import decrypt_secret
from ngxops.database.base import Base


class Credential(Base):
    """保存 SSH 认证信息及最近一次连通性测试摘要。"""

    __tablename__ = "ngxops_credentials"
    __table_args__ = (
        UniqueConstraint("name", "created_by", name="uq_ngxops_credentials_owner_name"),
        CheckConstraint(
            "auth_type IN ('password', 'key')",
            name="ck_ngxops_credentials_auth_type",
        ),
        CheckConstraint(
            "last_test_result IN ('success', 'partial', 'failed', 'unknown')",
            name="ck_ngxops_credentials_last_test_result",
        ),
        Index("ix_ngxops_credentials_created_at", "created_at"),
        Index("ix_ngxops_credentials_enabled_name", "is_enabled", "name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    auth_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="password", server_default="password"
    )
    password: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    private_key: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    last_test_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_test_result: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown", server_default="unknown"
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("auth_user.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.current_timestamp(),
    )
    creator: Mapped[User] = relationship()

    def get_password(self, key: bytes) -> str:
        """返回解密后的 SSH 密码。"""
        return decrypt_secret(key, self.password)

    def get_private_key(self, key: bytes) -> str:
        """返回解密后的 SSH 私钥。"""
        return decrypt_secret(key, self.private_key)
