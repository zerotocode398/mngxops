"""FastAPI 资产 JSON 接口。"""

from fastapi import APIRouter, Depends, Query

from fastops.api.deps import ensure_permission, require_user
from fastops.schemas.assets import (
    CredentialListResponse,
    NodeListResponse,
)
from fastops.schemas.system import HealthResponse, SnapshotResponse
from fastops.services.assets import (
    get_migration_snapshot,
    list_credentials,
    list_nodes,
)
from fastops.services.pagination import normalize_page
from fastops.services.users import FastUser

router = APIRouter(prefix="/api/v1", tags=["资产"])


@router.get("/healthz", response_model=HealthResponse, tags=["系统"], summary="健康检查")
def healthz():
    """返回 FastAPI 服务健康状态。"""
    snapshot = get_migration_snapshot()
    return {"status": "ok", "database": snapshot["db_path"]}


@router.get("/snapshot", response_model=SnapshotResponse, tags=["系统"], summary="迁移快照")
def snapshot(user: FastUser = Depends(require_user)):
    """返回数据库轻量快照。"""
    ensure_permission(user, "nodes", "read")
    return get_migration_snapshot()


@router.get("/nodes", response_model=NodeListResponse, summary="查询节点列表")
def nodes_api(
    search: str = Query("", description="主机名或 IP 搜索词"),
    status: str = Query("", description="SSH 状态：online/offline/unknown"),
    page: int = Query(1, ge=1, description="页码"),
    per_page: int = Query(10, description="每页条数：10/20/50/100"),
    user: FastUser = Depends(require_user),
):
    """分页查询节点列表。"""
    ensure_permission(user, "nodes", "read")
    page, per_page = normalize_page(str(page), str(per_page))
    return list_nodes(search=search, status=status, page=page, per_page=per_page)


@router.get("/credentials", response_model=CredentialListResponse, summary="查询凭证列表")
def credentials_api(
    search: str = Query("", description="凭证名称或 SSH 用户搜索词"),
    auth_type: str = Query("", description="认证方式：password/key"),
    status: str = Query("", description="启用状态：enabled/disabled"),
    page: int = Query(1, ge=1, description="页码"),
    per_page: int = Query(10, description="每页条数：10/20/50/100"),
    user: FastUser = Depends(require_user),
):
    """分页查询凭证列表。"""
    ensure_permission(user, "credentials", "read")
    page, per_page = normalize_page(str(page), str(per_page))
    return list_credentials(
        search=search,
        auth_type=auth_type,
        status=status,
        page=page,
        per_page=per_page,
    )
