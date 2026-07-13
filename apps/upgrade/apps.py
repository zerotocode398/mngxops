"""Nginx 升级模块 - 应用配置"""
from django.apps import AppConfig


class UpgradeConfig(AppConfig):
    """Nginx 升级应用配置类"""
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.upgrade"
    verbose_name = "Nginx 升级"