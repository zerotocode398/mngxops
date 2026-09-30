"""FastAPI 审计接口响应模型。"""

from typing import List, Optional

from pydantic import BaseModel

from fastops.schemas.base import Pagination


class AuditLogItem(BaseModel):
    """操作日志列表项。"""

    id: int
    username: str
    module: str
    action: str
    ip: str
    result: str
    detail: str
    task_center_id: Optional[int] = None
    source_batch: Optional[str] = None
    created_at: Optional[str] = None


class AuditLogListResponse(BaseModel):
    """操作日志列表响应。"""

    items: List[AuditLogItem]
    pagination: Pagination


class LoginLogItem(BaseModel):
    """登录日志列表项。"""

    id: int
    username: str
    ip: str
    user_agent: str
    status: str
    fail_reason: str
    fail_reason_label: str
    created_at: Optional[str] = None


class LoginLogListResponse(BaseModel):
    """登录日志列表响应。"""

    items: List[LoginLogItem]
    pagination: Pagination


class AuditModuleListResponse(BaseModel):
    """审计模块列表响应。"""

    items: List[str]
