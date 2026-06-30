from django.urls import path
from . import views

app_name = "credentials"

urlpatterns = [
    path("", views.CredentialListView.as_view(), name="list"),
    path("create/", views.CredentialCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", views.CredentialUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.CredentialDeleteView.as_view(), name="delete"),
    path(
        "<int:pk>/toggle-enable/",
        views.CredentialToggleEnableView.as_view(),
        name="toggle_enable",
    ),
    path("<int:pk>/decrypt/", views.CredentialDecryptView.as_view(), name="decrypt"),
    path(
        "<int:pk>/related-nodes/",
        views.CredentialRelatedNodesView.as_view(),
        name="related_nodes",
    ),
    path(
        "<int:pk>/enable-progress/",
        views.CredentialEnableProgressView.as_view(),
        name="enable_progress",
    ),
]
