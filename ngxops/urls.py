"""
URL configuration for ngxops project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.http import FileResponse, Http404
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from ngxops.runtime_paths import resource_dir


def serve_favicon(request):
    """返回站点图标，消除浏览器默认请求 /favicon.ico 的 404。"""
    icon_path = resource_dir() / "static" / "favicon.png"
    if not icon_path.is_file():
        raise Http404
    return FileResponse(icon_path.open("rb"), content_type="image/png")


urlpatterns = [
    path("favicon.ico", serve_favicon),
    path("admin/", admin.site.urls),
    path("", include("apps.dashboard.urls")),
    path("", include("apps.accounts.urls")),
    path("users/", include("apps.users.urls")),
    path("credentials/", include("apps.credentials.urls")),
    path("nodes/", include("apps.nodes.urls")),
    path("configs/", include("apps.configs.urls")),
    path("releases/", include("apps.releases.urls")),
    path("audit/", include("apps.audit.urls")),
    path("upgrade/", include("apps.upgrade.urls")),
    path("nginx-service/", include("apps.nginx_service.urls")),
    path("nginx-install/", include("apps.nginx_install.urls")),
    path("nginx-uninstall/", include("apps.nginx_uninstall.urls")),
    path("settings/", include("apps.settings.urls")),
]

# 单机交付无独立静态服务器时始终挂载 media（含冻结包 DEBUG=False）
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)