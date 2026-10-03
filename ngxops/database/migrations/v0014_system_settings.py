"""创建系统设置表。"""

from ngxops.database.migrations.definition import Migration


def upgrade(connection) -> None:
    """建立预置设置存储并保留唯一键和用户删除语义。"""
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_system_settings ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "key VARCHAR(100) NOT NULL UNIQUE, "
        "value TEXT NOT NULL, "
        "type VARCHAR(20) NOT NULL DEFAULT 'string', "
        "\"group\" VARCHAR(50) NOT NULL, "
        "label VARCHAR(100) NOT NULL, "
        "description TEXT NOT NULL DEFAULT '', "
        "placeholder VARCHAR(255) NOT NULL DEFAULT '', "
        "options TEXT NOT NULL DEFAULT '', "
        "is_required INTEGER NOT NULL DEFAULT 1, "
        "sort_order INTEGER NOT NULL DEFAULT 0, "
        "updated_by INTEGER REFERENCES auth_user(id) ON DELETE SET NULL, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_system_settings_group_order "
        "ON ngxops_system_settings (\"group\", sort_order)"
    )


MIGRATION = Migration(version=14, name="system_settings", upgrade=upgrade)
