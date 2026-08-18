"""OpenSSH 升级模块 - 应用配置"""
from django.apps import AppConfig


class OpensshUpgradeConfig(AppConfig):
    """OpenSSH 升级应用配置类"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.openssh_upgrade"
    verbose_name = "OpenSSH 升级"