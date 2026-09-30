"""SQLite 数据访问基础设施。"""

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from fastops.core.config import get_settings


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """打开 SQLite 连接并自动关闭。"""
    conn = sqlite3.connect(str(get_settings().database_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """判断指定表是否存在。"""
    row = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def count_table(conn: sqlite3.Connection, table_name: str) -> int:
    """统计表记录数，表不存在时返回 0。"""
    if not table_exists(conn, table_name):
        return 0
    row = conn.execute(f"select count(*) as total from {table_name}").fetchone()
    return int(row["total"] or 0)

