"""映射配置标签、节点绑定及绑定级版本快照。"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ngxops.accounts.models import User
from ngxops.database.base import Base
from ngxops.nodes.models import Node


class Config(Base):
    """保存配置类型标签及新绑定可复用的默认值。"""

    __tablename__ = "ngxops_configs"
    __table_args__ = (Index("ix_ngxops_configs_updated_at", "updated_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_remote_path: Mapped[str] = mapped_column(
        String(500), nullable=False, default="", server_default=""
    )
    template_content: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual", server_default="manual"
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
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
    bindings: Mapped[List["ConfigBinding"]] = relationship(
        back_populates="config", cascade="all, delete-orphan"
    )


class ConfigBinding(Base):
    """保存一个配置标签在单个节点上的内容、路径和同步状态。"""

    __tablename__ = "ngxops_config_bindings"
    __table_args__ = (
        UniqueConstraint(
            "config_id", "node_id", name="uq_ngxops_config_bindings_config_node"
        ),
        CheckConstraint(
            "current_version >= 1",
            name="ck_ngxops_config_bindings_current_version",
        ),
        CheckConstraint(
            "sync_status IN ('not_synced', 'synced', 'modified', 'orphaned', "
            "'failed', 'marked_deleted')",
            name="ck_ngxops_config_bindings_sync_status",
        ),
        Index("ix_ngxops_config_bindings_node_status", "node_id", "sync_status"),
        Index("ix_ngxops_config_bindings_updated_at", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_id: Mapped[int] = mapped_column(
        ForeignKey("ngxops_configs.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[int] = mapped_column(
        ForeignKey("ngxops_nodes.id", ondelete="CASCADE"), nullable=False
    )
    remote_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    current_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    sync_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_synced", server_default="not_synced"
    )
    synced_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_sync_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_sync_error: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    last_sync_task_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    remote_content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    drift_detected_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual", server_default="manual"
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

    config: Mapped[Config] = relationship(back_populates="bindings")
    node: Mapped[Node] = relationship()
    creator: Mapped[User] = relationship(foreign_keys=[created_by])
    versions: Mapped[List["BindingVersion"]] = relationship(
        back_populates="binding",
        cascade="all, delete-orphan",
        order_by="BindingVersion.version.desc()",
    )


class BindingVersion(Base):
    """保存绑定内容在某一版本号下的不可变快照。"""

    __tablename__ = "ngxops_binding_versions"
    __table_args__ = (
        UniqueConstraint(
            "binding_id",
            "version",
            name="uq_ngxops_binding_versions_binding_version",
        ),
        CheckConstraint("version >= 1", name="ck_ngxops_binding_versions_version"),
        Index("ix_ngxops_binding_versions_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    binding_id: Mapped[int] = mapped_column(
        ForeignKey("ngxops_config_bindings.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    remark: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("auth_user.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    binding: Mapped[ConfigBinding] = relationship(back_populates="versions")
    creator: Mapped[User] = relationship()

    @property
    def content_bytes(self) -> int:
        """返回该版本 UTF-8 正文的字节数。"""
        return len(self.content.encode("utf-8"))

    @property
    def version_label(self) -> str:
        """返回版本列表中使用的日期与版本号标签。"""
        return "{} - V{}".format(self.created_at.strftime("%Y%m%d"), self.version)


class ConfigSyncSetting(Base):
    """保存节点配置发现使用的主配置路径和最后更新人。"""

    __tablename__ = "ngxops_config_sync_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("ngxops_nodes.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    main_conf_path: Mapped[str] = mapped_column(
        String(500), nullable=False, default="", server_default=""
    )
    updated_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("auth_user.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.current_timestamp(),
    )

    node: Mapped[Node] = relationship()
    updater: Mapped[Optional[User]] = relationship()
