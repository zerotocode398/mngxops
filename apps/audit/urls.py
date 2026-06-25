from django.urls import path
from . import views

app_name = "audit"

urlpatterns = [
    path("", views.AuditLogListView.as_view(), name="list"),
    path("login/", views.LoginLogListView.as_view(), name="login_list"),
]
