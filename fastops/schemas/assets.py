"""FastAPI 资产接口响应模型。"""

from typing import List, Optional

from pydantic import BaseModel

from fastops.schemas.base import Pagination


class NodeItem(BaseModel):
    """节点列表项。"""

    id: int
    hostname: str
    ip: str
    port: int
    environment: str
    status: str
    nginx_version: Optional[str] = None
    nginx_available: Optional[int] = None
    is_locked: bool
    credential_name: Optional[str] = None
    group_names: Optional[str] = None
    last_probe_at: Optional[str] = None


class CredentialItem(BaseModel):
    """凭证列表项。"""

    id: int
    name: str
    username: str
    auth_type: str
    is_enabled: bool
    node_count: int
    last_test_time: Optional[str] = None
    last_test_result: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[str] = None


class NodeListResponse(BaseModel):
    """节点列表响应。"""

    items: List[NodeItem]
    pagination: Pagination


class CredentialListResponse(BaseModel):
    """凭证列表响应。"""

    items: List[CredentialItem]
    pagination: Pagination
