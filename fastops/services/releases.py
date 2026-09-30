"""发布历史只读查询服务。"""

from collections import OrderedDict
from typing import Dict, List, Sequence

from fastops.db.sqlite import connect, table_exists
from fastops.services.pagination import build_pagination
from fastops.services.query import split_search_tags, where_clause


RELEASE_STATUS_LABELS = {
    "pending": "等待发布",
    "running": "发布中",
    "success": "发布成功",
    "failed": "发布失败",
    "rollback": "已回滚",
    "cancelled": "已取消",
}


def release_status_options() -> List[Dict[str, str]]:
    """返回发布任务状态筛选选项。"""
    return [
        {"value": value, "label": label}
        for value, label in RELEASE_STATUS_LABELS.items()
    ]


def _release_tables_exist(conn) -> bool:
    """判断发布历史查询依赖的表是否存在。"""
    required_tables = (
        "releases_releasetask",
        "nodes_node",
        "configs_config",
        "auth_user",
    )
    return all(table_exists(conn, table_name) for table_name in required_tables)


def _filters(
    search: str,
    status: str,
    batch: str,
    node_ip: str,
) -> tuple[List[str], List[object]]:
    """生成发布历史筛选条件与参数。"""
    filters: List[str] = []
    params: List[object] = []
    for term in split_search_tags(search):
        like = f"%{term}%"
        filters.append(
            "(c.name like ? or n.hostname like ? or t.batch_number like ? or u.username like ?)"
        )
        params.extend([like, like, like, like])
    if status in RELEASE_STATUS_LABELS:
        filters.append("t.status = ?")
        params.append(status)
    if batch:
        filters.append("t.batch_number like ?")
        params.append(f"%{batch}%")
    if node_ip:
        filters.append("n.ip like ?")
        params.append(f"%{node_ip}%")
    return filters, params


def _base_from_sql() -> str:
    """返回发布历史基础关联 SQL。"""
    return """
        from releases_releasetask t
        join nodes_node n on n.id = t.node_id
        join configs_config c on c.id = t.config_id
        left join auth_user u on u.id = t.operator_id
    """


def _page_batch_numbers(
    conn,
    where_sql: str,
    params: Sequence[object],
    page: int,
    per_page: int,
) -> tuple[List[str], Dict[str, int]]:
    """按批次分页并返回本页批次号。"""
    total_row = conn.execute(
        f"""
        select count(*) as total
        from (
            select t.batch_number
            {_base_from_sql()}
            {where_sql}
            group by t.batch_number
        ) batches
        """,
        params,
    ).fetchone()
    pagination = build_pagination(int(total_row["total"] or 0), page, per_page)
    rows = conn.execute(
        f"""
        select t.batch_number, max(t.created_at) as latest
        {_base_from_sql()}
        {where_sql}
        group by t.batch_number
        order by latest desc, t.batch_number desc
        limit ? offset ?
        """,
        [*params, pagination["per_page"], pagination["offset"]],
    ).fetchall()
    return [str(row["batch_number"] or "") for row in rows], pagination


def _task_rows(
    conn,
    batch_numbers: Sequence[str],
    filters: Sequence[str],
    params: Sequence[object],
):
    """查询本页批次下的发布任务明细。"""
    if not batch_numbers:
        return []
    batch_filter = "t.batch_number in ({})".format(
        ",".join("?" for _ in batch_numbers)
    )
    where_sql = where_clause([*filters, batch_filter])
    return conn.execute(
        f"""
        select
            t.id, t.batch_number, t.publish_version, t.version_id, t.remote_path,
            t.status, t.result, t.started_at, t.finished_at, t.created_at,
            t.binding_id, t.config_id, t.node_id, t.operator_id,
            c.name as config_name,
            n.hostname, n.ip, n.port, n.is_locked, n.is_deleted,
            n.nginx_available,
            coalesce(u.username, '-') as operator_username,
            coalesce(ngs.group_names, '') as group_names
        {_base_from_sql()}
        left join (
            select ng.node_id, group_concat(g.name, '、') as group_names
            from nodes_node_groups ng
            join nodes_nodegroup g on g.id = ng.nodegroup_id
            group by ng.node_id
        ) ngs on ngs.node_id = n.id
        {where_sql}
        order by t.created_at desc, t.id desc
        """,
        [*params, *batch_numbers],
    ).fetchall()


def _empty_result(page: int, per_page: int) -> Dict[str, object]:
    """返回空发布历史分页结果。"""
    return {"items": [], "pagination": build_pagination(0, page, per_page)}


def list_release_history_batches(
    search: str = "",
    status: str = "",
    batch: str = "",
    node_ip: str = "",
    page: int = 1,
    per_page: int = 10,
) -> Dict[str, object]:
    """按批次分页查询发布历史树。"""
    filters, params = _filters(
        search=(search or "").strip(),
        status=(status or "").strip(),
        batch=(batch or "").strip(),
        node_ip=(node_ip or "").strip(),
    )
    where_sql = where_clause(filters)
    with connect() as conn:
        if not _release_tables_exist(conn):
            return _empty_result(page, per_page)
        batch_numbers, pagination = _page_batch_numbers(
            conn, where_sql, params, page, per_page
        )
        rows = _task_rows(conn, batch_numbers, filters, params)

    batches: OrderedDict[str, Dict[str, object]] = OrderedDict(
        (
            batch_number,
            {
                "batch_number": batch_number,
                "operator": "-",
                "created_at": None,
                "total": 0,
                "success": 0,
                "failed": 0,
                "other": 0,
                "nodes": OrderedDict(),
            },
        )
        for batch_number in batch_numbers
    )
    for row in rows:
        item = dict(row)
        item["status_label"] = RELEASE_STATUS_LABELS.get(item["status"], item["status"])
        batch_data = batches.get(str(item["batch_number"] or ""))
        if not batch_data:
            continue
        if batch_data["created_at"] is None:
            batch_data["created_at"] = item["created_at"]
            batch_data["operator"] = item["operator_username"] or "-"
        node_id = int(item["node_id"])
        nodes = batch_data["nodes"]
        if node_id not in nodes:
            groups = [name for name in (item["group_names"] or "").split("、") if name]
            nodes[node_id] = {
                "node": {
                    "id": node_id,
                    "hostname": item["hostname"],
                    "ip": item["ip"],
                    "port": item["port"],
                    "is_locked": bool(item["is_locked"]),
                    "is_deleted": bool(item["is_deleted"]),
                    "nginx_available": bool(item["nginx_available"]),
                    "groups": groups,
                },
                "tasks": [],
            }
        nodes[node_id]["tasks"].append(item)
        batch_data["total"] += 1
        if item["status"] == "success":
            batch_data["success"] += 1
        elif item["status"] == "failed":
            batch_data["failed"] += 1
        else:
            batch_data["other"] += 1

    items = []
    for batch_data in batches.values():
        if batch_data["total"] <= 0:
            continue
        batch_data["nodes"] = list(batch_data["nodes"].values())
        items.append(batch_data)
    return {"items": items, "pagination": pagination}
