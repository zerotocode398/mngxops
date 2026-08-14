"""Nginx 启停模块 - URL 配置"""
from django.urls import path

from . import views

app_name = "nginx_service"

urlpatterns = [
    path("", views.NginxServiceIndexView.as_view(), name="index"),
    path("history/", views.NginxServiceHistoryView.as_view(), name="history"),
    path("task/<int:pk>/log/", views.NginxServiceTaskLogView.as_view(), name="task_log"),
    path("api/execute/", views.NginxServiceExecuteAPIView.as_view(), name="api_execute"),
    path(
        "api/batch-progress/",
        views.NginxServiceBatchProgressAPIView.as_view(),
        name="api_batch_progress",
    ),
    path(
        "api/task/<int:pk>/log/",
        views.NginxServiceTaskLogAPIView.as_view(),
        name="api_task_log",
    ),
]
