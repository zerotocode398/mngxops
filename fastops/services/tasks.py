"""任务中心只读查询服务。"""

import re
from typing import Dict, List, Optional, Sequence, Tuple

from fastops.db.sqlite import connect, table_exists
from fastops.services.pagination import build_pagination
from fastops.services.query import split_search_tags, where_clause
from fastops.services.users import (
    FastUser,
    task_center_limited_ops_for_user,
    user_permission_codes,
)

STATUS_LABELS = {
    "pending": "等待中",
    "running": "执行中",
    "success": "成功",
    "failed": "失败",
    "cancelled": "已取消",
}

OPERATION_TYPE_LABELS = {
    "release_publish": "发布配置",
    "release_rollback": "回滚配置",
    "credential_enable_test": "凭证启用测试",
    "node_ssh_test": "节点SSH测试",
    "node_batch_test": "节点批量测试",
    "node_system_info": "节点系统信息采集",
    "node_nginx_version": "Nginx 版本检测",
    "config_batch_sync": "配置批量同步",
    "config_discover": "配置发现扫描",
    "config_drift_check": "配置漂移检测",
    "config_glob_preview": "配置Glob预览",
    "nginx_upgrade": "Nginx 编译升级",
    "nginx_rollback": "Nginx 升级回滚",
    "nginx_service_control": "Nginx 服务启停",
    "nginx_install": "Nginx 全新安装",
    "nginx_uninstall": "Nginx 卸载",
    "other": "其他任务",
}

VISIBLE_OPERATION_TYPES = [
    item
    for item in OPERATION_TYPE_LABELS
    if item not in ("config_discover", "config_drift_check", "config_glob_preview")
]


def _shorten_csv(value: str, limit: int = 3) -> str:
    """压缩逗号分隔文本，最多展示指定数量。"""
    items = [item.strip() for item in (value or "").split(",") if item.strip()]
    if not items:
        return ""
    if len(items) <= limit:
        return "、".join(items)
    return f"{'、'.join(items[:limit])} 等 {len(items)} 项"


def _extract_success_fail(text: str) -> str:
    """从文本中提取成功失败摘要。"""
    text = (text or "").strip()
    if not text:
        return ""
    match = re.search(r"成功\s*\d+[^，,；;\n]*[，,；;\s]+失败\s*\d+[^，,；;\n]*", text)
    if match:
        return match.group(0).strip("，,；; ")
    return ""


def _task_summary(row: Dict[str, object]) -> Tuple[str, str]:
    """生成任务中心列表主副摘要。"""
    detail = str(row.get("detail") or "").strip()
    result = str(row.get("result") or "").strip()
    batch = str(row.get("source_batch") or "").strip()
    hosts = str(row.get("target_hostnames") or "").strip()
    primary = batch or _shorten_csv(hosts) or "-"
    secondary = _extract_success_fail(detail) or _extract_success_fail(result)
    if not secondary:
        secondary = detail or (result.splitlines()[0].strip() if result else "")
    return primary, secondary


def _user_task_scope(user: FastUser) -> Tuple[bool, Sequence[str]]:
    """返回用户是否可看全部任务及受限任务类型。"""
    codes = user_permission_codes(user)
    can_read_all = user.is_superuser or "*" in codes or "releases.read" in codes
    if can_read_all:
        return True, ()
    return False, tuple(task_center_limited_ops_for_user(user))


def user_can_read_task_center(user: FastUser) -> bool:
    """判断用户是否可访问任务中心。"""
    can_read_all, allowed_ops = _user_task_scope(user)
    return can_read_all or bool(allowed_ops)


def operation_type_options() -> List[Dict[str, str]]:
    """返回任务类型筛选选项。"""
    return [
        {"value": value, "label": OPERATION_TYPE_LABELS[value]}
        for value in VISIBLE_OPERATION_TYPES
    ]


def status_options() -> List[Dict[str, str]]:
    """返回任务状态筛选选项。"""
    return [{"value": value, "label": label} for value, label in STATUS_LABELS.items()]


