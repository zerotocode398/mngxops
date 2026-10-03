"""定义应用 SQLAlchemy 模型共享的元数据基类。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """保存业务模型共用的 SQLAlchemy 元数据。"""

