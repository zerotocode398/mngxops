"""nginx_uninstall 视图层测试（卸载页面与 API）"""

import pytest
from django.urls import reverse

from apps.nginx_uninstall.models import NginxUninstallTask


@pytest.mark.django_db
class TestNginxUninstallIndexView:
    """卸载首页"""

    def test_index_accessible(self, admin_client):
        resp = admin_client.get(reverse("nginx_uninstall:index"))
        assert resp.status_code == 200

    def test_index_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("nginx_uninstall:index"))
        assert resp.status_code == 302

    def test_index_contains_stats(self, admin_client):
        resp = admin_client.get(reverse("nginx_uninstall:index"))
        assert resp.status_code == 200
        assert "running_count" in resp.context
        assert "failed_7d_count" in resp.context
        assert "recent_tasks" in resp.context


@pytest.mark.django_db
class TestNginxUninstallCenterView:
    """卸载向导"""

    def test_center_accessible(self, admin_client):
        resp = admin_client.get(reverse("nginx_uninstall:center"))
        assert resp.status_code == 200

    def test_center_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("nginx_uninstall:center"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestNginxUninstallHistoryView:
    """卸载历史"""

    def test_history_accessible(self, admin_client):
        resp = admin_client.get(reverse("nginx_uninstall:history"))
        assert resp.status_code == 200

    def test_history_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("nginx_uninstall:history"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestNginxUninstallTaskLogView:
    """卸载任务日志页"""

    def test_task_log_accessible(self, admin_client, admin_user, online_node):
        task = NginxUninstallTask.objects.create(
            node=online_node,
            resolved_prefix="/usr/local/nginx",
            operator=admin_user,
        )
        resp = admin_client.get(reverse("nginx_uninstall:task_log", args=[task.id]))
        assert resp.status_code == 200
        assert resp.context["task"] == task

    def test_task_log_redirects_anonymous(
        self, anonymous_client, admin_user, online_node
    ):
        task = NginxUninstallTask.objects.create(
            node=online_node,
            resolved_prefix="/usr/local/nginx",
            operator=admin_user,
        )
        resp = anonymous_client.get(reverse("nginx_uninstall:task_log", args=[task.id]))
        assert resp.status_code == 302

    def test_task_log_not_found(self, admin_client):
        resp = admin_client.get(reverse("nginx_uninstall:task_log", args=[99999]))
        assert resp.status_code == 404


@pytest.mark.django_db
class TestNginxUninstallTaskLogAPIView:
    """卸载任务日志 API"""

    def test_api_returns_log(self, admin_client, admin_user, online_node):
        task = NginxUninstallTask.objects.create(
            node=online_node,
            resolved_prefix="/usr/local/nginx",
            log_output="stopping nginx...\nremoving files...",
            status="removing_prefix",
            progress=60,
            operator=admin_user,
        )
        resp = admin_client.get(reverse("nginx_uninstall:api_task_log", args=[task.id]))
        payload = resp.json()
        assert payload["success"] is True
        assert payload["status"] == "removing_prefix"
        assert payload["progress"] == 60

    def test_api_task_not_found(self, admin_client):
        resp = admin_client.get(reverse("nginx_uninstall:api_task_log", args=[99999]))
        assert resp.status_code == 404


@pytest.mark.django_db
class TestNginxUninstallBatchProgressAPIView:
    """卸载批次进度 API"""

    def test_api_missing_batch(self, admin_client):
        resp = admin_client.get(reverse("nginx_uninstall:api_batch_progress"))
        payload = resp.json()
        assert payload["success"] is False

    def test_api_nonexistent_batch(self, admin_client):
        resp = admin_client.get(
            reverse("nginx_uninstall:api_batch_progress"), {"batch": "UN-000000-0000"}
        )
        payload = resp.json()
        assert payload["success"] is False


@pytest.mark.django_db
class TestNginxUninstallPreviewAPIView:
    """卸载预览 API"""

    def test_api_preview_requires_permission(self, anonymous_client):
        resp = anonymous_client.post(
            reverse("nginx_uninstall:api_preview"),
            data="{}",
            content_type="application/json",
        )
        assert resp.status_code == 302


@pytest.mark.django_db
class TestNginxUninstallCreateAPIView:
    """卸载任务创建 API"""

    def test_api_create_requires_permission(self, anonymous_client):
        resp = anonymous_client.post(
            reverse("nginx_uninstall:api_create"),
            data="{}",
            content_type="application/json",
        )
        assert resp.status_code == 302
