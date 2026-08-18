"""OpenSSH 升级路由"""
from django.urls import path

from . import views

app_name = "openssh_upgrade"

urlpatterns = [
    path("", views.OpenSSHUpgradeIndexView.as_view(), name="index"),
    path("center/", views.OpenSSHUpgradeCenterView.as_view(), name="center"),
    path("history/", views.OpenSSHUpgradeHistoryView.as_view(), name="history"),
    path("task/<int:pk>/log/", views.OpenSSHUpgradeTaskLogView.as_view(), name="task_log"),
    path(
        "api/task/<int:pk>/log/",
        views.OpenSSHUpgradeTaskLogAPIView.as_view(),
        name="api_task_log",
    ),
    path(
        "api/preview/",
        views.OpenSSHUpgradePreviewAPIView.as_view(),
        name="api_preview",
    ),
    path(
        "api/create/",
        views.OpenSSHUpgradeCreateAPIView.as_view(),
        name="api_create",
    ),
    path(
        "api/rollback/",
        views.OpenSSHUpgradeRollbackAPIView.as_view(),
        name="api_rollback",
    ),
    path(
        "api/batch-progress/",
        views.OpenSSHUpgradeBatchProgressAPIView.as_view(),
        name="api_batch_progress",
    ),
    path("packages/", views.OpenSSHPackageListView.as_view(), name="packages"),
    path("packages/upload/", views.OpenSSHPackageUploadView.as_view(), name="package_upload"),
    path(
        "api/packages/upload/",
        views.OpenSSHPackageUploadAPIView.as_view(),
        name="api_package_upload",
    ),
    path(
        "api/packages/<int:pk>/delete/",
        views.OpenSSHPackageDeleteAPIView.as_view(),
        name="api_package_delete",
    ),
    path(
        "packages/<int:pk>/delete/",
        views.OpenSSHPackageDeleteView.as_view(),
        name="package_delete",
    ),
]