"""Nginx 启停模块 - 应用配置"""
from django.apps import AppConfig


class NginxServiceConfig(AppConfig):
    """Nginx 启停应用配置类"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.nginx_service"
    verbose_name = "Nginx 启停"
