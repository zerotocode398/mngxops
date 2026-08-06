"""Nginx 安装模块 - 应用配置"""
from django.apps import AppConfig


class NginxInstallConfig(AppConfig):
    """Nginx 安装应用配置类"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.nginx_install"
    verbose_name = "Nginx 安装"
