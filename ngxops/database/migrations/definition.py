"""声明数据库迁移的稳定版本信息。"""

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class Migration:
    """描述一个具备稳定版本号的数据库结构迁移。"""

    version: int
    name: str
    upgrade: Callable[[Connection], None]

