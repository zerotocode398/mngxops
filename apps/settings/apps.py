"""系统设置模块 - 应用配置"""
from django.apps import AppConfig


class SettingsConfig(AppConfig):
    """系统设置应用配置类"""
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.settings"
    verbose_name = "系统设置"
