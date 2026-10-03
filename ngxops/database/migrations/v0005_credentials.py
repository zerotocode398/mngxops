"""建立 SSH 凭证表并保留用户删除时的级联规则。"""

from sqlalchemy.engine import Connection

from ngxops.database.migrations.definition import Migration


def upgrade(connection: Connection) -> None:
    """创建加密凭证字段、认证状态约束和查询索引。"""
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_credentials ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "name VARCHAR(100) NOT NULL, "
        "username VARCHAR(100) NOT NULL, "
        "auth_type VARCHAR(20) NOT NULL DEFAULT 'password' "
        "CHECK (auth_type IN ('password', 'key')), "
        "password TEXT NOT NULL DEFAULT '', "
        "private_key TEXT NOT NULL DEFAULT '', "
        "is_enabled BOOLEAN NOT NULL DEFAULT 1, "
        "description TEXT NOT NULL DEFAULT '', "
        "last_test_time DATETIME, "
        "last_test_result VARCHAR(20) NOT NULL DEFAULT 'unknown' "
        "CHECK (last_test_result IN ('success', 'partial', 'failed', 'unknown')), "
        "created_by INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT uq_ngxops_credentials_owner_name UNIQUE (name, created_by)"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_credentials_created_at "
        "ON ngxops_credentials (created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_credentials_enabled_name "
        "ON ngxops_credentials (is_enabled, name)"
    )


MIGRATION = Migration(
    version=5,
    name="ssh_credentials",
    upgrade=upgrade,
)
