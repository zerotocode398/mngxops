"""SQLite 查询条件构造工具。"""

from typing import List, Sequence, Tuple


def split_search_tags(search: str) -> List[str]:
    """将中英文逗号分隔的搜索词拆成非空列表。"""
    if not search:
        return []
    return [item.strip() for item in search.replace("，", ",").split(",") if item.strip()]


def where_clause(parts: Sequence[str]) -> str:
    """拼接 SQL WHERE 子句。"""
    return " where " + " and ".join(parts) if parts else ""


def date_range_filters(
    field_name: str,
    date_from: str,
    date_to: str,
) -> Tuple[List[str], List[object]]:
    """生成日期范围筛选条件与参数。"""
    filters: List[str] = []
    params: List[object] = []
    if date_from:
        filters.append(f"{field_name} >= ?")
        params.append(date_from)
    if date_to:
        filters.append(f"{field_name} <= ?")
        params.append(f"{date_to} 23:59:59")
    return filters, params
