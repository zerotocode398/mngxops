"""提供系统设置分组页面和结构化 JSON API。"""

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import Response

from ngxops.accounts.models import User
from ngxops.api.contracts import api_error_responses
from ngxops.audit.service import (
    prepare_audit_session,
    request_client_ip,
    write_audit_log,
)
from ngxops.database.session import get_session
from ngxops.security.dependencies import require_permission, require_superuser
from ngxops.settings.models import PRESET_SETTINGS, SystemSetting, preset_by_key
from ngxops.settings.service import initialize_defaults, save_group_settings
from ngxops.ui import render_page


router = APIRouter(tags=["settings"])


class SettingItemResponse(BaseModel):
    """描述一个可读取的预置设置项。"""

    key: str
    value: str
    type: str
    label: str
    description: str
    placeholder: str
    required: bool
    min_value: Optional[int] = None
    max_value: Optional[int] = None
    unit: str = ""


class SettingsGroupResponse(BaseModel):
    """描述一个设置分组及其当前值。"""

    success: bool = True
    group: str
    settings: List[SettingItemResponse]


class SettingsAllResponse(BaseModel):
    """描述全部预置设置的扁平键值映射。"""

    success: bool = True
    settings: Dict[str, str]


class SettingsGroupUpdateRequest(BaseModel):
    """描述一个分组的批量设置值。"""

    group: str = Field(..., min_length=1, max_length=50)
    values: Dict[str, str]


class SettingsUpdateResponse(BaseModel):
    """描述一次设置分组保存结果。"""

    success: bool = True
    message: str
    saved: List[str]


def _item_response(row: SystemSetting) -> dict:
    """将数据库设置与预置校验元数据组合为 API 字段。"""
    preset = preset_by_key().get(row.key, {})
    return {
        "key": row.key,
        "value": row.value,
        "type": row.value_type,
        "label": row.label,
        "description": row.description,
        "placeholder": row.placeholder,
        "required": bool(row.is_required),
        "min_value": preset.get("min_value"),
        "max_value": preset.get("max_value"),
        "unit": preset.get("unit", ""),
    }


def _load_settings(session: Session) -> List[SystemSetting]:
    """初始化缺失的默认值并返回按分组排序的预置项。"""
    initialize_defaults(session)
    session.commit()
    keys = [item["key"] for item in PRESET_SETTINGS]
    rows = session.scalars(
        select(SystemSetting)
        .where(SystemSetting.key.in_(keys))
        .order_by(SystemSetting.group_name, SystemSetting.sort_order, SystemSetting.key)
    ).all()
    return rows


@router.get("/settings/", include_in_schema=False)
def settings_index(
    request: Request,
    group: str = Query("", max_length=50),
    user: User = Depends(require_permission("settings", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """渲染按功能分组的运行设置页面。"""
    rows = _load_settings(session)
    groups = {}
    for row in rows:
        groups.setdefault(row.group_name, []).append(row)
    group_names = list(groups)
    active_group = group if group in groups else (group_names[0] if group_names else "")
    return render_page(
        request,
        "settings/index.html",
        {
            "groups": groups,
            "group_names": group_names,
            "active_group": active_group,
            "can_update": user.is_superuser,
            "presets": preset_by_key(),
        },
        user,
        session,
    )


@router.get(
    "/api/settings/group",
    response_model=SettingsGroupResponse,
    tags=["settings"],
    summary="读取设置分组",
    responses=api_error_responses((401, 403, 422, 500)),
)
def get_settings_group(
    group: str = Query("", max_length=50),
    _user: User = Depends(require_permission("settings", "read")),
    session: Session = Depends(get_session),
) -> SettingsGroupResponse:
    """返回一个设置分组中的值和展示信息。"""
    rows = _load_settings(session)
    selected = [row for row in rows if row.group_name == group]
    return SettingsGroupResponse(
        group=group,
        settings=[SettingItemResponse(**_item_response(row)) for row in selected],
    )


@router.get(
    "/api/settings/all",
    response_model=SettingsAllResponse,
    tags=["settings"],
    summary="读取全部系统设置",
    responses=api_error_responses((401, 403, 500)),
)
def get_all_settings(
    _user: User = Depends(require_permission("settings", "read")),
    session: Session = Depends(get_session),
) -> SettingsAllResponse:
    """返回全部预置设置的键值映射。"""
    rows = _load_settings(session)
    return SettingsAllResponse(settings={row.key: row.value for row in rows})


@router.post(
    "/api/settings/group",
    response_model=SettingsUpdateResponse,
    tags=["settings"],
    summary="保存设置分组",
    responses=api_error_responses((400, 401, 403, 404, 409, 422, 500)),
)
def update_settings_group(
    request: Request,
    payload: SettingsGroupUpdateRequest,
    user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> SettingsUpdateResponse:
    """以超级管理员身份校验并保存一个分组。"""
    prepare_audit_session(session, user.id, user.username, request_client_ip(request))
    try:
        saved = save_group_settings(session, payload.group, payload.values, user.id)
    except LookupError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        session.rollback()
        raise
    if saved:
        write_audit_log(
            session,
            "系统设置",
            "更新系统设置",
            "分组「{}」修改 {} 项：{}".format(
                payload.group, len(saved), ", ".join(saved[:20])
            ),
        )
    session.commit()
    return SettingsUpdateResponse(
        message="设置已保存" if saved else "设置没有变化",
        saved=saved,
    )
