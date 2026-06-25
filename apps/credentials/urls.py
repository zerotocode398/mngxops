from django.urls import path
from . import views

app_name = "credentials"

urlpatterns = [
    path("", views.CredentialListView.as_view(), name="list"),
    path("create/", views.CredentialCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", views.CredentialUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.CredentialDeleteView.as_view(), name="delete"),
]
