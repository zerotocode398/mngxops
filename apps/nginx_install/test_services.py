"""Nginx 安装服务层测试。"""

from django.test import TestCase

from apps.nginx_install.services import (
    derive_paths_from_prefix,
    _default_install_prefix,
    _default_listen_port,
    build_install_configure_opts,
    DEFAULT_INSTALL_MODULES,
)


class TestDefaultInstallPrefix(TestCase):
    def test_returns_string(self):
        prefix = _default_install_prefix()
        self.assertIsInstance(prefix, str)
        self.assertTrue(len(prefix) > 0)


class TestDefaultListenPort(TestCase):
    def test_returns_valid_port(self):
        port = _default_listen_port()
        self.assertIsInstance(port, int)
        self.assertGreaterEqual(port, 1)
        self.assertLessEqual(port, 65535)


class TestDerivePathsFromPrefix(TestCase):
    def test_standard_prefix(self):
        result = derive_paths_from_prefix("/opt/app")
        self.assertEqual(result["prefix"], "/opt/app")
        self.assertEqual(result["nginx_path"], "/opt/app/sbin/nginx")
        self.assertEqual(result["main_conf_path"], "/opt/app/conf/nginx.conf")

    def test_trailing_slash(self):
        result = derive_paths_from_prefix("/opt/app/")
        self.assertEqual(result["prefix"], "/opt/app")
        self.assertEqual(result["nginx_path"], "/opt/app/sbin/nginx")

    def test_empty_prefix(self):
        result = derive_paths_from_prefix("")
        self.assertTrue(result["prefix"])
        self.assertIn("sbin/nginx", result["nginx_path"])


class TestBuildInstallConfigureOpts(TestCase):
    def test_basic_opts(self):
        result = build_install_configure_opts(
            prefix="/opt/app",
            added_modules=[],
            added_third_party=[],
            remote_work_dir="/tmp/work",
        )
        self.assertIn("--prefix=/opt/app", result)

    def test_with_user_group(self):
        result = build_install_configure_opts(
            prefix="/opt/app",
            added_modules=[],
            added_third_party=[],
            remote_work_dir="/tmp/work",
            user="nginx",
            group="nginx",
        )
        self.assertIn("--user=nginx", result)
        self.assertIn("--group=nginx", result)

    def test_with_added_modules(self):
        result = build_install_configure_opts(
            prefix="/opt/app",
            added_modules=["--with-http_ssl_module"],
            added_third_party=[],
            remote_work_dir="/tmp/work",
        )
        self.assertIn("--with-http_ssl_module", result)

    def test_with_third_party(self):
        third_party = [{"name": "echo", "module_path": "/tmp/work/nginx-modules/echo"}]
        result = build_install_configure_opts(
            prefix="/opt/app",
            added_modules=[],
            added_third_party=third_party,
            remote_work_dir="/tmp/work",
        )
        self.assertIn("--add-module=/tmp/work/nginx-modules/echo", result)


class TestDefaultInstallModules(TestCase):
    def test_contains_expected_modules(self):
        self.assertIn("--with-http_ssl_module", DEFAULT_INSTALL_MODULES)
        self.assertIn("--with-http_v2_module", DEFAULT_INSTALL_MODULES)
        self.assertIn("--with-http_realip_module", DEFAULT_INSTALL_MODULES)
        self.assertIn("--with-http_stub_status_module", DEFAULT_INSTALL_MODULES)
        self.assertIn("--with-stream", DEFAULT_INSTALL_MODULES)
        self.assertIn("--with-stream_ssl_module", DEFAULT_INSTALL_MODULES)
