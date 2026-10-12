"""将 Nginx 源码包唯一身份调整为上传人、名称和版本。"""

from ngxops.database.migrations.definition import Migration


def upgrade(connection) -> None:
    """重建源码包表以保留数据并更换上传人级唯一约束。"""
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_nginx_source_packages_v16 ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "name VARCHAR(100) NOT NULL, "
        "version VARCHAR(50) NOT NULL, "
        "file_name VARCHAR(255) NOT NULL UNIQUE, "
        "file_size INTEGER NOT NULL CONSTRAINT ck_ngxops_source_package_size CHECK (file_size >= 0), "
        "file_md5 VARCHAR(32) NOT NULL, "
        "description TEXT NOT NULL DEFAULT '', "
        "is_official BOOLEAN NOT NULL DEFAULT 0, "
        "created_by INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT uq_ngxops_source_package_owner_name_version "
        "UNIQUE (name, version, created_by)"
        ")"
    )
    connection.exec_driver_sql(
        "INSERT INTO ngxops_nginx_source_packages_v16 "
        "(id, name, version, file_name, file_size, file_md5, description, "
        "is_official, created_by, created_at) "
        "SELECT id, name, version, file_name, file_size, file_md5, description, "
        "is_official, created_by, created_at FROM ngxops_nginx_source_packages"
    )
    connection.exec_driver_sql("DROP TABLE ngxops_nginx_source_packages")
    connection.exec_driver_sql(
        "ALTER TABLE ngxops_nginx_source_packages_v16 "
        "RENAME TO ngxops_nginx_source_packages"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_source_packages_created_at "
        "ON ngxops_nginx_source_packages (created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_source_packages_md5 "
        "ON ngxops_nginx_source_packages (file_md5)"
    )


MIGRATION = Migration(
    version=16,
    name="source_package_identity",
    upgrade=upgrade,
    requires_foreign_keys_off=True,
)
