from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from unittest.mock import patch

from utils.ssh import discover_nginx_configs
from apps.nodes.models import Node
from apps.configs.models import Config, ConfigNodeBinding


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


class ConfigListTagSearchTests(TestCase):
	def setUp(self):
		user_model = get_user_model()
		self.user = user_model.objects.create_superuser(
			username="admin", email="admin@example.com", password="pass1234"
		)
		self.client.force_login(self.user)

		self.node_a = Node.objects.create(
			hostname="web-prod-1", ip="10.0.0.11", created_by=self.user
		)
		self.node_b = Node.objects.create(
			hostname="api-stage-1", ip="10.0.0.21", created_by=self.user
		)

		self.config = Config.objects.create(
			name="nginx.conf", created_by=self.user
		)
		ConfigNodeBinding.objects.create(
			config=self.config,
			node=self.node_a,
			remote_path="/etc/nginx/nginx.conf",
			content="worker_processes 1;",
		)

	def _node_ids(self, response):
		return {node.id for node in response.context["nodes"]}

	def test_search_multi_tags_use_and_logic(self):
		response = self.client.get(
			reverse("configs:list"), {"search": "web-prod-1,10.0.0.11"}
		)
		self.assertEqual(response.status_code, 200)
		self.assertSetEqual(self._node_ids(response), {self.node_a.id})

	def test_search_multi_tags_unmatched_filters_out(self):
		response = self.client.get(
			reverse("configs:list"), {"search": "web-prod-1,10.0.0.99"}
		)
		self.assertEqual(response.status_code, 200)
		self.assertSetEqual(self._node_ids(response), set())

	def test_search_matches_config_name(self):
		response = self.client.get(reverse("configs:list"), {"search": "nginx.conf"})
		self.assertEqual(response.status_code, 200)
		self.assertSetEqual(self._node_ids(response), {self.node_a.id})

	def test_search_supports_full_width_comma(self):
		response = self.client.get(
			reverse("configs:list"), {"search": "web-prod-1，10.0.0.99"}
		)
		self.assertEqual(response.status_code, 200)
		self.assertSetEqual(self._node_ids(response), set())

