"""FastAPI 任务中心接口响应模型。"""

from typing import List, Optional

from pydantic import BaseModel

from fastops.schemas.base import Pagination


class TaskCenterItem(BaseModel):
    """任务中心列表项。"""

    id: int
    operation_type: str
    operation_label: str
    status: str
    status_label: str
    detail: str
    result: str
    progress: int
    source_batch: str
    target_hostnames: str
    target_ips: str
    target_configs: str
    trigger_username: str
    summary_primary: str
    summary_secondary: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class TaskCenterListResponse(BaseModel):
    """任务中心列表响应。"""

    items: List[TaskCenterItem]
    pagination: Pagination


class TaskCenterDetailResponse(TaskCenterItem):
    """任务中心详情响应。"""

    log_output: str
