"""创建 Nginx 升级包与任务配置记录。"""

from ngxops.database.migrations.definition import Migration


def upgrade(connection) -> None:
    """建立升级源码包、离线模块包和任务配置快照。"""
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_nginx_source_packages ("
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
        "CONSTRAINT uq_ngxops_source_package_owner_version "
        "UNIQUE (version, created_by)"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_source_packages_created_at "
        "ON ngxops_nginx_source_packages (created_at)"
    )
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_nginx_module_packages ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "name VARCHAR(100) NOT NULL, "
        "version VARCHAR(50) NOT NULL DEFAULT '', "
        "file_name VARCHAR(255) NOT NULL UNIQUE, "
        "file_size INTEGER NOT NULL CONSTRAINT ck_ngxops_module_package_size CHECK (file_size >= 0), "
        "file_md5 VARCHAR(32) NOT NULL, "
        "description TEXT NOT NULL DEFAULT '', "
        "created_by INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT uq_ngxops_module_package_owner_name_version "
        "UNIQUE (name, version, created_by)"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_module_packages_created_at "
        "ON ngxops_nginx_module_packages (created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_module_packages_md5 "
        "ON ngxops_nginx_module_packages (file_md5)"
    )
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_nginx_upgrade_runs ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "task_id INTEGER NOT NULL UNIQUE REFERENCES ngxops_tasks(id) ON DELETE CASCADE, "
        "node_id INTEGER REFERENCES ngxops_nodes(id) ON DELETE SET NULL, "
        "source_package_id INTEGER REFERENCES ngxops_nginx_source_packages(id) ON DELETE SET NULL, "
        "rollback_of_id INTEGER REFERENCES ngxops_nginx_upgrade_runs(id) ON DELETE SET NULL, "
        "batch_number VARCHAR(32) NOT NULL, "
        "node_hostname VARCHAR(100) NOT NULL, "
        "node_ip VARCHAR(45) NOT NULL, "
        "source_package_name VARCHAR(100) NOT NULL DEFAULT '', "
        "target_version VARCHAR(50) NOT NULL, "
        "upgrade_mode VARCHAR(20) NOT NULL CHECK (upgrade_mode IN ('upgrade', 'switch_path', 'rollback')), "
        "remote_work_dir VARCHAR(500) NOT NULL, "
        "make_jobs INTEGER NOT NULL CHECK (make_jobs BETWEEN 1 AND 32), "
        "current_version VARCHAR(50) NOT NULL DEFAULT '', "
        "current_configure_opts TEXT NOT NULL DEFAULT '', "
        "current_prefix VARCHAR(500) NOT NULL DEFAULT '', "
        "current_binary_path VARCHAR(500) NOT NULL DEFAULT '', "
        "target_configure_opts TEXT NOT NULL DEFAULT '', "
        "target_prefix VARCHAR(500) NOT NULL DEFAULT '', "
        "added_modules_json TEXT NOT NULL DEFAULT '[]', "
        "removed_modules_json TEXT NOT NULL DEFAULT '[]', "
        "third_party_json TEXT NOT NULL DEFAULT '[]', "
        "backup_binary_path VARCHAR(500) NOT NULL DEFAULT '', "
        "phase VARCHAR(40) NOT NULL DEFAULT 'pending', "
        "rolled_back_at DATETIME, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_upgrade_runs_batch_created "
        "ON ngxops_nginx_upgrade_runs (batch_number, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_upgrade_runs_node_created "
        "ON ngxops_nginx_upgrade_runs (node_id, created_at)"
    )


MIGRATION = Migration(version=10, name="nginx_upgrade", upgrade=upgrade)
