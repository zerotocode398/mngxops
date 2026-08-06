"""Nginx 启停模块 - URL 配置"""
from django.urls import path

from . import views

app_name = "nginx_service"

urlpatterns = [
    path("", views.NginxServiceIndexView.as_view(), name="index"),
    path("api/execute/", views.NginxServiceExecuteAPIView.as_view(), name="api_execute"),
]
