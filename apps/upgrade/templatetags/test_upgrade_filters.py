"""升级模块模板过滤器测试。"""

from django.test import TestCase

from apps.upgrade.templatetags.upgrade_filters import nginx_ver


class TestNginxVerFilter(TestCase):
    def test_nginx_prefix(self):
        self.assertEqual(nginx_ver("nginx/1.24.0"), "1.24.0")

    def test_nginx_dash_prefix(self):
        self.assertEqual(nginx_ver("nginx-1.26.1"), "1.26.1")

    def test_plain_version(self):
        self.assertEqual(nginx_ver("1.24.0"), "1.24.0")

    def test_none(self):
        self.assertEqual(nginx_ver(None), "")

    def test_empty(self):
        self.assertEqual(nginx_ver(""), "")
        self.assertEqual(nginx_ver("   "), "")

    def test_with_build_info(self):
        self.assertEqual(
            nginx_ver("nginx/1.25.3 (built by gcc)"), "1.25.3 (built by gcc)"
        )

    def test_mixed_case(self):
        self.assertEqual(nginx_ver("NGINX/1.24.0"), "1.24.0")