def list_task_center_tasks(
    user: FastUser,
    search: str = "",
    status: str = "",
    operation_type: str = "",
    page: int = 1,
    per_page: int = 10,
) -> Dict[str, object]:
    """分页查询任务中心列表。"""
    can_read_all, allowed_ops = _user_task_scope(user)
    if not can_read_all and not allowed_ops:
        return {"items": [], "pagination": build_pagination(0, page, per_page)}

    filters: List[str] = []
    params: List[object] = []
    if not can_read_all:
        placeholders = ",".join("?" for _ in allowed_ops)
        filters.append(f"t.operation_type in ({placeholders})")
        params.extend(allowed_ops)
        filters.append("t.trigger_user_id = ?")
        params.append(user.id)
    for term in split_search_tags(search):
        like = f"%{term}%"
        filters.append("(t.source_batch like ? or t.target_hostnames like ? or t.target_ips like ?)")
        params.extend([like, like, like])
    if status in STATUS_LABELS:
        filters.append("t.status = ?")
        params.append(status)
    if operation_type in OPERATION_TYPE_LABELS:
        filters.append("t.operation_type = ?")
        params.append(operation_type)

    where_sql = where_clause(filters)
    with connect() as conn:
        if not table_exists(conn, "releases_taskcentertask"):
            return {"items": [], "pagination": build_pagination(0, page, per_page)}
        total = conn.execute(
            f"""
            select count(*) as total
            from releases_taskcentertask t
            left join auth_user u on u.id = t.trigger_user_id
            {where_sql}
            """,
            params,
        ).fetchone()["total"]
        pagination = build_pagination(int(total or 0), page, per_page)
        rows = conn.execute(
            f"""
            select
                t.id, t.operation_type, t.status, t.detail, t.result,
                t.progress, t.source_batch, t.target_hostnames, t.target_ips,
                t.target_configs, t.started_at, t.finished_at, t.created_at,
                t.updated_at, coalesce(u.username, '-') as trigger_username
            from releases_taskcentertask t
            left join auth_user u on u.id = t.trigger_user_id
            {where_sql}
            order by t.created_at desc, t.id desc
            limit ? offset ?
            """,
            [*params, pagination["per_page"], pagination["offset"]],
        ).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        item["operation_label"] = OPERATION_TYPE_LABELS.get(
            item["operation_type"], item["operation_type"]
        )
        item["status_label"] = STATUS_LABELS.get(item["status"], item["status"])
        primary, secondary = _task_summary(item)
        item["summary_primary"] = primary
        item["summary_secondary"] = secondary
        items.append(item)
    return {"items": items, "pagination": pagination}


def get_task_center_task(user: FastUser, task_id: int) -> Optional[Dict[str, object]]:
    """按权限读取任务中心详情。"""
    can_read_all, allowed_ops = _user_task_scope(user)
    if not can_read_all and not allowed_ops:
        return None
    filters = ["t.id = ?"]
    params: List[object] = [task_id]
    if not can_read_all:
        placeholders = ",".join("?" for _ in allowed_ops)
        filters.append(f"t.operation_type in ({placeholders})")
        params.extend(allowed_ops)
        filters.append("t.trigger_user_id = ?")
        params.append(user.id)
    where_sql = where_clause(filters)
    with connect() as conn:
        if not table_exists(conn, "releases_taskcentertask"):
            return None
        row = conn.execute(
            f"""
            select
                t.id, t.operation_type, t.status, t.detail, t.result, t.log_output,
                t.progress, t.source_batch, t.target_hostnames, t.target_ips,
                t.target_configs, t.started_at, t.finished_at, t.created_at,
                t.updated_at, coalesce(u.username, '-') as trigger_username
            from releases_taskcentertask t
            left join auth_user u on u.id = t.trigger_user_id
            {where_sql}
            limit 1
            """,
            params,
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["operation_label"] = OPERATION_TYPE_LABELS.get(
        item["operation_type"], item["operation_type"]
    )
    item["status_label"] = STATUS_LABELS.get(item["status"], item["status"])
    primary, secondary = _task_summary(item)
    item["summary_primary"] = primary
    item["summary_secondary"] = secondary
    return item
