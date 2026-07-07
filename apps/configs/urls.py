from django.urls import path
from . import views

app_name = "configs"

urlpatterns = [
    path("", views.ConfigListView.as_view(), name="list"),
    path("create/", views.ConfigCreateView.as_view(), name="create"),
    path("api/by-nodes/", views.ConfigByNodesAPIView.as_view(), name="api_by_nodes"),
    path(
        "api/preview-glob/",
        views.ConfigGlobPreviewView.as_view(),
        name="api_preview_glob",
    ),
    path(
        "api/update-preview/",
        views.ConfigUpdatePreviewView.as_view(),
        name="api_update_preview",
    ),
    path("sync/", views.ConfigSyncWizardView.as_view(), name="sync_wizard"),
    path(
        "sync/api/batch/",
        views.ConfigSyncBatchAPIView.as_view(),
        name="sync_batch_api",
    ),
    path(
        "sync/api/single/",
        views.ConfigSyncSingleAPIView.as_view(),
        name="sync_single_api",
    ),
    path(
        "sync/api/progress/",
        views.ConfigSyncProgressView.as_view(),
        name="sync_progress",
    ),
    path("<int:pk>/", views.ConfigDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.ConfigEditView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.ConfigDeleteView.as_view(), name="delete"),
    path("<int:pk>/update/", views.ConfigUpdateView.as_view(), name="update"),
    path(
        "node/<int:pk>/delete/",
        views.ConfigNodeDeleteView.as_view(),
        name="node_delete",
    ),
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
]
