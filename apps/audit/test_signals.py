"""审计信号测试。"""

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import TestCase, RequestFactory

from apps.audit.middleware import CurrentUserMiddleware
from apps.audit.models import AuditLog
from apps.audit.signals import (
    _get_model_label,
    _get_client_ip,
    _truncate_remark,
    _binding_identity,
    _get_instance_label,
    TRACKED_MODELS,
)
from apps.configs.models import Config, ConfigNodeBinding, BindingVersion
from apps.nodes.models import Node


class TestGetModelLabel(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="ml-test",
            email="ml@example.com",
            password="pass1234",
        )

    def test_known_model(self):
        node = Node.objects.create(
            hostname="ml-node",
            ip="10.0.0.50",
            status="online",
            created_by=self.user,
        )
        label = _get_model_label(node)
        self.assertEqual(label, "节点管理")

    def test_unknown_model(self):
        log = AuditLog.objects.create(
            user=self.user,
            module="测试",
            action="测试",
            detail="测试审计日志",
            ip="127.0.0.1",
        )
        label = _get_model_label(log)
        self.assertEqual(label, "AuditLog")

    def test_tracked_models(self):
        self.assertIn("apps.nodes.models.Node", TRACKED_MODELS)
        self.assertIn("apps.configs.models.Config", TRACKED_MODELS)
        self.assertIn("apps.credentials.models.Credential", TRACKED_MODELS)


class TestGetClientIP(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_remote_addr(self):
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "192.168.1.1"

        captured = {}

        def get_response(req):
            captured["ip"] = _get_client_ip()
            return HttpResponse("OK")

        middleware = CurrentUserMiddleware(get_response)
        middleware(request)
        self.assertEqual(captured["ip"], "192.168.1.1")

    def test_x_forwarded_for(self):
        request = self.factory.get("/")
        request.META["HTTP_X_FORWARDED_FOR"] = "10.0.0.1, 10.0.0.2"
        request.META["REMOTE_ADDR"] = "192.168.1.1"

        captured = {}

        def get_response(req):
            captured["ip"] = _get_client_ip()
            return HttpResponse("OK")

        middleware = CurrentUserMiddleware(get_response)
        middleware(request)
        self.assertEqual(captured["ip"], "10.0.0.1")

    def test_no_request(self):
        ip = _get_client_ip()
        self.assertEqual(ip, "0.0.0.0")


class TestTruncateRemark(TestCase):
    def test_short_text(self):
        self.assertEqual(_truncate_remark("简单备注"), "简单备注")

    def test_long_text(self):
        long_text = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ"
        result = _truncate_remark(long_text, max_len=40)
        self.assertLessEqual(len(result), 40)
        self.assertTrue(result.endswith("…"))

    def test_empty_remark(self):
        self.assertEqual(_truncate_remark(""), "")
        self.assertEqual(_truncate_remark(None), "")


class TestBindingIdentity(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="sig-test",
            email="sig@example.com",
            password="pass1234",
        )
        self.node = Node.objects.create(
            hostname="web01",
            ip="10.0.0.1",
            status="online",
            created_by=self.user,
        )
        self.config = Config.objects.create(
            name="app.conf",
            default_remote_path="/etc/nginx/conf.d/app.conf",
            created_by=self.user,
        )
        self.binding = ConfigNodeBinding.objects.create(
            config=self.config,
            node=self.node,
            remote_path="/etc/nginx/conf.d/app.conf",
            content="server { listen 80; }",
            current_version=1,
            created_by=self.user,
        )

    def test_binding_identity(self):
        identity = _binding_identity(self.binding)
        self.assertIn("app.conf", identity)
        self.assertIn("web01", identity)


class TestGetInstanceLabel(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="label-test",
            email="label@example.com",
            password="pass1234",
        )
        self.node = Node.objects.create(
            hostname="web02",
            ip="10.0.0.2",
            status="online",
            created_by=self.user,
        )
        self.config = Config.objects.create(
            name="nginx.conf",
            default_remote_path="/etc/nginx/nginx.conf",
            created_by=self.user,
        )

    def test_config_label(self):
        label = _get_instance_label(self.config)
        self.assertEqual(label, "nginx.conf")

    def test_node_label(self):
        label = _get_instance_label(self.node)
        self.assertEqual(label, self.node.pk)

    def test_user_label(self):
        label = _get_instance_label(self.user)
        self.assertEqual(label, "label-test")


class TestAuditSignalsIntegration(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="audit-sig",
            email="audit-sig@example.com",
            password="pass1234",
        )

    def _perform_with_request(self, action):
        request = RequestFactory().get("/configs/")
        request.user = self.user

        def get_response(req):
            action()
            return HttpResponse("OK")

        middleware = CurrentUserMiddleware(get_response)
        middleware(request)

    def test_config_create_audit_log(self):
        def action():
            Config.objects.create(
                name="audit-test.conf",
                default_remote_path="/etc/nginx/conf.d/audit-test.conf",
                created_by=self.user,
            )

        self._perform_with_request(action)
        log = AuditLog.objects.filter(module="配置管理", action="创建配置管理").first()
        self.assertIsNotNone(log)

    def test_config_delete_audit_log(self):
        config = Config.objects.create(
            name="to-delete.conf",
            default_remote_path="/etc/nginx/conf.d/to-delete.conf",
            created_by=self.user,
        )

        def action():
            Config.objects.filter(pk=config.pk).delete()

        self._perform_with_request(action)
        log = AuditLog.objects.filter(module="配置管理", action="删除配置管理").first()
        self.assertIsNotNone(log)

    def test_node_update_audit_log(self):
        node = Node.objects.create(
            hostname="update-node",
            ip="10.0.0.99",
            status="online",
            created_by=self.user,
        )

        def action():
            n = Node.objects.get(pk=node.pk)
            n.status = "offline"
            n.save()

        self._perform_with_request(action)
        log = AuditLog.objects.filter(module="节点管理", action="更新节点管理").first()
        self.assertIsNotNone(log)
