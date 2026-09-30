"""分页参数与查询串工具。"""

from typing import Dict, Mapping, Tuple
from urllib.parse import urlencode


def normalize_page(raw_page: str, raw_per_page: str) -> Tuple[int, int]:
    """规范化分页参数。"""
    try:
        page = max(1, int(raw_page or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(raw_per_page or 10)
    except (TypeError, ValueError):
        per_page = 10
    if per_page not in (10, 20, 50, 100):
        per_page = 10
    return page, per_page


def build_pagination(total: int, page: int, per_page: int) -> Dict[str, int]:
    """生成模板使用的分页上下文。"""
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), total_pages)
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "offset": (page - 1) * per_page,
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_page": max(1, page - 1),
        "next_page": min(total_pages, page + 1),
    }


def build_page_query(filters: Mapping[str, str], per_page: int) -> str:
    """生成分页链接复用的查询串。"""
    params = {key: value for key, value in filters.items() if value}
    params["per_page"] = str(per_page)
    return urlencode(params)
