"""FastAPI 发布历史接口响应模型。"""

from typing import List, Optional

from pydantic import BaseModel

from fastops.schemas.base import Pagination


class ReleaseHistoryTaskItem(BaseModel):
    """发布历史配置任务项。"""

    id: int
    batch_number: str
    publish_version: Optional[int] = None
    version_id: Optional[int] = None
    remote_path: str
    status: str
    status_label: str
    result: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: Optional[str] = None
    binding_id: Optional[int] = None
    config_id: int
    node_id: int
    operator_id: int
    config_name: str
    hostname: str
    ip: str
    port: int
    is_locked: bool
    is_deleted: bool
    nginx_available: bool
    operator_username: str
    group_names: str


class ReleaseHistoryNodeInfo(BaseModel):
    """发布历史节点摘要。"""

    id: int
    hostname: str
    ip: str
    port: int
    is_locked: bool
    is_deleted: bool
    nginx_available: bool
    groups: List[str]


class ReleaseHistoryNodeGroup(BaseModel):
    """发布历史节点分组。"""

    node: ReleaseHistoryNodeInfo
    tasks: List[ReleaseHistoryTaskItem]


class ReleaseHistoryBatchItem(BaseModel):
    """发布历史批次项。"""

    batch_number: str
    operator: str
    created_at: Optional[str] = None
    total: int
    success: int
    failed: int
    other: int
    nodes: List[ReleaseHistoryNodeGroup]


class ReleaseHistoryListResponse(BaseModel):
    """发布历史批次分页响应。"""

    items: List[ReleaseHistoryBatchItem]
    pagination: Pagination
