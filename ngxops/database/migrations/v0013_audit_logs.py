"""创建可保留操作人快照和任务关联的审计日志表。"""

from ngxops.database.migrations.definition import Migration


def upgrade(connection) -> None:
    """建立操作日志表并添加筛选和跳转索引。"""
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_audit_logs ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER REFERENCES auth_user(id) ON DELETE SET NULL, "
        "username VARCHAR(150) NOT NULL DEFAULT '', "
        "module VARCHAR(100) NOT NULL, "
        "action VARCHAR(255) NOT NULL, "
        "ip VARCHAR(50) NOT NULL DEFAULT '', "
        "result VARCHAR(20) NOT NULL DEFAULT 'success' "
        "CHECK (result IN ('success', 'failed')), "
        "detail TEXT NOT NULL DEFAULT '', "
        "task_id INTEGER, "
        "source_batch VARCHAR(64) NOT NULL DEFAULT '', "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_audit_logs_created_at "
        "ON ngxops_audit_logs (created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_audit_logs_module_created_at "
        "ON ngxops_audit_logs (module, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_audit_logs_result_created_at "
        "ON ngxops_audit_logs (result, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_audit_logs_task_id "
        "ON ngxops_audit_logs (task_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_audit_logs_source_batch "
        "ON ngxops_audit_logs (source_batch)"
    )


MIGRATION = Migration(version=13, name="audit_logs", upgrade=upgrade)
