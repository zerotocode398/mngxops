"""FastAPI 任务中心 JSON 接口。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from fastops.api.deps import require_user
from fastops.schemas.base import OptionListResponse
from fastops.schemas.tasks import TaskCenterDetailResponse, TaskCenterListResponse
from fastops.services.pagination import normalize_page
from fastops.services.tasks import (
    get_task_center_task,
    list_task_center_tasks,
    operation_type_options,
    status_options,
    user_can_read_task_center,
)
from fastops.services.users import FastUser

router = APIRouter(prefix="/api/v1/tasks", tags=["任务中心"])


def _ensure_task_center_access(user: FastUser) -> None:
    """校验任务中心访问权限。"""
    if not user_can_read_task_center(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前账号没有使用该功能的权限",
        )


@router.get(
    "/options/status",
    response_model=OptionListResponse,
    summary="查询任务状态选项",
)
def task_status_options_api(user: FastUser = Depends(require_user)):
    """查询任务状态筛选选项。"""
    _ensure_task_center_access(user)
    return {"items": status_options()}


@router.get(
    "/options/operation-types",
    response_model=OptionListResponse,
    summary="查询任务类型选项",
)
def task_operation_options_api(user: FastUser = Depends(require_user)):
    """查询任务类型筛选选项。"""
    _ensure_task_center_access(user)
    return {"items": operation_type_options()}


@router.get("", response_model=TaskCenterListResponse, summary="查询任务中心列表")
def task_center_list_api(
    search: str = Query("", description="逗号分隔搜索词，匹配批次、主机名或 IP"),
    status_filter: str = Query("", alias="status", description="任务状态"),
    operation_type: str = Query("", description="任务类型"),
    page: int = Query(1, ge=1, description="页码"),
    per_page: int = Query(10, description="每页条数：10/20/50/100"),
    user: FastUser = Depends(require_user),
):
    """分页查询任务中心列表。"""
    _ensure_task_center_access(user)
    page, per_page = normalize_page(str(page), str(per_page))
    return list_task_center_tasks(
        user=user,
        search=search,
        status=status_filter,
        operation_type=operation_type,
        page=page,
        per_page=per_page,
    )


@router.get(
    "/{task_id}",
    response_model=TaskCenterDetailResponse,
    summary="查询任务中心详情",
)
def task_center_detail_api(task_id: int, user: FastUser = Depends(require_user)):
    """查询任务中心详情。"""
    _ensure_task_center_access(user)
    task = get_task_center_task(user, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return task
