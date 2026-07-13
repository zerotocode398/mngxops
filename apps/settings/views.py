"""系统设置模块 - 视图"""
from django.views.generic import TemplateView


class SettingsIndexView(TemplateView):
    """系统设置页面（占位视图）"""
    template_name = "settings/index.html"
