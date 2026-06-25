from django.urls import path
from . import views

app_name = "configs"

urlpatterns = [
    path("", views.ConfigListView.as_view(), name="list"),
    path("api/by-nodes/", views.ConfigByNodesAPIView.as_view(), name="api_by_nodes"),
    path("create/", views.ConfigCreateView.as_view(), name="create"),
    path("sync/", views.ConfigSyncWizardView.as_view(), name="sync_wizard"),
    path("sync/batch/", views.ConfigBatchSyncView.as_view(), name="batch_sync"),
    path(
        "sync/<int:node_id>/partial/",
        views.ConfigSyncPartialView.as_view(),
        name="sync_partial",
    ),
    path(
        "sync/<int:node_id>/", views.ConfigSyncRemoteView.as_view(), name="sync_remote"
    ),
    path("<int:pk>/", views.ConfigDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.ConfigEditView.as_view(), name="edit"),
    path("<int:pk>/update/", views.ConfigUpdateView.as_view(), name="update"),
    path("<int:pk>/versions/", views.ConfigVersionListView.as_view(), name="versions"),
    path(
        "<int:pk>/versions/compare/",
        views.ConfigVersionCompareView.as_view(),
        name="version_compare",
    ),
    path(
        "<int:pk>/versions/compare/apply/",
        views.ConfigVersionCompareApplyView.as_view(),
        name="version_compare_apply",
    ),
    path(
        "<int:pk>/versions/<int:version_id>/",
        views.ConfigVersionDetailView.as_view(),
        name="version_detail",
    ),
    path(
        "<int:pk>/versions/<int:version_id>/restore/",
        views.ConfigVersionRestoreView.as_view(),
        name="restore",
    ),
    path(
        "api/version-content/<int:version_id>/",
        views.ConfigVersionContentAPIView.as_view(),
        name="api_version_content",
    ),
]
