from django.urls import path
from . import views

app_name = "nodes"

urlpatterns = [
    path("", views.NodeListView.as_view(), name="list"),
    path("api/list/", views.NodeListAPIView.as_view(), name="api_list"),
    path(
        "api/search-nodes/", views.NodeSearchAPIView.as_view(), name="api_search_nodes"
    ),
    path("api/groups/", views.NodeGroupListAPIView.as_view(), name="api_groups"),
    path("create/", views.NodeCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", views.NodeUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.NodeDeleteView.as_view(), name="delete"),
    path("lock/", views.node_lock, name="lock"),
    path("test/", views.test_node_connection, name="test"),
    path("batch-test/", views.batch_test_node_connection, name="batch_test"),
    path("nginx-version/", views.get_node_nginx_version, name="nginx-version"),
    path("detail/", views.get_node_detail, name="detail"),
    path("system-info/", views.get_node_system_info, name="system-info"),
    path("groups/", views.NodeGroupListView.as_view(), name="group_list"),
    path("groups/create/", views.NodeGroupCreateView.as_view(), name="group_create"),
    path(
        "groups/<int:pk>/edit/", views.NodeGroupUpdateView.as_view(), name="group_edit"
    ),
    path(
        "groups/<int:pk>/delete/",
        views.NodeGroupDeleteView.as_view(),
        name="group_delete",
    ),
    path(
        "groups/<int:pk>/manage-nodes/",
        views.NodeGroupManageNodesView.as_view(),
        name="group_manage_nodes",
    ),
]
