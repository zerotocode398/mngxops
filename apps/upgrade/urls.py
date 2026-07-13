"""Nginx 升级模块 - URL 配置"""
from django.urls import path
from . import views

app_name = "upgrade"

urlpatterns = [
    path("", views.UpgradeListView.as_view(), name="list"),
]
