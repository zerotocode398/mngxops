"""建立与节点导入设置分离的配置同步路径表。"""

from ngxops.database.migrations.definition import Migration


def upgrade(connection) -> None:
    """创建每节点唯一的配置发现主路径设置。"""
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_config_sync_settings ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "node_id INTEGER NOT NULL UNIQUE "
        "REFERENCES ngxops_nodes(id) ON DELETE CASCADE, "
        "main_conf_path VARCHAR(500) NOT NULL DEFAULT '', "
        "updated_by INTEGER REFERENCES auth_user(id) ON DELETE SET NULL, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )


MIGRATION = Migration(
    version=9,
    name="config_sync_settings",
    upgrade=upgrade,
)
