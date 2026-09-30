"""配置管理只读查询服务。"""

import sqlite3
from typing import Dict, List, Tuple

from fastops.db.sqlite import connect, table_exists
from fastops.services.pagination import build_pagination
from fastops.services.query import split_search_tags, where_clause

SYNC_STATUS_LABELS = {
    "pending": "待同步",
    "synced": "已同步",
    "orphaned": "远程删除",
    "failed": "同步失败",
    "marked_deleted": "标记删除",
}

STATUS_COUNT_KEYS = (
    "total",
    "pending",
    "synced",
    "orphaned",
    "failed",
    "marked_deleted",
)


def _pending_statuses() -> Tuple[str, str]:
    """返回待同步归并状态。"""
    return "not_synced", "modified"


def _node_filter_sql(
    search: str,
    group_id: str,
    sync_status: str,
    nginx_available: str,
    has_config_tables: bool = True,
    has_group_tables: bool = True,
) -> Tuple[str, List[object]]:
    """生成配置节点列表筛选 SQL。"""
    filters = ["n.is_deleted = 0", "n.is_locked = 0"]
    params: List[object] = []
    for term in split_search_tags(search):
        like = f"%{term}%"
        if has_config_tables:
            filters.append(
                """
                (
                    n.hostname like ? or n.ip like ? or exists (
                        select 1
                        from configs_confignodebinding b
                        join configs_config c on c.id = b.config_id
                        where b.node_id = n.id
                          and (c.name like ? or b.remote_path like ?)
                    )
                )
                """
            )
            params.extend([like, like, like, like])
        else:
            filters.append("(n.hostname like ? or n.ip like ?)")
            params.extend([like, like])
    if group_id:
        if has_group_tables:
            filters.append(
                """
                exists (
                    select 1 from nodes_node_groups ng
                    where ng.node_id = n.id and ng.nodegroup_id = ?
                )
                """
            )
            params.append(group_id)
        else:
            filters.append("1 = 0")
    if sync_status:
        if not has_config_tables:
            filters.append("1 = 0")
        elif sync_status == "pending":
            filters.append(
                """
                exists (
                    select 1 from configs_confignodebinding b
                    where b.node_id = n.id and b.sync_status in (?, ?)
                )
                """
            )
            params.extend(_pending_statuses())
        else:
            filters.append(
                """
                exists (
                    select 1 from configs_confignodebinding b
                    where b.node_id = n.id and b.sync_status = ?
                )
                """
            )
            params.append(sync_status)
    if nginx_available == "true":
        filters.append("n.nginx_available = 1")
    return where_clause(filters), params


def _empty_status_counts() -> Dict[str, int]:
    """返回空状态计数字典。"""
    return {key: 0 for key in STATUS_COUNT_KEYS}


def _global_status_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """统计全部未删除节点上的配置绑定状态。"""
    if not table_exists(conn, "configs_confignodebinding"):
        return _empty_status_counts()
    row = conn.execute(
        """
        select
            count(*) as total,
            sum(case when b.sync_status in ('not_synced', 'modified') then 1 else 0 end) as pending,
            sum(case when b.sync_status = 'synced' then 1 else 0 end) as synced,
            sum(case when b.sync_status = 'orphaned' then 1 else 0 end) as orphaned,
            sum(case when b.sync_status = 'failed' then 1 else 0 end) as failed,
            sum(case when b.sync_status = 'marked_deleted' then 1 else 0 end) as marked_deleted
        from configs_confignodebinding b
        join nodes_node n on n.id = b.node_id
        where n.is_deleted = 0
        """
    ).fetchone()
    return {key: int(row[key] or 0) for key in row.keys()}


def _list_config_groups(conn: sqlite3.Connection) -> List[Dict[str, object]]:
    """使用当前连接查询节点组筛选数据。"""
    if not table_exists(conn, "nodes_nodegroup"):
        return []
    rows = conn.execute(
        "select id, name from nodes_nodegroup order by name asc"
    ).fetchall()
    return [dict(row) for row in rows]


