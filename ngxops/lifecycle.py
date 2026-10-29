"""提供 Nginx 生命周期任务详情共用的批次摘要。"""

from datetime import datetime
from typing import Iterable, Optional

from ngxops.tasks.presentation import format_task_datetime


def source_package_display_name(
    name: str, version: str, stored_file_name: str = ""
) -> str:
    """根据源码包元数据生成不含随机存储名的归档名称。"""
    package_name = str(name or "").strip()
    package_version = str(version or "").strip()
    stored_name = str(stored_file_name or "").lower()
    suffixes = (".tar.gz", ".tgz")
    suffix = next(
        (item for item in suffixes if stored_name.endswith(item)), ".tar.gz"
    )
    existing_suffix = next(
        (item for item in suffixes if package_name.lower().endswith(item)), ""
    )
    if existing_suffix:
        package_name = package_name[: -len(existing_suffix)]
        if not stored_name.endswith(suffixes):
            suffix = existing_suffix
    if not package_name:
        package_name = "nginx"
    if package_version and not package_name.lower().endswith(
        ("-" + package_version.lower(), "_" + package_version.lower())
    ):
        package_name = "{}-{}".format(package_name.rstrip("-_"), package_version)
    return package_name + suffix


def summarize_lifecycle_runs(runs: Iterable[object]) -> dict:
    """汇总单节点运行记录的目标、结果和批次时间。"""
    items = list(runs)
    tasks = [item.task for item in items if getattr(item, "task", None) is not None]
    created_at = _earliest(task.created_at for task in tasks)
    started_at = _earliest(task.started_at for task in tasks)
    finished_at = _latest(task.finished_at for task in tasks)
    return {
        "targets": [
            {
                "task_id": item.task_id,
                "hostname": item.node_hostname,
                "ip": item.node_ip,
                "status": item.task.status,
                "progress": item.task.progress,
            }
            for item in items
        ],
        "success_count": sum(1 for task in tasks if task.status == "success"),
        "fail_count": sum(
            1 for task in tasks if task.status in ("failed", "cancelled")
        ),
        "total_count": len(items),
        "created_at": format_task_datetime(created_at),
        "started_at": format_task_datetime(started_at),
        "finished_at": format_task_datetime(finished_at),
    }


def _earliest(values: Iterable[Optional[datetime]]) -> Optional[datetime]:
    """返回非空时间中的最早值。"""
    return min((value for value in values if value is not None), default=None)


def _latest(values: Iterable[Optional[datetime]]) -> Optional[datetime]:
    """返回非空时间中的最晚值。"""
    return max((value for value in values if value is not None), default=None)
