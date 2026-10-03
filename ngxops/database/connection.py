"""创建 SQLite Engine 与请求/任务使用的 Session 工厂。"""

from dataclasses import dataclass

from sqlalchemy import URL, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ngxops.config import Settings


SQLITE_BUSY_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Database:
    """封装应用共享的 Engine 和 Session 工厂。"""

    engine: Engine
    session_factory: sessionmaker


def create_database(settings: Settings) -> Database:
    """创建惰性连接的 SQLite Engine 与独立 Session 工厂。"""
    database_url = URL.create("sqlite", database=str(settings.database_path))
    engine = create_engine(
        database_url,
        hide_parameters=True,
        connect_args={
            "check_same_thread": False,
            "timeout": SQLITE_BUSY_TIMEOUT_SECONDS,
        },
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite_connection(dbapi_connection, connection_record) -> None:
        """为新 SQLite 连接开启外键约束并设置锁等待时间。"""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute(
                "PRAGMA busy_timeout = {}".format(
                    SQLITE_BUSY_TIMEOUT_SECONDS * 1000
                )
            )
        finally:
            cursor.close()

    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    return Database(engine=engine, session_factory=session_factory)
