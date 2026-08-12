"""Nginx 卸载模块单元测试"""
from django.test import SimpleTestCase, TestCase
from django.contrib.auth import get_user_model

from apps.nginx_uninstall.services import (
    derive_prefix_from_nginx_path,
    is_dangerous_path,
    normalize_remote_path,
    uninstall_gate_message,
)
from apps.nodes.models import Node


class PathSafetyTests(SimpleTestCase):
    """路径规范化与危险路径校验"""

    def test_normalize_strips_trailing_slash(self):
        """去掉尾部斜杠"""
        self.assertEqual(normalize_remote_path("/opt/app/"), "/opt/app")
        self.assertEqual(normalize_remote_path("/"), "/")

    def test_dangerous_roots_rejected(self):
        """系统根路径禁止删除"""
        for p in ("/", "/usr", "/opt", "/tmp", "/etc", ""):
            self.assertTrue(is_dangerous_path(p), p)

    def test_normal_prefix_allowed(self):
        """正常 prefix 允许"""
        self.assertFalse(is_dangerous_path("/opt/app"))
        self.assertFalse(is_dangerous_path("/usr/local/nginx"))

    def test_derive_prefix_from_sbin(self):
        """由 sbin/nginx 推导 prefix"""
        self.assertEqual(
            derive_prefix_from_nginx_path("/opt/app/sbin/nginx"),
            "/opt/app",
        )


class UninstallGateTests(TestCase):
    """卸载门禁"""

    def setUp(self):
        """准备在线节点"""
        User = get_user_model()
        self.user = User.objects.create_user(username="un_tester", password="x")
        self.node = Node.objects.create(
            hostname="host-a",
            ip="10.0.0.1",
            port=22,
            status="online",
            nginx_available=True,
            nginx_path="/opt/app/sbin/nginx",
            created_by=self.user,
        )

    def test_online_with_nginx_ok(self):
        """在线且 Nginx 可用可通过（无凭证时仍失败）"""
        msg = uninstall_gate_message(self.node)
        self.assertEqual(msg, "未配置凭证")
