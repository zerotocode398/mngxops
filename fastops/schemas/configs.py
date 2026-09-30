"""FastAPI 配置管理接口响应模型。"""

from typing import Dict, List, Optional

from pydantic import BaseModel

from fastops.schemas.base import Pagination


class ConfigGroupItem(BaseModel):
    """配置列表节点组筛选项。"""

    id: int
    name: str


class ConfigNodeItem(BaseModel):
    """配置列表节点项。"""

    id: int
    hostname: str
    ip: str
    port: int
    environment: str
    status: str
    nginx_available: Optional[int] = None
    nginx_version: Optional[str] = None
    group_names: Optional[str] = None
    total: int
    synced: int
    pending: int
    orphaned: int
    failed: int
    marked_deleted: int


class UnboundConfigItem(BaseModel):
    """未绑定配置标签项。"""

    id: int
    name: str
    default_remote_path: Optional[str] = None
    source: str
    created_at: Optional[str] = None
    created_by: str


class ConfigNodeListResponse(BaseModel):
    """配置节点列表响应。"""

    items: List[ConfigNodeItem]
    pagination: Pagination
    status_counts: Dict[str, int]
    groups: List[ConfigGroupItem]
    unbound_configs: List[UnboundConfigItem]
    nginx_available_count: int
    total_nodes_count: int
