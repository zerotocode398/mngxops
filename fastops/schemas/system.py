"""FastAPI 系统接口响应模型。"""

from typing import Dict, List

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str = Field(..., description="服务状态")
    database: str = Field(..., description="SQLite 数据库路径")


class SnapshotResponse(BaseModel):
    """迁移快照响应。"""

    db_path: str
    counts: Dict[str, int]
    recent_tasks: List[Dict[str, object]]
