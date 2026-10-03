"""建立账户身份与可撤销服务端会话表。"""

from sqlalchemy.engine import Connection

from ngxops.database.migrations.definition import Migration


def upgrade(connection: Connection) -> None:
    """创建认证依赖的用户身份表和服务端会话表。"""
    connection.exec_driver_sql(
        "CREATE TABLE auth_user ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "password VARCHAR(128) NOT NULL, "
        "last_login DATETIME, "
        "is_superuser BOOLEAN NOT NULL DEFAULT 0, "
        "username VARCHAR(150) NOT NULL UNIQUE, "
        "first_name VARCHAR(150) NOT NULL DEFAULT '', "
        "last_name VARCHAR(150) NOT NULL DEFAULT '', "
        "email VARCHAR(254) NOT NULL DEFAULT '', "
        "is_staff BOOLEAN NOT NULL DEFAULT 0, "
        "is_active BOOLEAN NOT NULL DEFAULT 1, "
        "date_joined DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_sessions ("
        "session_key VARCHAR(64) NOT NULL PRIMARY KEY, "
        "user_id INTEGER REFERENCES auth_user(id) ON DELETE CASCADE, "
        "session_data TEXT NOT NULL, "
        "expires_at DATETIME NOT NULL, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_sessions_user_id ON ngxops_sessions (user_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_sessions_expires_at ON ngxops_sessions (expires_at)"
    )


MIGRATION = Migration(
    version=1,
    name="auth_and_sessions",
    upgrade=upgrade,
)
