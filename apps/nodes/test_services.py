"""节点服务层测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.credentials.models import Credential
from apps.nodes.services import (
    _cell_str,
    _is_empty_optional,
    _split_group_names,
    _normalize_environment,
    _parse_port,
    _default_ssh_port,
    _default_nginx_bin,
    _default_nginx_conf,
    resolve_credential_by_name,
    mark_node_probe_success,
    nginx_ops_gate_message,
    install_gate_message,
)
from apps.nodes.models import Node


class TestCellStr(TestCase):
    def test_none(self):
        self.assertEqual(_cell_str(None), "")

    def test_string(self):
        self.assertEqual(_cell_str(" hello "), "hello")

    def test_float_int(self):
        self.assertEqual(_cell_str(22.0), "22")


class TestIsEmptyOptional(TestCase):
    def test_empty(self):
        self.assertTrue(_is_empty_optional(""))
        self.assertTrue(_is_empty_optional("-"))
        self.assertTrue(_is_empty_optional("n/a"))

    def test_text(self):
        self.assertFalse(_is_empty_optional("hello"))


class TestSplitGroupNames(TestCase):
    def test_empty(self):
        self.assertEqual(_split_group_names(""), [])
        self.assertEqual(_split_group_names("-"), [])

    def test_single(self):
        self.assertEqual(_split_group_names("默认组"), ["默认组"])

    def test_comma_separated(self):
        self.assertEqual(_split_group_names("组A,组B"), ["组A", "组B"])

    def test_chinese_comma(self):
        self.assertEqual(_split_group_names("组A，组B"), ["组A", "组B"])

    def test_deduplication(self):
        self.assertEqual(_split_group_names("组A,组A,组B"), ["组A", "组B"])


class TestNormalizeEnvironment(TestCase):
    def test_empty_defaults_test(self):
        env, err = _normalize_environment("")
        self.assertEqual(env, "test")
        self.assertIsNone(err)

    def test_dev(self):
        env, err = _normalize_environment("dev")
        self.assertEqual(env, "dev")
        self.assertIsNone(err)

    def test_development(self):
        env, err = _normalize_environment("development")
        self.assertEqual(env, "dev")
        self.assertIsNone(err)

    def test_chinese_dev(self):
        env, err = _normalize_environment("开发")
        self.assertEqual(env, "dev")
        self.assertIsNone(err)

    def test_test(self):
        env, err = _normalize_environment("test")
        self.assertEqual(env, "test")
        self.assertIsNone(err)

    def test_prod(self):
        env, err = _normalize_environment("prod")
        self.assertEqual(env, "prod")
        self.assertIsNone(err)

    def test_chinese_prod(self):
        env, err = _normalize_environment("生产")
        self.assertEqual(env, "prod")
        self.assertIsNone(err)

    def test_invalid(self):
        env, err = _normalize_environment("invalid")
        self.assertIsNone(env)
        self.assertIsNotNone(err)


class TestParsePort(TestCase):
    def test_valid_port(self):
        port, err = _parse_port("22")
        self.assertEqual(port, 22)
        self.assertIsNone(err)

    def test_empty_port(self):
        port, err = _parse_port("")
        self.assertIsNone(port)
        self.assertIsNotNone(err)

    def test_invalid_port(self):
        port, err = _parse_port("abc")
        self.assertIsNone(port)
        self.assertIsNotNone(err)

    def test_out_of_range(self):
        port, err = _parse_port("99999")
        self.assertIsNone(port)
        self.assertIsNotNone(err)

    def test_float_port(self):
        port, err = _parse_port("22.0")
        self.assertEqual(port, 22)
        self.assertIsNone(err)


class TestDefaultSettings(TestCase):
    def test_default_ssh_port(self):
        port = _default_ssh_port()
        self.assertIsInstance(port, int)
        self.assertGreaterEqual(port, 1)
        self.assertLessEqual(port, 65535)

    def test_default_nginx_bin(self):
        path = _default_nginx_bin()
        self.assertIsInstance(path, str)
        self.assertTrue(len(path) > 0)

    def test_default_nginx_conf(self):
        path = _default_nginx_conf()
        self.assertIsInstance(path, str)
        self.assertTrue(len(path) > 0)


class TestResolveCredentialByName(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="node-svc",
            email="node-svc@example.com",
            password="pass1234",
        )

    def test_empty_name(self):
        cred, err = resolve_credential_by_name("", self.user)
        self.assertIsNone(cred)
        self.assertIsNone(err)

    def test_not_found(self):
        cred, err = resolve_credential_by_name("nonexistent", self.user)
        self.assertIsNone(cred)
        self.assertIsNotNone(err)

    def test_found_own_credential(self):
        cred_obj = Credential.objects.create(
            name="node-cred",
            username="root",
            auth_type="password",
            password="encrypted",
            is_enabled=True,
            created_by=self.user,
        )
        cred, err = resolve_credential_by_name("node-cred", self.user)
        self.assertIsNotNone(cred)
        self.assertEqual(cred.id, cred_obj.id)
        self.assertIsNone(err)


class TestMarkNodeProbeSuccess(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="probe-svc",
            email="probe@example.com",
            password="pass1234",
        )

    def test_marks_online(self):
        node = Node(
            hostname="test-node",
            ip="10.0.0.5",
            status="unknown",
            created_by=self.user,
        )
        node = mark_node_probe_success(node)
        self.assertEqual(node.status, "online")
        self.assertIsNotNone(node.last_probe_at)


class TestNginxOpsGateMessage(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="gate-svc",
            email="gate@example.com",
            password="pass1234",
        )

    def test_offline_node(self):
        node = Node(
            hostname="offline-node",
            ip="10.0.0.6",
            status="offline",
            created_by=self.user,
        )
        msg = nginx_ops_gate_message(node)
        self.assertIsNotNone(msg)

    def test_no_nginx_probe(self):
        node = Node(
            hostname="no-probe-node",
            ip="10.0.0.7",
            status="online",
            nginx_available=None,
            created_by=self.user,
        )
        msg = nginx_ops_gate_message(node)
        self.assertIsNotNone(msg)

    def test_nginx_available(self):
        node = Node(
            hostname="ok-node",
            ip="10.0.0.8",
            status="online",
            nginx_available=True,
            created_by=self.user,
        )
        msg = nginx_ops_gate_message(node)
        self.assertIsNone(msg)


class TestInstallGateMessage(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="install-gate",
            email="install-gate@example.com",
            password="pass1234",
        )

    def test_online_node(self):
        node = Node(
            hostname="install-node",
            ip="10.0.0.9",
            status="online",
            created_by=self.user,
        )
        msg = install_gate_message(node)
        self.assertIsNone(msg)

    def test_offline_node(self):
        node = Node(
            hostname="offline-install",
            ip="10.0.0.10",
            status="offline",
            created_by=self.user,
        )
        msg = install_gate_message(node)
        self.assertIsNotNone(msg)
