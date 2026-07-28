"""历史数据保留清理：按系统设置天数删除过期记录"""

import logging
from datetime import timedelta

from django.utils import timezone

from utils.setting_service import get_setting

logger = logging.getLogger(__name__)

# 进行中任务不清理
_ACTIVE_STATUSES = ("pending", "running")

_RETENTION_KEYS = {
    "task_center": "system.retention_task_center_days",
    "release_history": "system.retention_release_history_days",
    "audit_log": "system.retention_audit_log_days",
    "login_log": "system.retention_login_log_days",
}


def _retention_days(key):
    """读取保留天数；非法值按 0（不清理）处理"""
    try:
        return max(0, int(get_setting(key, "90") or 0))
    except (TypeError, ValueError):
        return 0


def purge_expired_data():
    """
    按系统设置清理过期历史数据。
    days<=0 表示不清理该类；pending/running 任务跳过。
    返回各类删除条数字典。
    """
    from apps.audit.models import AuditLog, LoginLog
    from apps.releases.models import ReleaseTask, TaskCenterTask

    now = timezone.now()
    result = {
        "task_center": 0,
        "release_history": 0,
        "audit_log": 0,
        "login_log": 0,
    }

    days = _retention_days(_RETENTION_KEYS["task_center"])
    if days > 0:
        cutoff = now - timedelta(days=days)
        deleted, _ = (
            TaskCenterTask.objects.filter(created_at__lt=cutoff)
            .exclude(status__in=_ACTIVE_STATUSES)
            .delete()
        )
        result["task_center"] = deleted

    days = _retention_days(_RETENTION_KEYS["release_history"])
    if days > 0:
        cutoff = now - timedelta(days=days)
        deleted, _ = (
            ReleaseTask.objects.filter(created_at__lt=cutoff)
            .exclude(status__in=_ACTIVE_STATUSES)
            .delete()
        )
        result["release_history"] = deleted

    days = _retention_days(_RETENTION_KEYS["audit_log"])
    if days > 0:
        cutoff = now - timedelta(days=days)
        deleted, _ = AuditLog.objects.filter(created_at__lt=cutoff).delete()
        result["audit_log"] = deleted

    days = _retention_days(_RETENTION_KEYS["login_log"])
    if days > 0:
        cutoff = now - timedelta(days=days)
        deleted, _ = LoginLog.objects.filter(created_at__lt=cutoff).delete()
        result["login_log"] = deleted

    total = sum(result.values())
    if total:
        logger.info(
            "数据保留清理完成: task_center=%s release_history=%s audit_log=%s login_log=%s",
            result["task_center"],
            result["release_history"],
            result["audit_log"],
            result["login_log"],
        )
    return result


def maybe_run_daily_purge():
    """
    每日最多自动执行一次清理（cache 日期锁）。
    返回 True 表示本次触发了清理线程，False 表示今日已跑过或跳过。
    """
    from django.core.cache import cache
    import threading

    today = timezone.localdate().isoformat()
    cache_key = "mngxops_data_purge_date"
    if cache.get(cache_key) == today:
        return False

    # 先占位，避免并发请求重复拉起
    if not cache.add(f"{cache_key}:lock", today, timeout=60):
        return False
    cache.set(cache_key, today, timeout=60 * 60 * 36)

    def _run():
        try:
            purge_expired_data()
        except Exception:
            logger.exception("每日数据保留清理失败")
        finally:
            cache.delete(f"{cache_key}:lock")

    threading.Thread(target=_run, daemon=True).start()
    return True
