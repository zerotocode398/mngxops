"""Nginx 启停 API 测试"""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.credentials.models import Credential
from apps.nodes.models import Node
from apps.releases.models import TaskCenterTask


class NginxServiceExecuteTests(TestCase):
    """启停执行接口门禁与任务创建"""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="pass1234",
        )
        self.client.force_login(self.user)
        self.cred = Credential.objects.create(
            name="test-cred",
            auth_type="password",
            username="root",
            password="secret",
            is_enabled=True,
            created_by=self.user,
        )
        self.node = Node.objects.create(
            hostname="ngx-1",
            ip="10.0.0.11",
            status="online",
            credential=self.cred,
            created_by=self.user,
        )
        self.url = reverse("nginx_service:api_execute")

    def _post(self, payload):
        """POST JSON 到执行接口"""
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_index_page_ok(self):
        """启停页可访问"""
        resp = self.client.get(reverse("nginx_service:index"))
        self.assertEqual(resp.status_code, 200)

    def test_history_page_ok(self):
        """启停历史页可访问并仅展示启停任务"""
        TaskCenterTask.objects.create(
            operation_type="nginx_service_control",
            status="success",
            target_configs="reload",
            target_hostnames="ngx-1",
            target_ips="10.0.0.11",
            trigger_user=self.user,
        )
        TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            status="success",
            trigger_user=self.user,
        )
        resp = self.client.get(reverse("nginx_service:history"))
        self.assertEqual(resp.status_code, 200)
        tasks = list(resp.context["tasks"])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].operation_type, "nginx_service_control")

    def test_invalid_action_rejected(self):
        """非法 action 拒绝"""
        resp = self._post({"node_ids": [self.node.id], "action": "foo"})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertFalse(payload.get("success"))

    def test_offline_node_rejected(self):
        """离线节点不可执行"""
        self.node.status = "offline"
        self.node.save(update_fields=["status"])
        resp = self._post({"node_ids": [self.node.id], "action": "start"})
        payload = resp.json()
        self.assertFalse(payload.get("success"))

    def test_start_creates_task(self):
        """合法请求创建 nginx_service_control 任务"""
        with patch("apps.nginx_service.views.threading.Thread.start"):
            resp = self._post({"node_ids": [self.node.id], "action": "start"})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload.get("success"))
        self.assertTrue(payload.get("async"))
        task = TaskCenterTask.objects.get(pk=payload["task_center_id"])
        self.assertEqual(task.operation_type, "nginx_service_control")
        self.assertEqual(task.target_configs, "start")

    def test_reload_creates_task(self):
        """合法 reload 创建任务"""
        with patch("apps.nginx_service.views.threading.Thread.start"):
            resp = self._post({"node_ids": [self.node.id], "action": "reload"})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload.get("success"))
        task = TaskCenterTask.objects.get(pk=payload["task_center_id"])
        self.assertEqual(task.operation_type, "nginx_service_control")
        self.assertEqual(task.target_configs, "reload")
