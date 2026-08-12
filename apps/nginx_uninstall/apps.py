"""Nginx 卸载模块 - 应用配置"""
from django.apps import AppConfig


class NginxUninstallConfig(AppConfig):
    """Nginx 卸载应用配置类"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.nginx_uninstall"
    verbose_name = "Nginx 卸载"
