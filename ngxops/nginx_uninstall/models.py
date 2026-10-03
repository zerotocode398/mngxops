"""保存 Nginx 卸载任务的批次和执行参数快照。"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ngxops.database.base import Base
from ngxops.nodes.models import Node
from ngxops.tasks.models import Task


class NginxUninstallRun(Base):
    """保存单节点卸载批次、安装来源和确认的路径选择。"""

    __tablename__ = "ngxops_nginx_uninstall_runs"
    __table_args__ = (
        Index("ix_ngxops_uninstall_runs_batch_created", "batch_number", "created_at"),
        Index("ix_ngxops_uninstall_runs_node_created", "node_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("ngxops_tasks.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    node_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ngxops_nodes.id", ondelete="SET NULL"), nullable=True
    )
    batch_number: Mapped[str] = mapped_column(String(32), nullable=False)
    node_hostname: Mapped[str] = mapped_column(String(100), nullable=False)
    node_ip: Mapped[str] = mapped_column(String(45), nullable=False)
    install_origin: Mapped[str] = mapped_column(String(20), nullable=False)
    package_manager: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    package_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    resolved_prefix: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    backup_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    work_dir: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    options_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    task: Mapped[Task] = relationship()
    node: Mapped[Optional[Node]] = relationship()
