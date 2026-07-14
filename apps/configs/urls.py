from django.urls import path
from . import views

app_name = "configs"

urlpatterns = [
    # 配置标签 CRUD
    path("", views.ConfigListView.as_view(), name="list"),
    path("create/", views.ConfigCreateView.as_view(), name="create"),
    path("<int:pk>/", views.ConfigDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.ConfigEditView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.ConfigDeleteView.as_view(), name="delete"),

    # 配置节点绑定 CRUD
    path("bindings/create/", views.BindingCreateView.as_view(), name="binding_create"),
    path("bindings/<int:pk>/", views.BindingDetailView.as_view(), name="binding_detail"),
    path("bindings/<int:pk>/edit/", views.BindingEditView.as_view(), name="binding_edit"),
    path("bindings/<int:pk>/delete/", views.BindingDeleteView.as_view(), name="binding_delete"),

    # 绑定版本历史
    path("bindings/<int:pk>/versions/", views.BindingVersionListView.as_view(), name="binding_versions"),
    path("bindings/<int:pk>/versions/<int:version_id>/", views.BindingVersionDetailView.as_view(), name="binding_version_detail"),
    path("bindings/<int:pk>/versions/<int:version_id>/restore/", views.BindingVersionRestoreView.as_view(), name="binding_version_restore"),

    # 差异对比
    path("bindings/<int:pk>/compare/", views.BindingVersionCompareView.as_view(), name="binding_compare"),
    path("bindings/<int:pk>/compare/apply/", views.BindingVersionCompareApplyView.as_view(), name="binding_compare_apply"),

    # API
    path("api/by-nodes/", views.ConfigByNodesAPIView.as_view(), name="api_by_nodes"),
    path("api/preview-glob/", views.ConfigGlobPreviewView.as_view(), name="api_preview_glob"),
    path("api/update-preview/", views.ConfigUpdatePreviewView.as_view(), name="api_update_preview"),

    # 同步向导（保留兼容）
    path("sync/", views.ConfigSyncWizardView.as_view(), name="sync_wizard"),
    path("sync/api/batch/", views.ConfigSyncBatchAPIView.as_view(), name="sync_batch_api"),
    path("sync/api/single/", views.ConfigSyncSingleAPIView.as_view(), name="sync_single_api"),
    path("sync/api/progress/", views.ConfigSyncProgressView.as_view(), name="sync_progress"),

    # 兼容旧URL
    path("<int:pk>/update/", views.ConfigUpdateView.as_view(), name="update"),
    path("node/<int:pk>/delete/", views.ConfigNodeDeleteView.as_view(), name="node_delete"),

    # 旧版版本历史（逐步迁移）
    path("<int:pk>/versions/", views.BindingVersionListView.as_view(), name="versions"),
    path("<int:pk>/versions/compare/", views.BindingVersionCompareView.as_view(), name="version_compare"),
    path("<int:pk>/versions/compare/apply/", views.BindingVersionCompareApplyView.as_view(), name="version_compare_apply"),
    path("<int:pk>/versions/<int:version_id>/", views.BindingVersionDetailView.as_view(), name="version_detail"),
    path("<int:pk>/versions/<int:version_id>/restore/", views.BindingVersionRestoreView.as_view(), name="restore"),
]