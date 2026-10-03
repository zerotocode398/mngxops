"""提供系统设置初始化、校验、读取和数据保留清理。"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from ngxops.accounts.models import LoginLog
from ngxops.audit.models import AuditLog
from ngxops.database.session import session_scope
from ngxops.logging_setup import log_exception
from ngxops.settings.models import PRESET_SETTINGS, SystemSetting, preset_by_key
from ngxops.tasks.models import ACTIVE_STATUSES, Task


logger = logging.getLogger(__name__)
_PURGE_LOCK = threading.Lock()
_LAST_PURGE_DATE = None
_RELEASE_TYPES = ("release_publish", "release_rollback")
_UPGRADE_TYPES = ("nginx_upgrade", "nginx_rollback")
_UPGRADE_ACTIVE_PHASES = (
    "pending", "fetching_config", "uploading_package", "downloading_modules",
    "configuring", "compiling", "backing_up", "replacing_binary", "upgrading",
    "verifying",
)


def initialize_defaults(session: Session) -> None:
    """补齐预置设置并同步展示元数据，不覆盖已有值。"""
    keys = [item["key"] for item in PRESET_SETTINGS]
    existing = {
        setting.key: setting
        for setting in session.scalars(
            select(SystemSetting).where(SystemSetting.key.in_(keys))
        ).all()
    }
    missing = [item for item in PRESET_SETTINGS if item["key"] not in existing]
    if missing:
        statement = sqlite_insert(SystemSetting).values([
            {
                "key": item["key"],
                "value": item["value"],
                "type": item["type"],
                "group": item["group"],
                "label": item["label"],
                "description": item.get("description", ""),
                "placeholder": item.get("placeholder", ""),
                "options": item.get("options", ""),
                "is_required": 1 if item.get("is_required", True) else 0,
                "sort_order": item.get("sort_order", 0),
            }
            for item in missing
        ]).on_conflict_do_nothing(index_elements=["key"])
        session.execute(statement)
    for item in PRESET_SETTINGS:
        setting = existing.get(item["key"])
        if setting is None:
            setting = session.scalar(
                select(SystemSetting).where(SystemSetting.key == item["key"])
            )
        if setting is None:
            continue
        setting.value_type = item["type"]
        setting.group_name = item["group"]
        setting.label = item["label"]
        setting.description = item.get("description", "")
        setting.placeholder = item.get("placeholder", "")
        setting.options = item.get("options", "")
        setting.is_required = 1 if item.get("is_required", True) else 0
        setting.sort_order = item.get("sort_order", 0)


def read_setting(session: Session, key: str, default=None):
    """读取设置值并按预置类型转换；未初始化时返回调用方默认值。"""
    try:
        setting = session.scalar(
            select(SystemSetting).where(SystemSetting.key == key)
        )
    except OperationalError as exc:
        if "no such table: ngxops_system_settings" not in str(exc).lower():
            raise
        session.rollback()
        return default
    if setting is None:
        preset = preset_by_key().get(key)
        if preset is None:
            return default
        raw_value = preset["value"]
        value_type = preset["type"]
    else:
        raw_value = setting.value
        value_type = setting.value_type
    if value_type == "integer":
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return default
        preset = preset_by_key().get(key, {})
        if value < preset.get("min_value", value) or value > preset.get("max_value", value):
            return default
        return value
    if value_type == "boolean":
        return str(raw_value).lower() in ("true", "1", "yes", "on")
    return raw_value


def validate_setting_value(key: str, value) -> str:
    """校验预置设置并返回数据库保存的字符串值。"""
    item = preset_by_key().get(key)
    if item is None:
        raise ValueError("包含不支持的设置项")
    label = item["label"]
    if item["type"] == "integer":
        text = str(value).strip()
        if not text:
            raise ValueError("「{}」不能为空".format(label))
        try:
            number = int(text)
        except (TypeError, ValueError):
            raise ValueError("「{}」必须是整数".format(label))
        minimum = item.get("min_value")
        maximum = item.get("max_value")
        if minimum is not None and number < minimum or maximum is not None and number > maximum:
            raise ValueError("「{}」须在 {} ~ {} 之间".format(label, minimum, maximum))
        return str(number)
    text = str(value).strip()
    if item.get("is_required", True) and not text:
        raise ValueError("「{}」不能为空".format(label))
    if len(text) > 500:
        raise ValueError("「{}」不能超过 500 个字符".format(label))
    return text


def save_group_settings(
    session: Session, group_name: str, values: Dict[str, str], user_id: int
) -> list:
    """原子校验并保存一个分组的设置值。"""
    initialize_defaults(session)
    rows = session.scalars(
        select(SystemSetting).where(SystemSetting.group_name == group_name)
    ).all()
    if not rows:
        raise LookupError("设置分组不存在")
    allowed = {row.key for row in rows}
    if set(values) - allowed:
        raise ValueError("包含其他分组或不支持的设置项")
    validated = {
        key: validate_setting_value(key, value)
        for key, value in values.items()
    }
    changes = []
    for row in rows:
        if row.key not in validated:
            continue
        value = validated[row.key]
        if value != row.value:
            row.value = value
            row.updated_by = user_id
            row.updated_at = datetime.utcnow()
            changes.append(row.key)
    session.flush()
    return changes


def _retention_days(session: Session, key: str) -> int:
    """读取保留天数；无效值按关闭清理处理。"""
    value = read_setting(session, key, 90)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def purge_expired_data(session_factory: sessionmaker) -> Dict[str, int]:
    """按保留策略清理终态任务、审计日志和登录日志。"""
    result = {"task_center": 0, "release_history": 0, "audit_log": 0, "login_log": 0, "upgrade_task": 0}
    now = datetime.utcnow()
    with session_scope(session_factory) as session:
        try:
            initialize_defaults(session)
            task_days = _retention_days(session, "system.retention_task_center_days")
            release_days = _retention_days(session, "system.retention_release_history_days")
            audit_days = _retention_days(session, "system.retention_audit_log_days")
            login_days = _retention_days(session, "system.retention_login_log_days")
            upgrade_days = _retention_days(session, "system.retention_upgrade_task_days")
            if task_days:
                cutoff = now - timedelta(days=task_days)
                result["task_center"] = session.execute(
                    delete(Task).where(
                        Task.created_at < cutoff,
                        Task.status.not_in(ACTIVE_STATUSES),
                        Task.operation_type.not_in(_RELEASE_TYPES + _UPGRADE_TYPES),
                    )
                ).rowcount or 0
            if release_days:
                cutoff = now - timedelta(days=release_days)
                result["release_history"] = session.execute(
                    delete(Task).where(
                        Task.created_at < cutoff,
                        Task.status.not_in(ACTIVE_STATUSES),
                        Task.operation_type.in_(_RELEASE_TYPES),
                    )
                ).rowcount or 0
            if upgrade_days:
                cutoff = now - timedelta(days=upgrade_days)
                from ngxops.upgrade.models import NginxUpgradeRun
                active_upgrade_task_ids = select(NginxUpgradeRun.task_id).where(
                    NginxUpgradeRun.phase.in_(_UPGRADE_ACTIVE_PHASES)
                )
                result["upgrade_task"] = session.execute(
                    delete(Task).where(
                        Task.created_at < cutoff,
                        Task.status.not_in(ACTIVE_STATUSES),
                        Task.operation_type.in_(_UPGRADE_TYPES),
                        Task.id.not_in(active_upgrade_task_ids),
                    )
                ).rowcount or 0
            if audit_days:
                cutoff = now - timedelta(days=audit_days)
                result["audit_log"] = session.execute(
                    delete(AuditLog).where(AuditLog.created_at < cutoff)
                ).rowcount or 0
            if login_days:
                cutoff = now - timedelta(days=login_days)
                result["login_log"] = session.execute(
                    delete(LoginLog).where(LoginLog.created_at < cutoff)
                ).rowcount or 0
            session.commit()
        except Exception:
            session.rollback()
            raise
    if sum(result.values()):
        logger.info("数据保留清理完成: %s", result)
    return result


def maybe_run_daily_purge(session_factory: sessionmaker) -> bool:
    """在本进程每天最多启动一次后台清理。"""
    global _LAST_PURGE_DATE
    today = datetime.utcnow().date()
    with _PURGE_LOCK:
        if _LAST_PURGE_DATE == today:
            return False
        _LAST_PURGE_DATE = today

    def run_purge() -> None:
        """在线程中运行清理并记录失败类型。"""
        try:
            purge_expired_data(session_factory)
        except Exception as exc:
            log_exception(logger, "每日数据保留清理失败", exc)

    threading.Thread(target=run_purge, name="ngxops-data-retention", daemon=True).start()
    return True
