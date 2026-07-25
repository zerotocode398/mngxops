"""Nginx 升级模块 - 应用配置"""
from django.apps import AppConfig


class UpgradeConfig(AppConfig):
    """Nginx 升级应用配置类"""
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.upgrade"
    verbose_name = "Nginx 升级"

    def ready(self):
        """应用就绪时显式加载模板过滤器，避免进程未发现标签库"""
        import apps.upgrade.templatetags.upgrade_filters  # noqa: F401
