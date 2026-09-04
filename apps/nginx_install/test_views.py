"""nginx_install 视图层测试（安装页面与 API）"""

import pytest
from django.urls import reverse

from apps.nginx_install.models import NginxInstallTask


@pytest.mark.django_db
class TestNginxInstallIndexView:
    """Nginx 安装首页"""

    def test_index_accessible(self, admin_client):
        resp = admin_client.get(reverse("nginx_install:index"))
        assert resp.status_code == 200

    def test_index_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("nginx_install:index"))
        assert resp.status_code == 302

    def test_index_contains_stats(self, admin_client):
        resp = admin_client.get(reverse("nginx_install:index"))
        assert resp.status_code == 200
        assert "package_count" in resp.context
        assert "running_count" in resp.context
        assert "failed_7d_count" in resp.context
        assert "recent_tasks" in resp.context


@pytest.mark.django_db
class TestNginxInstallCenterView:
    """Nginx 安装向导"""

    def test_center_accessible(self, admin_client):
        resp = admin_client.get(reverse("nginx_install:center"))
        assert resp.status_code == 200

    def test_center_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("nginx_install:center"))
        assert resp.status_code == 302

    def test_center_contains_packages(self, admin_client):
        resp = admin_client.get(reverse("nginx_install:center"))
        assert resp.status_code == 200
        assert "packages" in resp.context
        assert "default_make_jobs" in resp.context


@pytest.mark.django_db
class TestNginxInstallHistoryView:
    """Nginx 安装历史"""

    def test_history_accessible(self, admin_client):
        resp = admin_client.get(reverse("nginx_install:history"))
        assert resp.status_code == 200

    def test_history_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("nginx_install:history"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestNginxInstallTaskLogView:
    """安装任务日志页"""

    def test_task_log_accessible(self, admin_client, admin_user, online_node):
        task = NginxInstallTask.objects.create(
            node=online_node,
            target_version="1.24.0",
            target_prefix="/usr/local/nginx",
            operator=admin_user,
        )
        resp = admin_client.get(reverse("nginx_install:task_log", args=[task.id]))
        assert resp.status_code == 200
        assert resp.context["task"] == task

    def test_task_log_redirects_anonymous(
        self, anonymous_client, admin_user, online_node
    ):
        task = NginxInstallTask.objects.create(
            node=online_node,
            target_version="1.24.0",
            target_prefix="/usr/local/nginx",
            operator=admin_user,
        )
        resp = anonymous_client.get(reverse("nginx_install:task_log", args=[task.id]))
        assert resp.status_code == 302

    def test_task_log_not_found(self, admin_client):
        resp = admin_client.get(reverse("nginx_install:task_log", args=[99999]))
        assert resp.status_code == 404


@pytest.mark.django_db
class TestNginxInstallTaskLogAPIView:
    """安装任务日志 API"""

    def test_api_returns_log(self, admin_client, admin_user, online_node):
        task = NginxInstallTask.objects.create(
            node=online_node,
            target_version="1.24.0",
            target_prefix="/usr/local/nginx",
            log_output="configure: ok\nmake: ok",
            status="compiling",
            progress=50,
            operator=admin_user,
        )
        resp = admin_client.get(reverse("nginx_install:api_task_log", args=[task.id]))
        payload = resp.json()
        assert payload["success"] is True
        assert payload["status"] == "compiling"
        assert payload["progress"] == 50
        assert "configure: ok" in payload["log_output"]

    def test_api_task_not_found(self, admin_client):
        resp = admin_client.get(reverse("nginx_install:api_task_log", args=[99999]))
        assert resp.status_code == 404


@pytest.mark.django_db
class TestNginxInstallBatchProgressAPIView:
    """安装批次进度 API"""

    def test_api_missing_batch(self, admin_client):
        resp = admin_client.get(reverse("nginx_install:api_batch_progress"))
        payload = resp.json()
        assert payload["success"] is False

    def test_api_nonexistent_batch(self, admin_client):
        resp = admin_client.get(
            reverse("nginx_install:api_batch_progress"), {"batch": "IN-000000-0000"}
        )
        payload = resp.json()
        assert payload["success"] is False


@pytest.mark.django_db
class TestNginxInstallTaskCreateAPIView:
    """安装任务创建 API"""

    def test_api_create_requires_permission(self, anonymous_client):
        resp = anonymous_client.post(
            reverse("nginx_install:api_create"),
            data="{}",
            content_type="application/json",
        )
        assert resp.status_code == 302
