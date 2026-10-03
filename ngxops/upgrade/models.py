"""映射平台托管的 Nginx 包与升级参数快照。"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
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
from ngxops.tasks.models import Task


class NginxSourcePackage(Base):
    """保存平台上传的 Nginx 源码包元数据。"""

    __tablename__ = "ngxops_nginx_source_packages"
    __table_args__ = (
        CheckConstraint("file_size >= 0", name="ck_ngxops_source_package_size"),
        UniqueConstraint(
            "version", "created_by", name="uq_ngxops_source_package_owner_version"
        ),
        Index("ix_ngxops_source_packages_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    file_md5: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_official: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("auth_user.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    creator: Mapped[User] = relationship()
    runs: Mapped[list["NginxUpgradeRun"]] = relationship(back_populates="source_package")


class NginxModulePackage(Base):
    """保存第三方 Nginx 模块离线包元数据。"""

    __tablename__ = "ngxops_nginx_module_packages"
    __table_args__ = (
        CheckConstraint("file_size >= 0", name="ck_ngxops_module_package_size"),
        UniqueConstraint(
            "name",
            "version",
            "created_by",
            name="uq_ngxops_module_package_owner_name_version",
        ),
        Index("ix_ngxops_module_packages_created_at", "created_at"),
        Index("ix_ngxops_module_packages_md5", "file_md5"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    file_md5: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[int] = mapped_column(
        ForeignKey("auth_user.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    creator: Mapped[User] = relationship()


class NginxUpgradeRun(Base):
    """保存每个节点升级任务的参数快照与回滚定位信息。"""

    __tablename__ = "ngxops_nginx_upgrade_runs"
    __table_args__ = (
        CheckConstraint(
            "upgrade_mode IN ('upgrade', 'switch_path', 'rollback')",
            name="ck_ngxops_upgrade_runs_mode",
        ),
        CheckConstraint(
            "make_jobs >= 1 AND make_jobs <= 32",
            name="ck_ngxops_upgrade_runs_make_jobs",
        ),
        Index("ix_ngxops_upgrade_runs_batch_created", "batch_number", "created_at"),
        Index("ix_ngxops_upgrade_runs_node_created", "node_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("ngxops_tasks.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    node_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ngxops_nodes.id", ondelete="SET NULL"), nullable=True
    )
    source_package_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ngxops_nginx_source_packages.id", ondelete="SET NULL"), nullable=True
    )
    rollback_of_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ngxops_nginx_upgrade_runs.id", ondelete="SET NULL"), nullable=True
    )
    batch_number: Mapped[str] = mapped_column(String(32), nullable=False)
    node_hostname: Mapped[str] = mapped_column(String(100), nullable=False)
    node_ip: Mapped[str] = mapped_column(String(45), nullable=False)
    source_package_name: Mapped[str] = mapped_column(
        String(100), nullable=False, default=""
    )
    target_version: Mapped[str] = mapped_column(String(50), nullable=False)
    upgrade_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    remote_work_dir: Mapped[str] = mapped_column(String(500), nullable=False)
    make_jobs: Mapped[int] = mapped_column(Integer, nullable=False)
    current_version: Mapped[str] = mapped_column(
        String(50), nullable=False, default=""
    )
    current_configure_opts: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    current_prefix: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    current_binary_path: Mapped[str] = mapped_column(
        String(500), nullable=False, default=""
    )
    target_configure_opts: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    target_prefix: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    added_modules_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    removed_modules_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    third_party_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    backup_binary_path: Mapped[str] = mapped_column(
        String(500), nullable=False, default=""
    )
    phase: Mapped[str] = mapped_column(
        String(40), nullable=False, default="pending", server_default="pending"
    )
    rolled_back_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    task: Mapped[Task] = relationship()
    node: Mapped[Optional[Node]] = relationship()
    source_package: Mapped[Optional[NginxSourcePackage]] = relationship(
        back_populates="runs"
    )
    rollback_of: Mapped[Optional["NginxUpgradeRun"]] = relationship(
        remote_side=[id], foreign_keys=[rollback_of_id]
    )
