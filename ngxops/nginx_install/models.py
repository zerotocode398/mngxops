"""保存 Nginx 全新安装的批次参数和执行快照。"""

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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ngxops.database.base import Base
from ngxops.nodes.models import Node
from ngxops.tasks.models import Task
from ngxops.upgrade.models import NginxSourcePackage


class NginxInstallRun(Base):
    """保存单节点安装参数、批次信息和配置同步结果。"""

    __tablename__ = "ngxops_nginx_install_runs"
    __table_args__ = (
        CheckConstraint("make_jobs BETWEEN 1 AND 32", name="ck_ngxops_install_jobs"),
        CheckConstraint(
            "listen_port BETWEEN 1 AND 65535", name="ck_ngxops_install_listen_port"
        ),
        Index("ix_ngxops_install_runs_batch_created", "batch_number", "created_at"),
        Index("ix_ngxops_install_runs_node_created", "node_id", "created_at"),
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
    batch_number: Mapped[str] = mapped_column(String(32), nullable=False)
    node_hostname: Mapped[str] = mapped_column(String(100), nullable=False)
    node_ip: Mapped[str] = mapped_column(String(45), nullable=False)
    source_package_name: Mapped[str] = mapped_column(
        String(100), nullable=False, default=""
    )
    target_version: Mapped[str] = mapped_column(String(50), nullable=False)
    remote_work_dir: Mapped[str] = mapped_column(String(500), nullable=False)
    target_prefix: Mapped[str] = mapped_column(String(500), nullable=False)
    nginx_user: Mapped[str] = mapped_column(
        String(100), nullable=False, default="root", server_default="root"
    )
    nginx_group: Mapped[str] = mapped_column(
        String(100), nullable=False, default="root", server_default="root"
    )
    target_configure_opts: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    added_modules_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    third_party_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    make_jobs: Mapped[int] = mapped_column(Integer, nullable=False)
    listen_port: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    phase: Mapped[str] = mapped_column(
        String(40), nullable=False, default="pending", server_default="pending"
    )
    sync_ok: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    sync_detail: Mapped[str] = mapped_column(
        String(500), nullable=False, default="", server_default=""
    )
    nginx_path: Mapped[str] = mapped_column(
        String(500), nullable=False, default="", server_default=""
    )
    main_conf_path: Mapped[str] = mapped_column(
        String(500), nullable=False, default="", server_default=""
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    task: Mapped[Task] = relationship()
    node: Mapped[Optional[Node]] = relationship()
    source_package: Mapped[Optional[NginxSourcePackage]] = relationship()
