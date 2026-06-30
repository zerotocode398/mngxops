from django.test import SimpleTestCase
from unittest.mock import patch

from utils.ssh import discover_nginx_configs


class DiscoverNginxConfigsTests(SimpleTestCase):
	def test_nested_include_is_recursively_discovered(self):
		file_map = {
			"/etc/nginx/nginx.conf": "include /etc/nginx/conf.d/main.conf;",
			"/etc/nginx/conf.d/main.conf": "include /etc/nginx/conf.d/extra/*.conf;",
			"/etc/nginx/conf.d/extra/app.conf": "server { listen 80; }",
		}

		def fake_read(*args, **kwargs):
			path = args[-1]
			if path in file_map:
				return True, file_map[path]
			return False, "not found"

		def fake_glob(*args, **kwargs):
			pattern = args[-1]
			if pattern == "/etc/nginx/conf.d/extra/*.conf":
				return ["/etc/nginx/conf.d/extra/app.conf"]
			return []

		with patch("utils.ssh.read_remote_file", side_effect=fake_read), patch(
			"utils.ssh.expand_remote_glob", side_effect=fake_glob
		):
			results, errors = discover_nginx_configs(
				host="127.0.0.1",
				port=22,
				username="tester",
				password="pwd",
				nginx_conf_path="/etc/nginx/nginx.conf",
			)

		self.assertEqual(errors, [])
		self.assertEqual(
			{item["path"] for item in results},
			{
				"/etc/nginx/nginx.conf",
				"/etc/nginx/conf.d/main.conf",
				"/etc/nginx/conf.d/extra/app.conf",
			},
		)

	def test_quoted_relative_include_is_normalized(self):
		file_map = {
			"/etc/nginx/nginx.conf": "include '/etc/nginx/sites-enabled/site.conf';",
			"/etc/nginx/sites-enabled/site.conf": 'include "../conf.d/app.conf";',
			"/etc/nginx/conf.d/app.conf": "server { listen 443 ssl; }",
		}

		def fake_read(*args, **kwargs):
			path = args[-1]
			if path in file_map:
				return True, file_map[path]
			return False, "not found"

		with patch("utils.ssh.read_remote_file", side_effect=fake_read), patch(
			"utils.ssh.expand_remote_glob", return_value=[]
		):
			results, errors = discover_nginx_configs(
				host="127.0.0.1",
				port=22,
				username="tester",
				password="pwd",
				nginx_conf_path="/etc/nginx/nginx.conf",
			)

		self.assertEqual(errors, [])
		self.assertIn("/etc/nginx/conf.d/app.conf", {item["path"] for item in results})

	def test_include_depth_limit_adds_error(self):
		file_map = {
			"/etc/nginx/nginx.conf": "include /etc/nginx/lv1.conf;",
			"/etc/nginx/lv1.conf": "include /etc/nginx/lv2.conf;",
			"/etc/nginx/lv2.conf": "include /etc/nginx/lv3.conf;",
			"/etc/nginx/lv3.conf": "server { listen 8080; }",
		}

		def fake_read(*args, **kwargs):
			path = args[-1]
			if path in file_map:
				return True, file_map[path]
			return False, "not found"

		with patch("utils.ssh.read_remote_file", side_effect=fake_read), patch(
			"utils.ssh.expand_remote_glob", return_value=[]
		):
			results, errors = discover_nginx_configs(
				host="127.0.0.1",
				port=22,
				username="tester",
				password="pwd",
				nginx_conf_path="/etc/nginx/nginx.conf",
				max_include_depth=2,
			)

		self.assertEqual(
			{item["path"] for item in results},
			{
				"/etc/nginx/nginx.conf",
				"/etc/nginx/lv1.conf",
				"/etc/nginx/lv2.conf",
			},
		)
		self.assertTrue(any("include 递归超限" in e for e in errors))

