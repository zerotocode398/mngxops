"""检查并显式执行 SQLite 结构迁移。"""

from typing import Iterable, List, Set, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ngxops.database.migrations import MIGRATIONS, Migration


MIGRATION_TABLE = "ngxops_schema_migrations"


class LegacyDatabaseError(RuntimeError):
    """表示数据库含有未登记的既有表，迁移已拒绝执行。"""


class MigrationStateError(RuntimeError):
    """表示数据库迁移版本与当前代码不兼容。"""


def _validate_migrations(migrations: Iterable[Migration]) -> Tuple[Migration, ...]:
    """校验迁移版本唯一且按升序登记。"""
    ordered = tuple(migrations)
    versions = [migration.version for migration in ordered]
    if any(version < 1 for version in versions):
        raise ValueError("迁移版本必须是正整数")
    if versions != sorted(set(versions)):
        raise ValueError("迁移版本必须唯一并按升序登记")
    return ordered


def _read_table_names(connection: Connection) -> Set[str]:
    """读取 SQLite 用户表名称，不包含内部 sqlite 表。"""
    result = connection.exec_driver_sql(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    return {row[0] for row in result}


def _read_applied_versions(connection: Connection) -> Set[int]:
    """读取本项目已登记的迁移版本。"""
    result = connection.execute(
        text("SELECT version FROM {}".format(MIGRATION_TABLE))
    )
    return {row[0] for row in result}


def upgrade_database(
    engine: Engine,
    migrations: Iterable[Migration] = MIGRATIONS,
) -> Tuple[Migration, ...]:
    """在单个写锁事务中应用尚未执行的数据库迁移。"""
    ordered = _validate_migrations(migrations)
    applied_now: List[Migration] = []

    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            table_names = _read_table_names(connection)
            has_migration_table = MIGRATION_TABLE in table_names
            if table_names and not has_migration_table:
                raise LegacyDatabaseError(
                    "检测到未登记的既有数据表；本次未修改数据库。"
                    "请先核对 schema 基线并完成显式登记。"
                )

            if not has_migration_table:
                connection.exec_driver_sql(
                    "CREATE TABLE {} ("
                    "version INTEGER PRIMARY KEY CHECK (version > 0), "
                    "name TEXT NOT NULL UNIQUE, "
                    "applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
                    ")".format(MIGRATION_TABLE)
                )

            applied_versions = _read_applied_versions(connection)
            known_versions = {migration.version for migration in ordered}
            unknown_versions = applied_versions - known_versions
            if unknown_versions:
                raise MigrationStateError(
                    "数据库包含当前代码未登记的迁移版本：{}".format(
                        ", ".join(str(version) for version in sorted(unknown_versions))
                    )
                )

            for migration in ordered:
                if migration.version in applied_versions:
                    continue
                if any(version > migration.version for version in applied_versions):
                    raise MigrationStateError(
                        "迁移 {} 缺失，但数据库已应用更高版本".format(
                            migration.version
                        )
                    )
                migration.upgrade(connection)
                connection.execute(
                    text(
                        "INSERT INTO {} (version, name) VALUES (:version, :name)".format(
                            MIGRATION_TABLE
                        )
                    ),
                    {"version": migration.version, "name": migration.name},
                )
                applied_versions.add(migration.version)
                applied_now.append(migration)

            connection.commit()
        except Exception:
            connection.rollback()
            raise

    return tuple(applied_now)
