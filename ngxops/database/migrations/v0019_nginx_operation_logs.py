"""为 Nginx 安装、卸载和启停任务添加完整操作日志表。"""

from ngxops.database.migrations.definition import Migration


def upgrade(connection) -> None:
    """创建按任务与目标节点查询的远程操作日志表。"""
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_nginx_operation_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "operation_type VARCHAR(40) NOT NULL CHECK (operation_type IN "
        "('nginx_install', 'nginx_uninstall', 'nginx_service_control')), "
        "task_id INTEGER NOT NULL REFERENCES ngxops_tasks(id) ON DELETE CASCADE, "
        "node_id INTEGER REFERENCES ngxops_nodes(id) ON DELETE SET NULL, "
        "level VARCHAR(20) NOT NULL CHECK (level IN ('debug', 'info', 'warning', 'error')), "
        "message TEXT NOT NULL, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_nginx_operation_logs_type_task_id "
        "ON ngxops_nginx_operation_logs (operation_type, task_id, id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_nginx_operation_logs_task_node_id "
        "ON ngxops_nginx_operation_logs (task_id, node_id, id)"
    )


MIGRATION = Migration(version=19, name="nginx_operation_logs", upgrade=upgrade)
