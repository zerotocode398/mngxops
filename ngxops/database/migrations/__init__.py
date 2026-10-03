"""登记按版本顺序执行的数据库结构迁移。"""

from typing import Tuple

from ngxops.database.migrations.definition import Migration
from ngxops.database.migrations.v0001_auth_and_sessions import MIGRATION as AUTH_AND_SESSIONS
from ngxops.database.migrations.v0002_tasks import MIGRATION as TASKS
from ngxops.database.migrations.v0003_accounts import MIGRATION as ACCOUNTS
from ngxops.database.migrations.v0004_rbac import MIGRATION as RBAC
from ngxops.database.migrations.v0005_credentials import MIGRATION as CREDENTIALS
from ngxops.database.migrations.v0006_nodes import MIGRATION as NODES
from ngxops.database.migrations.v0007_task_subject import MIGRATION as TASK_SUBJECT
from ngxops.database.migrations.v0008_configs import MIGRATION as CONFIGS
from ngxops.database.migrations.v0009_config_sync_settings import (
    MIGRATION as CONFIG_SYNC_SETTINGS,
)
from ngxops.database.migrations.v0010_upgrade import MIGRATION as NGINX_UPGRADE
from ngxops.database.migrations.v0011_nginx_install import MIGRATION as NGINX_INSTALL
from ngxops.database.migrations.v0012_nginx_uninstall import (
    MIGRATION as NGINX_UNINSTALL,
)
from ngxops.database.migrations.v0013_audit_logs import MIGRATION as AUDIT_LOGS
from ngxops.database.migrations.v0014_system_settings import MIGRATION as SYSTEM_SETTINGS


MIGRATIONS: Tuple[Migration, ...] = (
    AUTH_AND_SESSIONS,
    TASKS,
    ACCOUNTS,
    RBAC,
    CREDENTIALS,
    NODES,
    TASK_SUBJECT,
    CONFIGS,
    CONFIG_SYNC_SETTINGS,
    NGINX_UPGRADE,
    NGINX_INSTALL,
    NGINX_UNINSTALL,
    AUDIT_LOGS,
    SYSTEM_SETTINGS,
)
