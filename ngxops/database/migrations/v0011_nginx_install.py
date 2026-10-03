"""创建 Nginx 全新安装运行快照并登记独立权限。"""

from ngxops.database.migrations.definition import Migration


def upgrade(connection) -> None:
    """建立全新安装任务配置快照表和安装权限项。"""
    connection.exec_driver_sql(
        "CREATE TABLE ngxops_nginx_install_runs ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "task_id INTEGER NOT NULL UNIQUE REFERENCES ngxops_tasks(id) ON DELETE CASCADE, "
        "node_id INTEGER REFERENCES ngxops_nodes(id) ON DELETE SET NULL, "
        "source_package_id INTEGER REFERENCES ngxops_nginx_source_packages(id) ON DELETE SET NULL, "
        "batch_number VARCHAR(32) NOT NULL, "
        "node_hostname VARCHAR(100) NOT NULL, "
        "node_ip VARCHAR(45) NOT NULL, "
        "source_package_name VARCHAR(100) NOT NULL DEFAULT '', "
        "target_version VARCHAR(50) NOT NULL, "
        "remote_work_dir VARCHAR(500) NOT NULL, "
        "target_prefix VARCHAR(500) NOT NULL, "
        "nginx_user VARCHAR(100) NOT NULL DEFAULT 'root', "
        "nginx_group VARCHAR(100) NOT NULL DEFAULT 'root', "
        "target_configure_opts TEXT NOT NULL DEFAULT '', "
        "added_modules_json TEXT NOT NULL DEFAULT '[]', "
        "third_party_json TEXT NOT NULL DEFAULT '[]', "
        "make_jobs INTEGER NOT NULL CHECK (make_jobs BETWEEN 1 AND 32), "
        "listen_port INTEGER NOT NULL CHECK (listen_port BETWEEN 1 AND 65535), "
        "phase VARCHAR(40) NOT NULL DEFAULT 'pending', "
        "sync_ok BOOLEAN, "
        "sync_detail VARCHAR(500) NOT NULL DEFAULT '', "
        "nginx_path VARCHAR(500) NOT NULL DEFAULT '', "
        "main_conf_path VARCHAR(500) NOT NULL DEFAULT '', "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_install_runs_batch_created "
        "ON ngxops_nginx_install_runs (batch_number, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_ngxops_install_runs_node_created "
        "ON ngxops_nginx_install_runs (node_id, created_at)"
    )
    connection.exec_driver_sql(
        "INSERT OR IGNORE INTO ngxops_permission_items (code, name, resource, action) "
        "VALUES ('nginx_install.read', '安装历史查看', 'nginx_install', 'read')"
    )
    connection.exec_driver_sql(
        "INSERT OR IGNORE INTO ngxops_permission_items (code, name, resource, action) "
        "VALUES ('nginx_install.create', '创建安装任务', 'nginx_install', 'create')"
    )


MIGRATION = Migration(version=11, name="nginx_install", upgrade=upgrade)
