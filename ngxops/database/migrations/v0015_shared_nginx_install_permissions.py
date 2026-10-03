"""将全新安装权限迁移到统一的 Nginx 安装/升级资源。"""

from ngxops.database.migrations.definition import Migration


def _merge_permission_grants(connection, source_code: str, target_code: str) -> None:
    """将旧权限关联复制到共用权限项。"""
    source_id = connection.exec_driver_sql(
        "SELECT id FROM ngxops_permission_items WHERE code = ?",
        (source_code,),
    ).scalar()
    target_id = connection.exec_driver_sql(
        "SELECT id FROM ngxops_permission_items WHERE code = ?",
        (target_code,),
    ).scalar()
    if source_id is None or target_id is None:
        return

    for table, owner_column in (
        ("ngxops_role_permissions", "role_id"),
        ("ngxops_profile_permissions", "user_id"),
    ):
        connection.exec_driver_sql(
            "INSERT OR IGNORE INTO {} ({}, permission_id) "
            "SELECT {}, ? FROM {} WHERE permission_id = ?".format(
                table, owner_column, owner_column, table
            ),
            (target_id, source_id),
        )


def upgrade(connection) -> None:
    """合并全新安装授权并移除独立权限项。"""
    _merge_permission_grants(
        connection, "nginx_install.read", "upgrade.read"
    )
    _merge_permission_grants(
        connection, "nginx_install.create", "upgrade.execute"
    )
    connection.exec_driver_sql(
        "DELETE FROM ngxops_permission_items "
        "WHERE code IN ('nginx_install.read', 'nginx_install.create')"
    )


MIGRATION = Migration(
    version=15,
    name="shared_nginx_install_permissions",
    upgrade=upgrade,
)
