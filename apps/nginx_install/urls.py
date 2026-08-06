"""Nginx 安装路由"""
from django.urls import path

from . import views

app_name = "nginx_install"

urlpatterns = [
    path("", views.NginxInstallIndexView.as_view(), name="index"),
    path("center/", views.NginxInstallCenterView.as_view(), name="center"),
    path("history/", views.NginxInstallHistoryView.as_view(), name="history"),
    path("task/<int:pk>/log/", views.NginxInstallTaskLogView.as_view(), name="task_log"),
    path("api/create/", views.NginxInstallTaskCreateAPIView.as_view(), name="api_create"),
    path(
        "api/batch-progress/",
        views.NginxInstallBatchProgressAPIView.as_view(),
        name="api_batch_progress",
    ),
    path("api/task/<int:pk>/log/", views.NginxInstallTaskLogAPIView.as_view(), name="api_task_log"),
]
