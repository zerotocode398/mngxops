"""为通用任务日志增加节点归属。"""

from ngxops.database.migrations.definition import Migration


def upgrade(connection) -> None:
    """添加可空节点外键和日志筛选索引。"""
    connection.exec_driver_sql(
        "ALTER TABLE ngxops_task_logs "
        "ADD COLUMN node_id INTEGER REFERENCES ngxops_nodes(id) ON DELETE SET NULL"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_task_logs_task_node_id_id "
        "ON ngxops_task_logs (task_id, node_id, id)"
    )


MIGRATION = Migration(version=18, name="task_log_node_scope", upgrade=upgrade)
