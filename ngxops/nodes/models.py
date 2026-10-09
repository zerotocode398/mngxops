"""映射节点、节点组及节点远程配置路径。"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ngxops.accounts.models import User
from ngxops.credentials.models import Credential
from ngxops.database.base import Base


node_group_members = Table(
    "ngxops_node_group_members",
    Base.metadata,
    Column(
        "node_id",
        ForeignKey("ngxops_nodes.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "group_id",
        ForeignKey("ngxops_node_groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class NodeGroup(Base):
    """保存唯一命名的节点分组。"""

    __tablename__ = "ngxops_node_groups"
    __table_args__ = (Index("ix_ngxops_node_groups_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
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
    nodes: Mapped[List["Node"]] = relationship(
        secondary=node_group_members, back_populates="groups"
    )


class Node(Base):
    """保存 SSH 目标、资产状态与历史关联所需的稳定节点主键。"""

    __tablename__ = "ngxops_nodes"
    __table_args__ = (
        CheckConstraint("port >= 1 AND port <= 65535", name="ck_ngxops_nodes_port"),
        CheckConstraint(
            "environment IN ('dev', 'test', 'prod')",
            name="ck_ngxops_nodes_environment",
        ),
        CheckConstraint(
            "status IN ('online', 'offline', 'unknown')",
            name="ck_ngxops_nodes_status",
        ),
        Index("ix_ngxops_nodes_is_deleted", "is_deleted"),
        Index("ix_ngxops_nodes_status_created_at", "status", "created_at"),
        Index("ix_ngxops_nodes_environment", "environment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hostname: Mapped[str] = mapped_column(String(100), nullable=False)
    ip: Mapped[str] = mapped_column(String(45), nullable=False, unique=True)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=22, server_default="22")
    credential_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ngxops_credentials.id", ondelete="SET NULL"), nullable=True
    )
    environment: Mapped[str] = mapped_column(
        String(20), nullable=False, default="dev", server_default="dev"
    )
    nginx_version: Mapped[str] = mapped_column(
        String(50), nullable=False, default="", server_default=""
    )
    nginx_path: Mapped[str] = mapped_column(
        String(255), nullable=False, default="/usr/sbin/nginx", server_default="/usr/sbin/nginx"
    )
    nginx_available: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    last_nginx_probe_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown", server_default="unknown"
    )
    last_probe_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deleted_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("auth_user.id", ondelete="SET NULL"), nullable=True
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
    credential: Mapped[Optional[Credential]] = relationship()
    creator: Mapped[User] = relationship(foreign_keys=[created_by])
    deleter: Mapped[Optional[User]] = relationship(foreign_keys=[deleted_by])
    groups: Mapped[List[NodeGroup]] = relationship(
        secondary=node_group_members, back_populates="nodes"
    )
    sync_setting: Mapped[Optional["NodeSyncSetting"]] = relationship(
        back_populates="node", cascade="all, delete-orphan", uselist=False
    )

    def soft_delete(self, user_id: int) -> None:
        """逻辑删除节点并保留其 IP 和历史关联。"""
        self.is_deleted = True
        self.deleted_at = datetime.utcnow()
        self.deleted_by = user_id
        self.status = "unknown"


class NodeSyncSetting(Base):
    """保存节点资产及配置发现共用的 Nginx 主配置路径。"""

    __tablename__ = "ngxops_node_sync_settings"

    node_id: Mapped[int] = mapped_column(
        ForeignKey("ngxops_nodes.id", ondelete="CASCADE"), primary_key=True
    )
    main_conf_path: Mapped[str] = mapped_column(
        String(500), nullable=False, default="/etc/nginx/nginx.conf", server_default="/etc/nginx/nginx.conf"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.current_timestamp(),
    )
    node: Mapped[Node] = relationship(back_populates="sync_setting")
