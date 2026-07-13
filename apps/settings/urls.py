"""系统设置模块 - URL 配置"""
from django.urls import path
from . import views

app_name = "settings"

urlpatterns = [
    path("", views.SettingsIndexView.as_view(), name="index"),
    path("save/", views.SettingsSaveAPIView.as_view(), name="save"),
    path("api/group/", views.SettingsGroupAPIView.as_view(), name="api_group"),
    path("api/all/", views.SettingsAllAPIView.as_view(), name="api_all"),
]