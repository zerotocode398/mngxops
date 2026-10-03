"""映射保留操作人快照的操作审计日志。"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ngxops.database.base import Base


class AuditLog(Base):
    """保存已提交的重要业务变更及异步任务关联。"""

    __tablename__ = "ngxops_audit_logs"
    __table_args__ = (
        Index("ix_ngxops_audit_logs_created_at", "created_at"),
        Index("ix_ngxops_audit_logs_module_created_at", "module", "created_at"),
        Index("ix_ngxops_audit_logs_result_created_at", "result", "created_at"),
        Index("ix_ngxops_audit_logs_task_id", "task_id"),
        Index("ix_ngxops_audit_logs_source_batch", "source_batch"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("auth_user.id", ondelete="SET NULL"), nullable=True
    )
    username: Mapped[str] = mapped_column(String(150), nullable=False, default="")
    module: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    ip: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    result: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_batch: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
