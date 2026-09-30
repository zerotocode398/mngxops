"""审计日志只读查询服务。"""

from typing import Dict, List

from fastops.db.sqlite import connect, table_exists
from fastops.services.pagination import build_pagination
from fastops.services.query import date_range_filters, split_search_tags, where_clause

FAIL_REASON_LABELS = {
    "": "未知",
    "user_not_found": "用户不存在",
    "wrong_password": "密码错误",
    "user_locked": "用户已锁定",
    "user_inactive": "用户未激活",
}


def list_audit_modules() -> List[str]:
    """查询操作日志中出现过的模块。"""
    with connect() as conn:
        if not table_exists(conn, "audit_auditlog"):
            return []
        rows = conn.execute(
            """
            select distinct module
            from audit_auditlog
            where module <> ''
            order by module asc
            """
        ).fetchall()
    return [row["module"] for row in rows]


def list_audit_logs(
    search: str = "",
    module: str = "",
    result: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    per_page: int = 10,
) -> Dict[str, object]:
    """分页查询操作日志。"""
    filters: List[str] = []
    params: List[object] = []
    for term in split_search_tags(search):
        like = f"%{term}%"
        filters.append("(u.username like ? or l.action like ? or l.detail like ?)")
        params.extend([like, like, like])
    if module:
        filters.append("l.module = ?")
        params.append(module)
    if result in ("success", "failed"):
        filters.append("l.result = ?")
        params.append(result)
    date_filters, date_params = date_range_filters("l.created_at", date_from, date_to)
    filters.extend(date_filters)
    params.extend(date_params)

    where_sql = where_clause(filters)
    with connect() as conn:
        if not table_exists(conn, "audit_auditlog"):
            return {"items": [], "pagination": build_pagination(0, page, per_page)}
        total = conn.execute(
            f"""
            select count(*) as total
            from audit_auditlog l
            left join auth_user u on u.id = l.user_id
            {where_sql}
            """,
            params,
        ).fetchone()["total"]
        pagination = build_pagination(int(total or 0), page, per_page)
        rows = conn.execute(
            f"""
            select
                l.id, l.module, l.action, l.ip, l.result, l.detail,
                l.task_center_id, l.source_batch, l.created_at,
                coalesce(u.username, '-') as username
            from audit_auditlog l
            left join auth_user u on u.id = l.user_id
            {where_sql}
            order by l.created_at desc, l.id desc
            limit ? offset ?
            """,
            [*params, pagination["per_page"], pagination["offset"]],
        ).fetchall()
    return {"items": [dict(row) for row in rows], "pagination": pagination}


def list_login_logs(
    search: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    per_page: int = 10,
) -> Dict[str, object]:
    """分页查询登录日志。"""
    filters: List[str] = []
    params: List[object] = []
    for term in split_search_tags(search):
        like = f"%{term}%"
        filters.append("(username like ? or ip like ?)")
        params.extend([like, like])
    if status in ("success", "failed"):
        filters.append("status = ?")
        params.append(status)
    date_filters, date_params = date_range_filters("created_at", date_from, date_to)
    filters.extend(date_filters)
    params.extend(date_params)

    where_sql = where_clause(filters)
    with connect() as conn:
        if not table_exists(conn, "audit_loginlog"):
            return {"items": [], "pagination": build_pagination(0, page, per_page)}
        total = conn.execute(
            f"select count(*) as total from audit_loginlog{where_sql}",
            params,
        ).fetchone()["total"]
        pagination = build_pagination(int(total or 0), page, per_page)
        rows = conn.execute(
            f"""
            select id, username, ip, user_agent, status, fail_reason, created_at
            from audit_loginlog
            {where_sql}
            order by created_at desc, id desc
            limit ? offset ?
            """,
            [*params, pagination["per_page"], pagination["offset"]],
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["fail_reason_label"] = FAIL_REASON_LABELS.get(
            item.get("fail_reason") or "",
            item.get("fail_reason") or "未知",
        )
        items.append(item)
    return {"items": items, "pagination": pagination}
