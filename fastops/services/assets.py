"""资产类只读查询服务。"""

from typing import Dict, List

from fastops.core.config import get_settings
from fastops.db.sqlite import connect, count_table, table_exists
from fastops.services.pagination import build_pagination
from fastops.services.query import where_clause


def _recent_tasks(conn, limit: int = 8) -> List[Dict[str, str]]:
    """读取最近任务中心记录摘要。"""
    table_name = "releases_taskcentertask"
    if not table_exists(conn, table_name):
        return []
    rows = conn.execute(
        """
        select id, operation_type, status, progress, created_at, source_batch
        from releases_taskcentertask
        order by created_at desc
        limit ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def list_nodes(search: str = "", status: str = "", page: int = 1, per_page: int = 10):
    """查询 FastAPI 节点列表页数据。"""
    filters = ["n.is_deleted = 0"]
    params: List[object] = []
    search = (search or "").strip()
    if search:
        like = f"%{search}%"
        filters.append("(n.hostname like ? or n.ip like ?)")
        params.extend([like, like])
    if status in ("online", "offline", "unknown"):
        filters.append("n.status = ?")
        params.append(status)

    where_sql = where_clause(filters)
    with connect() as conn:
        total = conn.execute(
            f"select count(*) as total from nodes_node n{where_sql}", params
        ).fetchone()["total"]
        pagination = build_pagination(int(total or 0), page, per_page)
        rows = conn.execute(
            f"""
            select
                n.id, n.hostname, n.ip, n.port, n.environment, n.status,
                n.nginx_version, n.nginx_available, n.is_locked,
                n.last_probe_at, n.last_nginx_probe_at, c.name as credential_name,
                ngs.group_names
            from nodes_node n
            left join credentials_credential c on c.id = n.credential_id
            left join (
                select ng.node_id, group_concat(g.name, '、') as group_names
                from nodes_node_groups ng
                join nodes_nodegroup g on g.id = ng.nodegroup_id
                group by ng.node_id
            ) ngs on ngs.node_id = n.id
            {where_sql}
            order by n.id desc
            limit ? offset ?
            """,
            [*params, pagination["per_page"], pagination["offset"]],
        ).fetchall()
    return {"items": [dict(row) for row in rows], "pagination": pagination}


def list_credentials(
    search: str = "",
    auth_type: str = "",
    status: str = "",
    page: int = 1,
    per_page: int = 10,
):
    """查询 FastAPI 凭证列表页数据。"""
    filters: List[str] = []
    params: List[object] = []
    search = (search or "").strip()
    if search:
        like = f"%{search}%"
        filters.append("(c.name like ? or c.username like ?)")
        params.extend([like, like])
    if auth_type in ("password", "key"):
        filters.append("c.auth_type = ?")
        params.append(auth_type)
    if status == "enabled":
        filters.append("c.is_enabled = 1")
    elif status == "disabled":
        filters.append("c.is_enabled = 0")

    where_sql = where_clause(filters)
    with connect() as conn:
        total = conn.execute(
            f"select count(*) as total from credentials_credential c{where_sql}",
            params,
        ).fetchone()["total"]
        pagination = build_pagination(int(total or 0), page, per_page)
        rows = conn.execute(
            f"""
            select
                c.id, c.name, c.username, c.auth_type, c.is_enabled,
                c.last_test_time, c.last_test_result, c.created_at,
                u.username as created_by,
                coalesce(nc.node_count, 0) as node_count
            from credentials_credential c
            left join auth_user u on u.id = c.created_by_id
            left join (
                select credential_id, count(*) as node_count
                from nodes_node
                where is_deleted = 0 and credential_id is not null
                group by credential_id
            ) nc on nc.credential_id = c.id
            {where_sql}
            order by c.id desc
            limit ? offset ?
            """,
            [*params, pagination["per_page"], pagination["offset"]],
        ).fetchall()
    return {"items": [dict(row) for row in rows], "pagination": pagination}


def get_migration_snapshot() -> Dict[str, object]:
    """返回 FastAPI 迁移页展示用的轻量数据快照。"""
    with connect() as conn:
        return {
            "db_path": str(get_settings().database_path),
            "counts": {
                "nodes": count_table(conn, "nodes_node"),
                "credentials": count_table(conn, "credentials_credential"),
                "configs": count_table(conn, "configs_config"),
                "bindings": count_table(conn, "configs_confignodebinding"),
                "tasks": count_table(conn, "releases_taskcentertask"),
                "audit_logs": count_table(conn, "audit_auditlog"),
                "login_logs": count_table(conn, "audit_loginlog"),
            },
            "recent_tasks": _recent_tasks(conn),
        }
