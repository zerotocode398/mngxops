"""FastAPI 发布历史 JSON 接口。"""

from fastapi import APIRouter, Depends, Query

from fastops.api.deps import ensure_permission, require_user
from fastops.schemas.base import OptionListResponse
from fastops.schemas.releases import ReleaseHistoryListResponse
from fastops.services.pagination import normalize_page
from fastops.services.releases import list_release_history_batches, release_status_options
from fastops.services.users import FastUser

router = APIRouter(prefix="/api/v1/releases", tags=["发布历史"])


@router.get(
    "/options/status",
    response_model=OptionListResponse,
    summary="查询发布状态选项",
)
def release_status_options_api(user: FastUser = Depends(require_user)):
    """查询发布历史状态筛选选项。"""
    ensure_permission(user, "releases", "read")
    return {"items": release_status_options()}


@router.get(
    "/history",
    response_model=ReleaseHistoryListResponse,
    summary="查询发布历史批次列表",
)
def release_history_api(
    search: str = Query("", description="逗号分隔搜索词，匹配批次、节点、配置或操作人"),
    status_filter: str = Query("", alias="status", description="发布任务状态"),
    batch: str = Query("", description="批次号过滤"),
    node_ip: str = Query("", description="节点 IP 过滤"),
    page: int = Query(1, ge=1, description="页码"),
    per_page: int = Query(10, description="每页批次数：10/20/50/100"),
    user: FastUser = Depends(require_user),
):
    """按批次分页查询发布历史。"""
    ensure_permission(user, "releases", "read")
    page, per_page = normalize_page(str(page), str(per_page))
    return list_release_history_batches(
        search=search,
        status=status_filter,
        batch=batch,
        node_ip=node_ip,
        page=page,
        per_page=per_page,
    )
