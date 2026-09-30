"""FastAPI 系统设置接口响应模型。"""

from typing import Dict, List, Optional

from pydantic import BaseModel


class SettingGroupItem(BaseModel):
    """系统设置分组项。"""

    name: str
    description: str
    count: int


class SettingGroupListResponse(BaseModel):
    """系统设置分组列表响应。"""

    items: List[SettingGroupItem]


class SettingItem(BaseModel):
    """系统设置列表项。"""

    id: int
    key: str
    value: str
    value_type: str
    group: str
    label: str
    description: str
    placeholder: str
    options: str
    is_required: bool
    sort_order: int
    unit: str
    updated_by: str
    updated_at: Optional[str] = None


class SettingListResponse(BaseModel):
    """系统设置列表响应。"""

    items: List[SettingItem]


class SettingValueMapResponse(BaseModel):
    """系统设置 key-value 响应。"""

    settings: Dict[str, str]
