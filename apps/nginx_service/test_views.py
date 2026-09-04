"""nginx_service 视图层测试（pytest 风格，复用共享 fixture）"""

import json
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.releases.models import TaskCenterTask


@pytest.mark.django_db
class TestNginxServiceViews:
    """页面可访问性与权限校验"""

    def test_index_page_accessible(self, admin_client):
        resp = admin_client.get(reverse("nginx_service:index"))
        assert resp.status_code == 200

    def test_index_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("nginx_service:index"))
        assert resp.status_code == 302
        assert resp.url.startswith("/login/")

    def test_history_page_accessible(self, admin_client, admin_user):
        TaskCenterTask.objects.create(
            operation_type="nginx_service_control",
            status="success",
            target_configs="reload",
            target_hostnames="ngx-1",
            target_ips="10.0.0.11",
            trigger_user=admin_user,
        )
        resp = admin_client.get(reverse("nginx_service:history"))
        assert resp.status_code == 200
        assert len(resp.context["tasks"]) == 1

    def test_history_page_empty(self, admin_client):
        resp = admin_client.get(reverse("nginx_service:history"))
        assert resp.status_code == 200
        assert len(resp.context["tasks"]) == 0


@pytest.mark.django_db
class TestNginxServiceAPI:
    """API 端点测试"""

    @pytest.fixture(autouse=True)
    def setup(self, admin_client, online_node):
        self.client = admin_client
        self.node = online_node
        self.url = reverse("nginx_service:api_execute")

    def _post(self, payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    @pytest.mark.parametrize("action", ["start", "stop", "reload", "restart"])
    def test_valid_actions_create_task(self, action):
        with patch("apps.nginx_service.views.threading.Thread.start"):
            resp = self._post({"node_ids": [self.node.id], "action": action})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["success"] is True
        assert payload["async"] is True
        task = TaskCenterTask.objects.get(pk=payload["task_center_id"])
        assert task.operation_type == "nginx_service_control"
        assert task.target_configs == action

    @pytest.mark.parametrize("action", ["foo", "", "delete", "kill"])
    def test_invalid_actions_rejected(self, action):
        resp = self._post({"node_ids": [self.node.id], "action": action})
        payload = resp.json()
        assert payload["success"] is False

    def test_empty_node_ids_rejected(self):
        resp = self._post({"node_ids": [], "action": "start"})
        payload = resp.json()
        assert payload["success"] is False

    def test_missing_node_ids_rejected(self):
        resp = self._post({"action": "start"})
        payload = resp.json()
        assert payload["success"] is False

    def test_offline_node_rejected(self, offline_node):
        resp = self._post({"node_ids": [offline_node.id], "action": "start"})
        payload = resp.json()
        assert payload["success"] is False

    def test_batch_number_increments(self):
        with patch("apps.nginx_service.views.threading.Thread.start"):
            r1 = self._post({"node_ids": [self.node.id], "action": "start"})
            r2 = self._post({"node_ids": [self.node.id], "action": "reload"})
        b1 = r1.json()["source_batch"]
        b2 = r2.json()["source_batch"]
        assert b1[:10] == b2[:10]
        assert int(b2[-4:]) == int(b1[-4:]) + 1
