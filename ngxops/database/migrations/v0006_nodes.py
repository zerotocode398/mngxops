"""建立节点、节点组、成员关联及远程配置路径表。"""

from ngxops.database.migrations.definition import Migration


def upgrade(connection) -> None:
    """创建节点资产相关表和查询索引。"""
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_node_groups ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "name VARCHAR(100) NOT NULL UNIQUE, "
        "description TEXT NOT NULL DEFAULT '', "
        "created_by INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_node_groups_created_at "
        "ON ngxops_node_groups (created_at)"
    )
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_nodes ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "hostname VARCHAR(100) NOT NULL, "
        "ip VARCHAR(45) NOT NULL UNIQUE, "
        "port INTEGER NOT NULL DEFAULT 22 CHECK (port >= 1 AND port <= 65535), "
        "credential_id INTEGER REFERENCES ngxops_credentials(id) ON DELETE SET NULL, "
        "environment VARCHAR(20) NOT NULL DEFAULT 'dev' "
        "CHECK (environment IN ('dev', 'test', 'prod')), "
        "nginx_version VARCHAR(50) NOT NULL DEFAULT '', "
        "nginx_path VARCHAR(255) NOT NULL DEFAULT '/usr/sbin/nginx', "
        "nginx_available BOOLEAN, "
        "last_nginx_probe_at DATETIME, "
        "status VARCHAR(20) NOT NULL DEFAULT 'unknown' "
        "CHECK (status IN ('online', 'offline', 'unknown')), "
        "last_probe_at DATETIME, "
        "is_locked BOOLEAN NOT NULL DEFAULT 0, "
        "description TEXT NOT NULL DEFAULT '', "
        "is_deleted BOOLEAN NOT NULL DEFAULT 0, "
        "deleted_at DATETIME, "
        "deleted_by INTEGER REFERENCES auth_user(id) ON DELETE SET NULL, "
        "created_by INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_nodes_is_deleted ON ngxops_nodes (is_deleted)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_nodes_status_created_at "
        "ON ngxops_nodes (status, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_nodes_environment ON ngxops_nodes (environment)"
    )
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_node_group_members ("
        "node_id INTEGER NOT NULL REFERENCES ngxops_nodes(id) ON DELETE CASCADE, "
        "group_id INTEGER NOT NULL REFERENCES ngxops_node_groups(id) ON DELETE CASCADE, "
        "PRIMARY KEY (node_id, group_id)"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_node_group_members_group "
        "ON ngxops_node_group_members (group_id, node_id)"
    )
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_node_sync_settings ("
        "node_id INTEGER NOT NULL PRIMARY KEY "
        "REFERENCES ngxops_nodes(id) ON DELETE CASCADE, "
        "main_conf_path VARCHAR(500) NOT NULL DEFAULT '/etc/nginx/nginx.conf', "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )


MIGRATION = Migration(version=6, name="nodes", upgrade=upgrade)
