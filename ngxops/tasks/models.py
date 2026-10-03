"""映射统一异步任务与追加式任务日志。"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
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


ACTIVE_STATUSES = ("pending", "running")
TERMINAL_STATUSES = ("success", "failed", "cancelled")
OPERATION_TYPES = (
    "release_publish",
    "release_rollback",
    "credential_enable_test",
    "node_ssh_test",
    "node_batch_test",
    "node_system_info",
    "node_nginx_version",
    "config_batch_sync",
    "config_discover",
    "config_drift_check",
    "config_glob_preview",
    "nginx_upgrade",
    "nginx_rollback",
    "nginx_service_control",
    "nginx_install",
    "nginx_uninstall",
    "other",
)


class Task(Base):
    """保存任务状态、进度、结果摘要、目标和触发人。"""

    __tablename__ = "ngxops_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed', 'cancelled')",
            name="ck_ngxops_tasks_status",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_ngxops_tasks_progress",
        ),
        Index("ix_ngxops_tasks_created_at", "created_at"),
        Index("ix_ngxops_tasks_status_created_at", "status", "created_at"),
        Index(
            "ix_ngxops_tasks_operation_created_at",
            "operation_type",
            "created_at",
        ),
        Index("ix_ngxops_tasks_source_batch", "source_batch"),
        Index("ix_ngxops_tasks_subject", "subject_type", "subject_id", "created_at"),
        Index(
            "ix_ngxops_tasks_trigger_user_created_at",
            "trigger_user_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="other", server_default="other"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    result_tree_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    source_batch: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    target_hostnames: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    target_ips: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    target_configs: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    subject_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="", server_default=""
    )
    subject_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    trigger_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("auth_user.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.current_timestamp(),
    )
    logs: Mapped[list["TaskLog"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskLog(Base):
    """保存与任务关联、按 ID 游标读取的日志行。"""

    __tablename__ = "ngxops_task_logs"
    __table_args__ = (Index("ix_ngxops_task_logs_task_id_id", "task_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("ngxops_tasks.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[str] = mapped_column(
        String(10), nullable=False, default="info", server_default="info"
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.current_timestamp(),
    )
    task: Mapped[Task] = relationship(back_populates="logs")
