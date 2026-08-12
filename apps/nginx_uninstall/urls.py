"""Nginx 卸载路由"""
from django.urls import path

from . import views

app_name = "nginx_uninstall"

urlpatterns = [
    path("", views.NginxUninstallIndexView.as_view(), name="index"),
    path("center/", views.NginxUninstallCenterView.as_view(), name="center"),
    path("history/", views.NginxUninstallHistoryView.as_view(), name="history"),
    path("task/<int:pk>/log/", views.NginxUninstallTaskLogView.as_view(), name="task_log"),
    path(
        "api/task/<int:pk>/log/",
        views.NginxUninstallTaskLogAPIView.as_view(),
        name="api_task_log",
    ),
    path("api/preview/", views.NginxUninstallPreviewAPIView.as_view(), name="api_preview"),
    path("api/create/", views.NginxUninstallCreateAPIView.as_view(), name="api_create"),
    path(
        "api/batch-progress/",
        views.NginxUninstallBatchProgressAPIView.as_view(),
        name="api_batch_progress",
    ),
]
