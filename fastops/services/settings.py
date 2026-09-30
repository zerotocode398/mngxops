"""系统设置只读查询服务。"""

from typing import Dict, List

from fastops.db.sqlite import connect, table_exists

ACTIVE_SETTING_KEYS = [
    "dashboard.recent_tasks_count",
    "node.batch_max_count",
    "node.ssh_connect_timeout",
    "node.ssh_default_port",
    "node.detect_retries",
    "config.discover_max_depth",
    "config.default_nginx_path",
    "config.default_nginx_bin",
    "release.backup_dir",
    "auth.login_fail_lock_count",
    "auth.login_fail_lock_minutes",
    "system.task_progress_poll_interval",
    "system.dashboard_refresh_interval",
    "system.retention_task_center_days",
    "system.retention_release_history_days",
    "system.retention_audit_log_days",
    "system.retention_login_log_days",
    "system.retention_upgrade_task_days",
    "upgrade.default_work_dir",
    "upgrade.make_jobs_default",
    "upgrade.package_max_size_mb",
    "install.default_user",
    "install.default_group",
    "install.default_prefix",
    "install.default_listen_port",
]

GROUP_META = {
    "仪表盘": "仪表盘与运维工具首页最近任务展示条数。",
    "节点管理": "SSH 超时、默认端口、批量操作上限与探测相关参数。",
    "配置管理": "发现深度与默认主配置/Nginx 路径。",
    "发布管理": "远程配置备份路径。",
    "登录": "连续登录失败次数与临时锁定时长。",
    "系统": "任务进度轮询、仪表盘刷新间隔与历史数据保留天数。",
    "Nginx升级": "默认工作目录、并行编译核数与源码包/第三方模块包大小限制。",
    "安装管理": "Nginx 全新安装向导缺省用户、用户组、安装路径与监听端口。",
}

UNIT_MAP = {
    "dashboard.recent_tasks_count": "条",
    "node.batch_max_count": "台",
    "node.ssh_connect_timeout": "秒",
    "node.detect_retries": "次",
    "config.discover_max_depth": "层",
    "auth.login_fail_lock_count": "次",
    "auth.login_fail_lock_minutes": "分钟",
    "system.task_progress_poll_interval": "秒",
    "system.dashboard_refresh_interval": "秒",
    "system.retention_task_center_days": "天",
    "system.retention_release_history_days": "天",
    "system.retention_audit_log_days": "天",
    "system.retention_login_log_days": "天",
    "system.retention_upgrade_task_days": "天",
    "upgrade.make_jobs_default": "核",
    "upgrade.package_max_size_mb": "MB",
}


def _active_key_clause() -> str:
    """生成已接线设置项 key 的 SQL 占位条件。"""
    return ",".join("?" for _ in ACTIVE_SETTING_KEYS)


def list_setting_groups() -> List[Dict[str, object]]:
    """按分组查询系统设置摘要。"""
    rows = list_settings()
    grouped: Dict[str, Dict[str, object]] = {}
    for item in rows:
        group_name = str(item["group"])
        if group_name not in grouped:
            grouped[group_name] = {
                "name": group_name,
                "description": GROUP_META.get(group_name, "系统运行参数。"),
                "count": 0,
            }
        grouped[group_name]["count"] = int(grouped[group_name]["count"]) + 1
    return list(grouped.values())


def list_settings(group: str = "") -> List[Dict[str, object]]:
    """查询已接线系统设置项。"""
    with connect() as conn:
        if not table_exists(conn, "settings_systemsetting"):
            return []
        params: List[object] = list(ACTIVE_SETTING_KEYS)
        group_sql = ""
        if group:
            group_sql = " and s.\"group\" = ?"
            params.append(group)
        rows = conn.execute(
            f"""
            select
                s.id, s.key, s.value, s.type as value_type, s."group", s.label,
                s.description, s.placeholder, s.options, s.is_required,
                s.sort_order, s.updated_at, coalesce(u.username, '-') as updated_by
            from settings_systemsetting s
            left join auth_user u on u.id = s.updated_by_id
            where s.key in ({_active_key_clause()}){group_sql}
            order by s."group" asc, s.sort_order asc, s.id asc
            """,
            params,
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["unit"] = UNIT_MAP.get(item["key"], "")
        item["is_required"] = bool(item["is_required"])
        items.append(item)
    return items


def settings_value_map() -> Dict[str, str]:
    """查询全部已接线设置的 key 到 value 映射。"""
    return {item["key"]: item["value"] for item in list_settings()}
