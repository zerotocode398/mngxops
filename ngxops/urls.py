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

import re

from django.contrib import admin
from django.contrib.staticfiles.views import serve as staticfiles_serve
from django.http import FileResponse, Http404
from django.urls import include, path, re_path
from django.conf import settings
from django.views.static import serve as media_serve

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
    path("openssh-upgrade/", include("apps.openssh_upgrade.urls")),
    path("settings/", include("apps.settings.urls")),
]


def _mount_delivery_files():
    """DEBUG=False 时由进程托管 /static/ 与 /media/（Waitress 无 StaticFilesHandler）。"""
    static_prefix = settings.STATIC_URL.lstrip("/")
    media_prefix = settings.MEDIA_URL.lstrip("/")
    return [
        re_path(
            r"^%s(?P<path>.*)$" % re.escape(static_prefix),
            staticfiles_serve,
            {"insecure": True},
        ),
        re_path(
            r"^%s(?P<path>.*)$" % re.escape(media_prefix),
            media_serve,
            {"document_root": str(settings.MEDIA_ROOT)},
        ),
    ]


# django.conf.urls.static.static() 在 DEBUG=False 时返回空列表，不可用于冻结包
if not settings.DEBUG:
    urlpatterns += _mount_delivery_files()