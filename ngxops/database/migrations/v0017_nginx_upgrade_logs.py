"""为 Nginx 升级和回滚保存独立的完整执行日志。"""

from ngxops.database.migrations.definition import Migration


def upgrade(connection) -> None:
    """创建逐行升级日志表及其任务游标索引。"""
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_nginx_upgrade_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "task_id INTEGER NOT NULL REFERENCES ngxops_tasks(id) ON DELETE CASCADE, "
        "level VARCHAR(20) NOT NULL CHECK (level IN ('debug', 'info', 'warning', 'error')), "
        "message TEXT NOT NULL, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_nginx_upgrade_logs_task_id_id "
        "ON ngxops_nginx_upgrade_logs (task_id, id)"
    )


MIGRATION = Migration(version=17, name="nginx_upgrade_logs", upgrade=upgrade)
