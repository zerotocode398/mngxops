"""系统设置模块 - 应用配置"""
from django.apps import AppConfig


class SettingsConfig(AppConfig):
    """系统设置应用配置类"""
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.settings"
    verbose_name = "系统设置"

    def ready(self):
        """启动时自动种子化预置配置（迁移未就绪时静默跳过）"""
        try:
            from utils.setting_service import seed_default_settings
            seed_default_settings()
        except Exception:
            pass
