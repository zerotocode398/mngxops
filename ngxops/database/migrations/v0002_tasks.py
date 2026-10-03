"""建立统一异步任务与持久化日志表。"""

from sqlalchemy.engine import Connection

from ngxops.database.migrations.definition import Migration


def upgrade(connection: Connection) -> None:
    """创建任务中心表、状态约束及轮询索引。"""
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_tasks ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "operation_type VARCHAR(40) NOT NULL DEFAULT 'other', "
        "status VARCHAR(20) NOT NULL DEFAULT 'pending' "
        "CHECK (status IN ('pending', 'running', 'success', 'failed', 'cancelled')), "
        "detail TEXT NOT NULL DEFAULT '', "
        "result_tree_json TEXT, "
        "progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100), "
        "source_batch VARCHAR(64) NOT NULL DEFAULT '', "
        "target_hostnames TEXT NOT NULL DEFAULT '', "
        "target_ips TEXT NOT NULL DEFAULT '', "
        "target_configs TEXT NOT NULL DEFAULT '', "
        "trigger_user_id INTEGER REFERENCES auth_user(id) ON DELETE SET NULL, "
        "started_at DATETIME, "
        "finished_at DATETIME, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_tasks_created_at ON ngxops_tasks (created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_tasks_status_created_at "
        "ON ngxops_tasks (status, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_tasks_operation_created_at "
        "ON ngxops_tasks (operation_type, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_tasks_source_batch ON ngxops_tasks (source_batch)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_tasks_trigger_user_created_at "
        "ON ngxops_tasks (trigger_user_id, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_task_logs ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "task_id INTEGER NOT NULL REFERENCES ngxops_tasks(id) ON DELETE CASCADE, "
        "level VARCHAR(10) NOT NULL DEFAULT 'info', "
        "message TEXT NOT NULL, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_task_logs_task_id_id "
        "ON ngxops_task_logs (task_id, id)"
    )


MIGRATION = Migration(
    version=2,
    name="async_tasks",
    upgrade=upgrade,
)
