"""Nginx 升级模块 - 视图"""
from django.views.generic import TemplateView


class UpgradeListView(TemplateView):
    """Nginx 升级列表页面（占位视图）"""
    template_name = "upgrade/index.html"
