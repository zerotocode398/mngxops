from django.urls import path
from . import views

app_name = "users"

urlpatterns = [
    path("", views.UserListView.as_view(), name="list"),
    path("create/", views.UserCreateView.as_view(), name="create"),
    path("<slug:username>/edit/", views.UserUpdateView.as_view(), name="edit"),
    path("<slug:username>/delete/", views.UserDeleteView.as_view(), name="delete"),
    path(
        "<slug:username>/lock/",
        views.UserLockToggleView.as_view(),
        name="lock_toggle",
    ),
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
    # 用户组 (UserTeam)
    path("teams/", views.UserTeamListView.as_view(), name="team_list"),
    path("teams/create/", views.UserTeamCreateView.as_view(), name="team_create"),
    path("teams/<int:pk>/edit/", views.UserTeamUpdateView.as_view(), name="team_edit"),
    path("teams/<int:pk>/delete/", views.UserTeamDeleteView.as_view(), name="team_delete"),
    path("teams/<int:pk>/members/", views.UserTeamMemberListView.as_view(), name="team_members"),
    path("teams/<int:pk>/manage-members/", views.UserTeamManageMembersView.as_view(), name="team_manage_members"),
]
