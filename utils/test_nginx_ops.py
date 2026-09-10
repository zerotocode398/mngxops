"""Nginx 启停管理工具测试。"""

from unittest.mock import patch, MagicMock

from django.test import TestCase

from utils.nginx_ops import (
    build_nginx_unit_content,
    _systemd_cmd,
    SYSTEMD_UNIT_DIR,
    UNIT_FILE_PATH,
    DEFAULT_UNIT_NAME,
    _DEFAULT_UNITS,
)


class TestSystemdCmd(TestCase):
    def test_no_sudo(self):
        self.assertEqual(_systemd_cmd("systemctl start nginx"), "systemctl start nginx")

    def test_with_sudo(self):
        self.assertEqual(
            _systemd_cmd("systemctl start nginx", use_sudo=True),
            "sudo -n systemctl start nginx",
        )

    def test_empty(self):
        self.assertEqual(_systemd_cmd(""), "")
        self.assertEqual(_systemd_cmd("  "), "")

    def test_none(self):
        self.assertEqual(_systemd_cmd(None), "")


class TestBuildNginxUnitContent(TestCase):
    def test_basic_content(self):
        content = build_nginx_unit_content("/usr/local/nginx/sbin/nginx")
        self.assertIn("[Unit]", content)
        self.assertIn("Description=nginx (managed by mngxops)", content)
        self.assertIn("[Service]", content)
        self.assertIn("Type=forking", content)
        self.assertIn("ExecStart=/usr/local/nginx/sbin/nginx", content)
        self.assertIn("ExecReload=/usr/local/nginx/sbin/nginx -s reload", content)
        self.assertIn("ExecStop=/usr/local/nginx/sbin/nginx -s quit", content)
        self.assertIn("[Install]", content)
        self.assertIn("WantedBy=multi-user.target", content)

    def test_with_user_and_group(self):
        content = build_nginx_unit_content(
            "/usr/local/nginx/sbin/nginx", user="nginx", group="nginx"
        )
        self.assertIn("User=nginx", content)
        self.assertIn("Group=nginx", content)

    def test_without_user_and_group(self):
        content = build_nginx_unit_content("/usr/local/nginx/sbin/nginx")
        self.assertNotIn("User=", content)
        self.assertNotIn("Group=", content)

    def test_empty_bin(self):
        content = build_nginx_unit_content("")
        self.assertIn("ExecStart=nginx", content)

    def test_none_bin(self):
        content = build_nginx_unit_content(None)
        self.assertIn("ExecStart=nginx", content)


class TestConstants(TestCase):
    def test_systemd_unit_dir(self):
        self.assertEqual(SYSTEMD_UNIT_DIR, "/etc/systemd/system")

    def test_unit_file_path(self):
        self.assertEqual(UNIT_FILE_PATH, "/etc/systemd/system/nginx.service")

    def test_default_unit_name(self):
        self.assertEqual(DEFAULT_UNIT_NAME, "nginx")

    def test_default_units(self):
        self.assertIn("nginx", _DEFAULT_UNITS)
        self.assertIn("nginx.service", _DEFAULT_UNITS)


class TestUnitCandidates(TestCase):
    def test_default(self):
        from utils.nginx_ops import _unit_candidates

        candidates = _unit_candidates()
        self.assertIn("nginx", candidates)
        self.assertEqual(len(candidates), len(set(candidates)))

    def test_custom(self):
        from utils.nginx_ops import _unit_candidates

        candidates = _unit_candidates("myservice")
        self.assertIn("myservice", candidates)
        self.assertEqual(candidates[0], "myservice")

    def test_custom_with_service_suffix(self):
        from utils.nginx_ops import _unit_candidates

        candidates = _unit_candidates("myservice.service")
        self.assertIn("myservice", candidates)
        self.assertNotIn("myservice.service", candidates)


class TestAuthKwargs(TestCase):
    def test_password_only(self):
        from utils.nginx_ops import _auth_kwargs

        kwargs = _auth_kwargs(password="secret")
        self.assertEqual(kwargs["password"], "secret")
        self.assertNotIn("private_key", kwargs)

    def test_private_key_only(self):
        from utils.nginx_ops import _auth_kwargs

        kwargs = _auth_kwargs(private_key="key-data")
        self.assertEqual(kwargs["private_key"], "key-data")
        self.assertNotIn("password", kwargs)

    def test_both(self):
        from utils.nginx_ops import _auth_kwargs

        kwargs = _auth_kwargs(password="secret", private_key="key-data")
        self.assertEqual(kwargs["password"], "secret")
        self.assertEqual(kwargs["private_key"], "key-data")

    def test_empty(self):
        from utils.nginx_ops import _auth_kwargs

        kwargs = _auth_kwargs()
        self.assertEqual(kwargs, {})
