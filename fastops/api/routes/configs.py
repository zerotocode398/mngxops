"""FastAPI 配置管理 JSON 接口。"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query

from fastops.api.deps import ensure_permission, require_user
from fastops.schemas.configs import ConfigNodeListResponse
from fastops.services.configs import list_config_nodes
from fastops.services.pagination import normalize_page
from fastops.services.users import FastUser

router = APIRouter(prefix="/api/v1/configs", tags=["配置管理"])


@router.get("/nodes", response_model=ConfigNodeListResponse, summary="查询配置节点列表")
def config_nodes_api(
    search: str = Query("", description="逗号分隔搜索词，匹配节点、配置名称或远程路径"),
    group_id: str = Query("", description="节点组 ID"),
    sync_status: Optional[
        Literal["pending", "synced", "orphaned", "failed", "marked_deleted"]
    ] = Query(None, description="同步状态"),
    nginx_available: Literal["true", "all"] = Query(
        "true",
        description="是否仅展示 Nginx 已识别节点",
    ),
    page: int = Query(1, ge=1, description="页码"),
    per_page: int = Query(10, description="每页条数：10/20/50/100"),
    user: FastUser = Depends(require_user),
):
    """分页查询配置列表节点视图。"""
    ensure_permission(user, "configs", "read")
    page, per_page = normalize_page(str(page), str(per_page))
    return list_config_nodes(
        search=search,
        group_id=group_id,
        sync_status=sync_status or "",
        nginx_available=nginx_available,
        page=page,
        per_page=per_page,
    )
