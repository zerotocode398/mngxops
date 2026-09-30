"""FastAPI 通用响应模型。"""

from typing import List

from pydantic import BaseModel, Field


class Pagination(BaseModel):
    """分页信息。"""

    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    per_page: int = Field(..., description="每页条数")
    total_pages: int = Field(..., description="总页数")
    has_previous: bool = Field(..., description="是否有上一页")
    has_next: bool = Field(..., description="是否有下一页")


class OptionItem(BaseModel):
    """通用下拉选项。"""

    value: str
    label: str


class OptionListResponse(BaseModel):
    """通用下拉选项响应。"""

    items: List[OptionItem]
