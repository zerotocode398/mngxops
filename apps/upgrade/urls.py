"""Nginx 升级模块 - URL 配置"""
from django.urls import path
from . import views

app_name = "upgrade"

urlpatterns = [
    # 源码包管理
    path("packages/", views.PackageListView.as_view(), name="package_list"),
    path("packages/upload/", views.PackageUploadView.as_view(), name="package_upload"),
    path("packages/<int:pk>/delete/", views.PackageDeleteView.as_view(), name="package_delete"),
    path("packages/<int:pk>/download/", views.PackageDownloadView.as_view(), name="package_download"),

    # 升级中心
    path("center/", views.UpgradeCenterView.as_view(), name="center"),

    # API 接口
    path("api/nginx-v/<int:node_id>/", views.NginxVApiView.as_view(), name="api_nginx_v"),
    path("api/parse-config/", views.ParseConfigApiView.as_view(), name="api_parse_config"),
    path("api/compute-config/", views.ComputeConfigApiView.as_view(), name="api_compute_config"),

    # 升级任务
    path("task/create/", views.UpgradeTaskCreateView.as_view(), name="task_create"),
    path("task/<int:pk>/progress/", views.UpgradeTaskProgressView.as_view(), name="task_progress"),
    path("task/<int:pk>/log/", views.UpgradeTaskLogView.as_view(), name="task_log"),
    path("task/<int:pk>/cancel/", views.UpgradeTaskCancelView.as_view(), name="task_cancel"),
    path("task/<int:pk>/rollback/", views.UpgradeTaskRollbackView.as_view(), name="task_rollback"),

    # 升级历史
    path("history/", views.UpgradeHistoryView.as_view(), name="history"),

    # 主页（兼容旧路由）
    path("", views.UpgradeTaskListView.as_view(), name="list"),
]