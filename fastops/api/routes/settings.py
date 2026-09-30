"""FastAPI 系统设置 JSON 接口。"""

from fastapi import APIRouter, Depends, Query

from fastops.api.deps import ensure_permission, require_user
from fastops.schemas.settings import (
    SettingGroupListResponse,
    SettingListResponse,
    SettingValueMapResponse,
)
from fastops.services.settings import list_setting_groups, list_settings, settings_value_map
from fastops.services.users import FastUser

router = APIRouter(prefix="/api/v1/settings", tags=["系统设置"])


@router.get("/groups", response_model=SettingGroupListResponse, summary="查询系统设置分组")
def setting_groups_api(user: FastUser = Depends(require_user)):
    """查询系统设置分组摘要。"""
    ensure_permission(user, "settings", "read")
    return {"items": list_setting_groups()}


@router.get("", response_model=SettingListResponse, summary="查询系统设置列表")
def settings_list_api(
    group: str = Query("", description="配置分组名称"),
    user: FastUser = Depends(require_user),
):
    """查询已接线系统设置列表。"""
    ensure_permission(user, "settings", "read")
    return {"items": list_settings(group=group)}


@router.get("/values", response_model=SettingValueMapResponse, summary="查询系统设置键值")
def settings_values_api(user: FastUser = Depends(require_user)):
    """查询全部已接线系统设置 key-value。"""
    ensure_permission(user, "settings", "read")
    return {"settings": settings_value_map()}
