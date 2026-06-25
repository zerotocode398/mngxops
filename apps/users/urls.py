from django.urls import path
from . import views

app_name = "users"

urlpatterns = [
    path("", views.UserListView.as_view(), name="list"),
    path("create/", views.UserCreateView.as_view(), name="create"),
    path("<slug:username>/edit/", views.UserUpdateView.as_view(), name="edit"),
    path("<slug:username>/delete/", views.UserDeleteView.as_view(), name="delete"),
    path("groups/", views.UserGroupListView.as_view(), name="group_list"),
    path("groups/create/", views.UserGroupCreateView.as_view(), name="group_create"),
    path(
        "groups/<int:pk>/edit/", views.UserGroupUpdateView.as_view(), name="group_edit"
    ),
    path(
        "groups/<int:pk>/delete/",
        views.UserGroupDeleteView.as_view(),
        name="group_delete",
    ),
    path(
        "groups/<int:pk>/manage-users/",
        views.UserGroupManageUsersView.as_view(),
        name="group_manage_users",
    ),
    path("roles/", views.UserGroupListView.as_view(), name="role_list"),
    path("roles/create/", views.UserGroupCreateView.as_view(), name="role_create"),
    path("roles/<int:pk>/edit/", views.UserGroupUpdateView.as_view(), name="role_edit"),
    path(
        "roles/<int:pk>/delete/",
        views.UserGroupDeleteView.as_view(),
        name="role_delete",
    ),
    path(
        "roles/<int:pk>/manage-users/",
        views.UserGroupManageUsersView.as_view(),
        name="role_manage_users",
    ),
]
