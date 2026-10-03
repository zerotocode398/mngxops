"""为统一任务增加稳定的关联资源索引。"""

from ngxops.database.migrations.definition import Migration


def upgrade(connection) -> None:
    """添加任务关联资源类型、主键及索引。"""
    connection.exec_driver_sql(
        "ALTER TABLE ngxops_tasks ADD COLUMN subject_type VARCHAR(40) NOT NULL DEFAULT ''"
    )
    connection.exec_driver_sql(
        "ALTER TABLE ngxops_tasks ADD COLUMN subject_id INTEGER"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_tasks_subject "
        "ON ngxops_tasks (subject_type, subject_id, created_at)"
    )


MIGRATION = Migration(version=7, name="task_subject", upgrade=upgrade)
