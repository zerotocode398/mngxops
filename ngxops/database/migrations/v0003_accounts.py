"""创建登录失败状态与登录日志表。"""

from sqlalchemy.engine import Connection

from ngxops.database.migrations.definition import Migration


def upgrade(connection: Connection) -> None:
    """创建 NX-010 使用的登录锁定和登录日志表。"""
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_login_failure_states ("
        "user_id INTEGER NOT NULL PRIMARY KEY "
        "REFERENCES auth_user(id) ON DELETE CASCADE, "
        "failed_login_count INTEGER NOT NULL DEFAULT 0 "
        "CHECK (failed_login_count >= 0), "
        "login_locked_until DATETIME"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_login_logs ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "username VARCHAR(150) NOT NULL, "
        "ip VARCHAR(50) NOT NULL DEFAULT '', "
        "user_agent TEXT NOT NULL DEFAULT '', "
        "status VARCHAR(20) NOT NULL "
        "CHECK (status IN ('success', 'failed')), "
        "fail_reason VARCHAR(50) NOT NULL DEFAULT '' "
        "CHECK (fail_reason IN ('', 'user_not_found', 'wrong_password', "
        "'user_locked', 'user_inactive')), "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_login_logs_created_at "
        "ON ngxops_login_logs (created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_login_logs_username "
        "ON ngxops_login_logs (username)"
    )


MIGRATION = Migration(
    version=3,
    name="accounts_login_lock_and_logs",
    upgrade=upgrade,
)
