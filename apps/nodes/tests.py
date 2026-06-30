from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
import json
from unittest.mock import patch

from .models import Node, NodeGroup
from apps.releases.models import TaskCenterTask


class NodeListTagSearchTests(TestCase):
	def setUp(self):
		user_model = get_user_model()
		self.user = user_model.objects.create_superuser(
			username="admin",
			email="admin@example.com",
			password="pass1234",
		)
		self.client.force_login(self.user)

		self.group_prod = NodeGroup.objects.create(
			name="prod-core",
			created_by=self.user,
		)
		self.group_stage = NodeGroup.objects.create(
			name="stage-api",
			created_by=self.user,
		)

		self.node_a = Node.objects.create(
			hostname="web-prod-1",
			ip="10.0.0.11",
			created_by=self.user,
		)
		self.node_b = Node.objects.create(
			hostname="api-stage-1",
			ip="10.0.0.21",
			created_by=self.user,
		)
		self.node_c = Node.objects.create(
			hostname="misc-1",
			ip="10.0.0.99",
			created_by=self.user,
		)

		self.node_a.groups.add(self.group_prod)
		self.node_b.groups.add(self.group_stage)

	def _node_ids(self, response):
		return {node.id for node in response.context["nodes"]}

	def test_group_search_matches_group_name(self):
		response = self.client.get(reverse("nodes:list"), {"group_search": "prod"})

		self.assertEqual(response.status_code, 200)
		self.assertSetEqual(self._node_ids(response), {self.node_a.id})

	def test_group_search_also_matches_hostname_or_ip(self):
		response = self.client.get(reverse("nodes:list"), {"group_search": "stage-1"})

		self.assertEqual(response.status_code, 200)
		self.assertSetEqual(self._node_ids(response), {self.node_b.id})

	def test_group_search_requires_all_tags_to_match(self):
		response = self.client.get(reverse("nodes:list"), {"group_search": "stage-1,10.0.0.99"})

		self.assertEqual(response.status_code, 200)
		self.assertSetEqual(self._node_ids(response), set())

	def test_group_search_supports_full_width_comma(self):
		response = self.client.get(reverse("nodes:list"), {"group_search": "prod，stage"})

		self.assertEqual(response.status_code, 200)
		self.assertSetEqual(self._node_ids(response), set())

	def test_api_group_search_matches_group_name(self):
		response = self.client.get(reverse("nodes:api_list"), {"group_search": "prod"})

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(payload.get("success"))
		ids = {item["id"] for item in payload.get("data", [])}
		self.assertSetEqual(ids, {self.node_a.id})

	def test_api_group_search_requires_all_tags(self):
		response = self.client.get(
			reverse("nodes:api_list"), {"group_search": "prod,stage"}
		)

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(payload.get("success"))
		ids = {item["id"] for item in payload.get("data", [])}
		self.assertSetEqual(ids, set())

	def test_api_group_search_also_matches_hostname_or_ip(self):
		response = self.client.get(
			reverse("nodes:api_list"), {"group_search": "stage-1"}
		)

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(payload.get("success"))
		ids = {item["id"] for item in payload.get("data", [])}
		self.assertSetEqual(ids, {self.node_b.id})

	def test_batch_test_creates_async_task_center_record(self):
		self.node_a.is_locked = True
		self.node_a.save(update_fields=["is_locked"])
		self.node_b.is_locked = True
		self.node_b.save(update_fields=["is_locked"])

		with patch("apps.nodes.views.threading.Thread.start"):
			response = self.client.post(
				reverse("nodes:batch_test"),
				data=json.dumps({"node_ids": [self.node_a.id, self.node_b.id]}),
				content_type="application/json",
			)

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(payload.get("success"))
		self.assertTrue(payload.get("async"))
		self.assertTrue(payload.get("task_center_id"))

		task = TaskCenterTask.objects.get(pk=payload["task_center_id"])
		self.assertEqual(task.operation_type, "node_batch_test")

