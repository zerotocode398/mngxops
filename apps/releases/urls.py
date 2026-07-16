from django.urls import path
from . import views

app_name = "releases"

urlpatterns = [
    # API
    path("api/nodes/", views.ReleaseNodeListAPIView.as_view(), name="api_nodes"),
    path("api/node-bindings/<int:node_id>/", views.ReleaseNodeBindingsAPIView.as_view(), name="api_node_bindings"),
    # 创建
    path("api/create/", views.ReleaseCreateAPIView.as_view(), name="api_create"),
    # 任务中心
    path("history/", views.TaskCenterListView.as_view(), name="history"),
    path("tasks/<int:pk>/", views.TaskCenterDetailView.as_view(), name="task_center_detail"),
    path("tasks/progress/", views.TaskCenterProgressAPIView.as_view(), name="task_center_progress"),
    # 发布历史
    path("list/", views.ReleaseListView.as_view(), name="list"),
    # 发布中心
    path("center/", views.ReleaseCenterView.as_view(), name="center"),
    path(
        "center/<str:batch_number>/execute/",
        views.ReleaseCenterExecuteView.as_view(),
        name="center_execute",
    ),
    path(
        "center/<str:batch_number>/cancel/",
        views.ReleaseCenterCancelView.as_view(),
        name="center_cancel",
    ),
    path(
        "center/task/<int:task_id>/execute/",
        views.ReleaseCenterSingleExecuteView.as_view(),
        name="center_execute_single",
    ),
    path(
        "center/task/<int:task_id>/status/",
        views.ReleaseTaskStatusView.as_view(),
        name="task_status",
    ),
    # 详情/回滚/重试
    path("<int:pk>/", views.ReleaseDetailView.as_view(), name="detail"),
    path("<int:pk>/rollback/", views.ReleaseRollbackView.as_view(), name="rollback"),
    path("<int:pk>/retry/", views.ReleaseRetryView.as_view(), name="retry"),
    # 批量回滚
    path(
        "batch-rollback/<str:batch_number>/",
        views.ReleaseBatchRollbackView.as_view(),
        name="batch_rollback",
    ),
    # 版本内容
    path(
        "version/<int:version_id>/content/",
        views.VersionContentAPIView.as_view(),
        name="version_content",
    ),
]