def _list_unbound_configs(conn: sqlite3.Connection) -> List[Dict[str, object]]:
    """使用当前连接查询未绑定任何节点的配置标签。"""
    if (
        not table_exists(conn, "configs_config")
        or not table_exists(conn, "configs_confignodebinding")
    ):
        return []
    rows = conn.execute(
        """
        select c.id, c.name, c.default_remote_path, c.source, c.created_at,
               coalesce(u.username, '-') as created_by
        from configs_config c
        left join auth_user u on u.id = c.created_by_id
        where not exists (
            select 1 from configs_confignodebinding b where b.config_id = c.id
        )
        order by c.created_at desc, c.id desc
        limit 50
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _binding_stats_sql(has_config_tables: bool) -> str:
    """返回节点绑定状态统计子查询 SQL。"""
    if not has_config_tables:
        return (
            "select null as node_id, 0 as total, 0 as synced, 0 as pending, "
            "0 as orphaned, 0 as failed, 0 as marked_deleted where 0"
        )
    return """
        select
            b.node_id,
            count(*) as total,
            sum(case when b.sync_status = 'synced' then 1 else 0 end) as synced,
            sum(case when b.sync_status in ('not_synced', 'modified') then 1 else 0 end) as pending,
            sum(case when b.sync_status = 'orphaned' then 1 else 0 end) as orphaned,
            sum(case when b.sync_status = 'failed' then 1 else 0 end) as failed,
            sum(case when b.sync_status = 'marked_deleted' then 1 else 0 end) as marked_deleted
        from configs_confignodebinding b
        group by b.node_id
    """


def _node_groups_sql(has_group_tables: bool) -> str:
    """返回节点组聚合子查询 SQL。"""
    if not has_group_tables:
        return "select null as node_id, null as group_names where 0"
    return """
        select ng.node_id, group_concat(g.name, '、') as group_names
        from nodes_node_groups ng
        join nodes_nodegroup g on g.id = ng.nodegroup_id
        group by ng.node_id
    """


def _empty_config_node_result(page: int, per_page: int) -> Dict[str, object]:
    """返回配置节点列表空结果。"""
    return {
        "items": [],
        "pagination": build_pagination(0, page, per_page),
        "status_counts": _empty_status_counts(),
        "groups": [],
        "unbound_configs": [],
        "nginx_available_count": 0,
        "total_nodes_count": 0,
    }


def list_config_groups() -> List[Dict[str, object]]:
    """查询节点组筛选数据。"""
    with connect() as conn:
        return _list_config_groups(conn)


def list_unbound_configs() -> List[Dict[str, object]]:
    """查询未绑定任何节点的配置标签。"""
    with connect() as conn:
        return _list_unbound_configs(conn)


def list_config_nodes(
    search: str = "",
    group_id: str = "",
    sync_status: str = "",
    nginx_available: str = "true",
    page: int = 1,
    per_page: int = 10,
) -> Dict[str, object]:
    """分页查询配置列表节点视图。"""
    with connect() as conn:
        if not table_exists(conn, "nodes_node"):
            return _empty_config_node_result(page, per_page)
        has_config_tables = (
            table_exists(conn, "configs_confignodebinding")
            and table_exists(conn, "configs_config")
        )
        has_group_tables = (
            table_exists(conn, "nodes_node_groups")
            and table_exists(conn, "nodes_nodegroup")
        )
        where_sql, params = _node_filter_sql(
            search,
            group_id,
            sync_status,
            nginx_available,
            has_config_tables=has_config_tables,
            has_group_tables=has_group_tables,
        )
        total = conn.execute(
            f"select count(*) as total from nodes_node n{where_sql}", params
        ).fetchone()["total"]
        pagination = build_pagination(int(total or 0), page, per_page)
        binding_stats_sql = _binding_stats_sql(has_config_tables)
        node_groups_sql = _node_groups_sql(has_group_tables)
        rows = conn.execute(
            f"""
            select
                n.id, n.hostname, n.ip, n.port, n.environment, n.status,
                n.nginx_available, n.nginx_version, ngs.group_names,
                coalesce(s.total, 0) as total,
                coalesce(s.synced, 0) as synced,
                coalesce(s.pending, 0) as pending,
                coalesce(s.orphaned, 0) as orphaned,
                coalesce(s.failed, 0) as failed,
                coalesce(s.marked_deleted, 0) as marked_deleted
            from nodes_node n
            left join ({binding_stats_sql}) s on s.node_id = n.id
            left join ({node_groups_sql}) ngs on ngs.node_id = n.id
            {where_sql}
            order by n.hostname asc, n.id asc
            limit ? offset ?
            """,
            [*params, pagination["per_page"], pagination["offset"]],
        ).fetchall()
        counts = conn.execute(
            """
            select
                count(*) as total_nodes_count,
                sum(case when nginx_available = 1 then 1 else 0 end) as nginx_available_count
            from nodes_node
            where is_deleted = 0 and is_locked = 0
            """
        ).fetchone()
        include_unbound = not (search or group_id or sync_status)
        return {
            "items": [dict(row) for row in rows],
            "pagination": pagination,
            "status_counts": _global_status_counts(conn),
            "groups": _list_config_groups(conn),
            "unbound_configs": _list_unbound_configs(conn) if include_unbound else [],
            "nginx_available_count": int(counts["nginx_available_count"] or 0),
            "total_nodes_count": int(counts["total_nodes_count"] or 0),
        }
