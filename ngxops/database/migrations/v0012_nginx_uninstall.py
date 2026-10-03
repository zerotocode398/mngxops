"""建立 Nginx 卸载历史快照并补齐独立权限。"""

from ngxops.database.migrations.definition import Migration


def upgrade(connection) -> None:
    """创建卸载快照表并迁移既有节点查看和编辑授权。"""
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_nginx_uninstall_runs ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "task_id INTEGER NOT NULL UNIQUE REFERENCES ngxops_tasks(id) ON DELETE CASCADE, "
        "node_id INTEGER REFERENCES ngxops_nodes(id) ON DELETE SET NULL, "
        "batch_number VARCHAR(32) NOT NULL, "
        "node_hostname VARCHAR(100) NOT NULL, "
        "node_ip VARCHAR(45) NOT NULL, "
        "install_origin VARCHAR(20) NOT NULL CHECK (install_origin IN ('source', 'package')), "
        "package_manager VARCHAR(10) NOT NULL DEFAULT '', "
        "package_name VARCHAR(200) NOT NULL DEFAULT '', "
        "resolved_prefix VARCHAR(500) NOT NULL DEFAULT '', "
        "backup_path VARCHAR(500) NOT NULL DEFAULT '', "
        "work_dir VARCHAR(500) NOT NULL DEFAULT '', "
        "options_json TEXT NOT NULL DEFAULT '{}', "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_uninstall_runs_batch_created "
        "ON ngxops_nginx_uninstall_runs (batch_number, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_uninstall_runs_node_created "
        "ON ngxops_nginx_uninstall_runs (node_id, created_at)"
    )
    connection.exec_driver_sql(
        "INSERT OR IGNORE INTO ngxops_permission_items (code, name, resource, action) "
        "VALUES ('nginx_uninstall.read', '卸载历史查看', 'nginx_uninstall', 'read')"
    )
    connection.exec_driver_sql(
        "INSERT OR IGNORE INTO ngxops_permission_items (code, name, resource, action) "
        "VALUES ('nginx_uninstall.execute', '执行卸载', 'nginx_uninstall', 'execute')"
    )
    connection.exec_driver_sql(
        "INSERT OR IGNORE INTO ngxops_role_permissions (role_id, permission_id) "
        "SELECT source.role_id, target.id FROM ngxops_role_permissions AS source "
        "JOIN ngxops_permission_items AS old ON old.id = source.permission_id "
        "JOIN ngxops_permission_items AS target ON target.code = 'nginx_uninstall.read' "
        "WHERE old.code = 'nodes.read'"
    )
    connection.exec_driver_sql(
        "INSERT OR IGNORE INTO ngxops_role_permissions (role_id, permission_id) "
        "SELECT source.role_id, target.id FROM ngxops_role_permissions AS source "
        "JOIN ngxops_permission_items AS old ON old.id = source.permission_id "
        "JOIN ngxops_permission_items AS target ON target.code = 'nginx_uninstall.execute' "
        "WHERE old.code = 'nodes.update'"
    )
    connection.exec_driver_sql(
        "INSERT OR IGNORE INTO ngxops_profile_permissions (user_id, permission_id) "
        "SELECT source.user_id, target.id FROM ngxops_profile_permissions AS source "
        "JOIN ngxops_permission_items AS old ON old.id = source.permission_id "
        "JOIN ngxops_permission_items AS target ON target.code = 'nginx_uninstall.read' "
        "WHERE old.code = 'nodes.read'"
    )
    connection.exec_driver_sql(
        "INSERT OR IGNORE INTO ngxops_profile_permissions (user_id, permission_id) "
        "SELECT source.user_id, target.id FROM ngxops_profile_permissions AS source "
        "JOIN ngxops_permission_items AS old ON old.id = source.permission_id "
        "JOIN ngxops_permission_items AS target ON target.code = 'nginx_uninstall.execute' "
        "WHERE old.code = 'nodes.update'"
    )


MIGRATION = Migration(version=12, name="nginx_uninstall", upgrade=upgrade)
