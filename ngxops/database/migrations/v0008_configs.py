"""创建配置标签、节点绑定及绑定版本表。"""

from ngxops.database.migrations.definition import Migration


def upgrade(connection) -> None:
    """建立配置管理核心表、约束与常用查询索引。"""
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_configs ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "name VARCHAR(255) NOT NULL, "
        "default_remote_path VARCHAR(500) NOT NULL DEFAULT '', "
        "template_content TEXT NOT NULL DEFAULT '', "
        "source VARCHAR(20) NOT NULL DEFAULT 'manual', "
        "description TEXT NOT NULL DEFAULT '', "
        "created_by INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_configs_updated_at ON ngxops_configs (updated_at)"
    )
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_config_bindings ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "config_id INTEGER NOT NULL REFERENCES ngxops_configs(id) ON DELETE CASCADE, "
        "node_id INTEGER NOT NULL REFERENCES ngxops_nodes(id) ON DELETE CASCADE, "
        "remote_path VARCHAR(500) NOT NULL, "
        "content TEXT NOT NULL, "
        "current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1), "
        "sync_status VARCHAR(20) NOT NULL DEFAULT 'not_synced' "
        "CHECK (sync_status IN ('not_synced', 'synced', 'modified', 'orphaned', "
        "'failed', 'marked_deleted')), "
        "synced_version INTEGER, "
        "last_sync_time DATETIME, "
        "last_sync_error TEXT NOT NULL DEFAULT '', "
        "last_sync_task_id BIGINT, "
        "remote_content_hash VARCHAR(64) NOT NULL DEFAULT '', "
        "drift_detected_at DATETIME, "
        "source VARCHAR(20) NOT NULL DEFAULT 'manual', "
        "created_by INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT uq_ngxops_config_bindings_config_node UNIQUE (config_id, node_id)"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_config_bindings_node_status "
        "ON ngxops_config_bindings (node_id, sync_status)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_config_bindings_updated_at "
        "ON ngxops_config_bindings (updated_at)"
    )
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_binding_versions ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "binding_id INTEGER NOT NULL REFERENCES ngxops_config_bindings(id) "
        "ON DELETE CASCADE, "
        "version INTEGER NOT NULL CHECK (version >= 1), "
        "content TEXT NOT NULL, "
        "remark TEXT NOT NULL DEFAULT '', "
        "created_by INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT uq_ngxops_binding_versions_binding_version "
        "UNIQUE (binding_id, version)"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_binding_versions_created_at "
        "ON ngxops_binding_versions (created_at)"
    )


MIGRATION = Migration(version=8, name="configs", upgrade=upgrade)
