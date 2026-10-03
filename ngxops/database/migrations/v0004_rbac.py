"""创建 RBAC 授权关系并初始化稳定权限项。"""

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ngxops.database.migrations.definition import Migration
from ngxops.rbac.permission_defs import all_permission_items


def upgrade(connection: Connection) -> None:
    """建立角色、用户组、个人授权表并写入权限项种子。"""
    statements = (
        "CREATE TABLE ngxops_permission_items ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "code VARCHAR(100) NOT NULL UNIQUE, name VARCHAR(100) NOT NULL, "
        "resource VARCHAR(50) NOT NULL, action VARCHAR(20) NOT NULL)",
        "CREATE TABLE ngxops_roles ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "name VARCHAR(100) NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '', "
        "created_by INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE ngxops_role_permissions ("
        "role_id INTEGER NOT NULL REFERENCES ngxops_roles(id) ON DELETE CASCADE, "
        "permission_id INTEGER NOT NULL REFERENCES ngxops_permission_items(id) "
        "ON DELETE CASCADE, "
        "PRIMARY KEY (role_id, permission_id))",
        "CREATE TABLE ngxops_user_teams ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "name VARCHAR(100) NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '', "
        "created_by INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE ngxops_team_members ("
        "team_id INTEGER NOT NULL REFERENCES ngxops_user_teams(id) ON DELETE CASCADE, "
        "user_id INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE, "
        "PRIMARY KEY (team_id, user_id))",
        "CREATE TABLE ngxops_team_roles ("
        "team_id INTEGER NOT NULL REFERENCES ngxops_user_teams(id) ON DELETE CASCADE, "
        "role_id INTEGER NOT NULL REFERENCES ngxops_roles(id) ON DELETE CASCADE, "
        "PRIMARY KEY (team_id, role_id))",
        "CREATE TABLE ngxops_user_profiles ("
        "user_id INTEGER NOT NULL PRIMARY KEY REFERENCES auth_user(id) "
        "ON DELETE CASCADE, "
        "remark TEXT NOT NULL DEFAULT '')",
        "CREATE TABLE ngxops_profile_roles ("
        "user_id INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE, "
        "role_id INTEGER NOT NULL REFERENCES ngxops_roles(id) ON DELETE CASCADE, "
        "PRIMARY KEY (user_id, role_id))",
        "CREATE TABLE ngxops_profile_permissions ("
        "user_id INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE, "
        "permission_id INTEGER NOT NULL REFERENCES ngxops_permission_items(id) "
        "ON DELETE CASCADE, "
        "PRIMARY KEY (user_id, permission_id))",
        "CREATE INDEX ix_ngxops_team_members_user_id ON ngxops_team_members (user_id)",
        "CREATE INDEX ix_ngxops_team_roles_role_id ON ngxops_team_roles (role_id)",
        "CREATE INDEX ix_ngxops_profile_roles_role_id "
        "ON ngxops_profile_roles (role_id)",
        "CREATE INDEX ix_ngxops_role_permissions_permission_id "
        "ON ngxops_role_permissions (permission_id)",
        "CREATE INDEX ix_ngxops_profile_permissions_permission_id "
        "ON ngxops_profile_permissions (permission_id)",
    )
    for statement in statements:
        connection.exec_driver_sql(statement)
    connection.execute(
        text(
            "INSERT INTO ngxops_permission_items "
            "(code, name, resource, action) "
            "VALUES (:code, :name, :resource, :action)"
        ),
        all_permission_items(),
    )


MIGRATION = Migration(
    version=4,
    name="rbac_users_roles_teams",
    upgrade=upgrade,
)
