from django.urls import path
from . import views

app_name = "releases"

urlpatterns = [
    path("create/", views.ReleaseCreateView.as_view(), name="create"),
    path("history/", views.ReleaseListView.as_view(), name="history"),
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
    path("<int:pk>/", views.ReleaseDetailView.as_view(), name="detail"),
    path("<int:pk>/rollback/", views.ReleaseRollbackView.as_view(), name="rollback"),
    path(
        "version/<int:version_id>/content/",
        views.VersionContentAPIView.as_view(),
        name="version_content",
    ),
]
