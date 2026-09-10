"""Nginx 卸载服务层测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.nginx_uninstall.services import (
    normalize_remote_path,
    is_dangerous_path,
    is_shallow_prefix,
    is_valid_package_name,
    derive_prefix_from_nginx_path,
    is_file_like_path,
    resolve_nginx_tree_path,
    coalesce_delete_targets,
    is_under_path,
    _FORBIDDEN_PATHS,
    batch_max_count,
    uninstall_gate_message,
)
from apps.nodes.models import Node


class TestNormalizeRemotePath(TestCase):
    def test_normal_path(self):
        self.assertEqual(normalize_remote_path("/opt/nginx/"), "/opt/nginx")

    def test_root(self):
        self.assertEqual(normalize_remote_path("/"), "/")

    def test_empty(self):
        self.assertEqual(normalize_remote_path(""), "")

    def test_relative_path(self):
        self.assertEqual(normalize_remote_path("relative/path"), "")

    def test_double_slash(self):
        self.assertEqual(normalize_remote_path("//opt//nginx//"), "/opt/nginx")


class TestIsDangerousPath(TestCase):
    def test_empty(self):
        self.assertTrue(is_dangerous_path(""))

    def test_root(self):
        self.assertTrue(is_dangerous_path("/"))

    def test_usr(self):
        self.assertTrue(is_dangerous_path("/usr"))

    def test_etc(self):
        self.assertTrue(is_dangerous_path("/etc"))

    def test_normal_path(self):
        self.assertFalse(is_dangerous_path("/opt/app/nginx"))

    def test_forbidden_set(self):
        for path in [
            "/",
            "/usr",
            "/usr/local",
            "/etc",
            "/var",
            "/home",
            "/boot",
            "/root",
            "/dev",
            "/proc",
            "/sys",
            "/tmp",
            "/bin",
            "/sbin",
            "/lib",
            "/lib64",
            "/run",
            "/media",
            "/mnt",
            "/opt",
        ]:
            self.assertIn(path, _FORBIDDEN_PATHS, f"Expected {path} in forbidden")


class TestIsShallowPrefix(TestCase):
    def test_shallow(self):
        self.assertTrue(is_shallow_prefix("/data"))

    def test_deep(self):
        self.assertFalse(is_shallow_prefix("/opt/app/nginx"))

    def test_root(self):
        self.assertFalse(is_shallow_prefix("/"))

    def test_empty(self):
        self.assertFalse(is_shallow_prefix(""))


class TestIsValidPackageName(TestCase):
    def test_valid_name(self):
        self.assertTrue(is_valid_package_name("nginx"))

    def test_valid_with_version(self):
        self.assertTrue(is_valid_package_name("nginx-1.24.0"))

    def test_empty(self):
        self.assertFalse(is_valid_package_name(""))

    def test_with_spaces(self):
        self.assertFalse(is_valid_package_name("nginx package"))

    def test_error_message(self):
        self.assertFalse(is_valid_package_name("not owned by any package"))
        self.assertFalse(is_valid_package_name("error reading package"))


class TestDerivePrefixFromNginxPath(TestCase):
    def test_sbin_nginx(self):
        result = derive_prefix_from_nginx_path("/opt/app/sbin/nginx")
        self.assertEqual(result, "/opt/app")

    def test_nginx_only(self):
        result = derive_prefix_from_nginx_path("/usr/bin/nginx")
        self.assertNotEqual(result, "/usr/bin")

    def test_empty(self):
        self.assertEqual(derive_prefix_from_nginx_path(""), "")


class TestIsFileLikePath(TestCase):
    def test_file_with_extension(self):
        self.assertTrue(is_file_like_path("/etc/nginx/nginx.conf"))

    def test_sbin_nginx(self):
        self.assertTrue(is_file_like_path("/opt/app/sbin/nginx"))

    def test_directory(self):
        self.assertFalse(is_file_like_path("/opt/app/nginx"))

    def test_empty(self):
        self.assertFalse(is_file_like_path(""))


class TestResolveNginxTreePath(TestCase):
    def test_config_file(self):
        result = resolve_nginx_tree_path("/etc/nginx/nginx.conf")
        self.assertEqual(result, "/etc/nginx")

    def test_sbin_nginx(self):
        result = resolve_nginx_tree_path("/usr/sbin/nginx")
        self.assertEqual(result, "/usr/sbin/nginx")

    def test_empty(self):
        self.assertEqual(resolve_nginx_tree_path(""), "")


class TestCoalesceDeleteTargets(TestCase):
    def test_empty(self):
        self.assertEqual(coalesce_delete_targets([]), [])

    def test_single(self):
        result = coalesce_delete_targets(["/opt/nginx"])
        self.assertEqual(result, ["/opt/nginx"])

    def test_parent_child(self):
        result = coalesce_delete_targets(["/opt/nginx", "/opt/nginx/conf"])
        self.assertEqual(result, ["/opt/nginx"])

    def test_unrelated(self):
        result = coalesce_delete_targets(["/opt/nginx", "/etc/nginx"])
        self.assertEqual(len(result), 2)


class TestIsUnderPath(TestCase):
    def test_equal(self):
        self.assertTrue(is_under_path("/opt/nginx", "/opt/nginx"))

    def test_child(self):
        self.assertTrue(is_under_path("/opt/nginx/conf", "/opt/nginx"))

    def test_unrelated(self):
        self.assertFalse(is_under_path("/opt/nginx", "/etc/nginx"))

    def test_empty(self):
        self.assertFalse(is_under_path("", "/opt/nginx"))


class TestBatchMaxCount(TestCase):
    def test_returns_positive_int(self):
        count = batch_max_count()
        self.assertIsInstance(count, int)
        self.assertGreaterEqual(count, 1)


class TestUninstallGateMessage(TestCase):
    def test_locked_node(self):
        node = Node(
            hostname="locked-node",
            ip="10.0.0.30",
            status="online",
            nginx_available=True,
            is_locked=True,
        )
        msg = uninstall_gate_message(node)
        self.assertIsNotNone(msg)
        self.assertIn("锁定", msg)

    def test_offline_node(self):
        node = Node(
            hostname="offline-node",
            ip="10.0.0.31",
            status="offline",
            nginx_available=True,
        )
        msg = uninstall_gate_message(node)
        self.assertIsNotNone(msg)

    def test_no_nginx(self):
        node = Node(
            hostname="no-nginx",
            ip="10.0.0.32",
            status="online",
            nginx_available=False,
        )
        msg = uninstall_gate_message(node)
        self.assertIsNotNone(msg)

    def test_ok_node(self):
        from apps.credentials.models import Credential

        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="uninstall-ok",
            email="uninstall-ok@example.com",
            password="pass1234",
        )
        cred = Credential.objects.create(
            name="uninstall-cred",
            username="root",
            auth_type="password",
            password="encrypted",
            is_enabled=True,
            created_by=user,
        )
        node = Node(
            hostname="ok-uninstall",
            ip="10.0.0.33",
            status="online",
            nginx_available=True,
            credential=cred,
        )
        msg = uninstall_gate_message(node)
        self.assertIsNone(msg)
