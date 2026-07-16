import json
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.configs.models import Config, ConfigNodeBinding, BindingVersion
from apps.nodes.models import Node
from apps.releases.models import TaskCenterTask
from apps.releases.models import ReleaseTask
from apps.users.models import PermissionItem, UserProfile


class TaskCenterScopedAccessTests(TestCase):
	def setUp(self):
		user_model = get_user_model()
		self.node_user = user_model.objects.create_user(
			username="node-operator",
			email="node@example.com",
			password="pass1234",
		)
		self.other_user = user_model.objects.create_user(
			username="other-user",
			email="other@example.com",
			password="pass1234",
		)

		nodes_update_perm, _ = PermissionItem.objects.get_or_create(
			code="nodes.update",
			defaults={
				"name": "节点-编辑",
				"resource": "nodes",
				"action": "update",
			},
		)
		profile, _ = UserProfile.objects.get_or_create(user=self.node_user)
		profile.direct_permissions.add(nodes_update_perm)

		self.own_node_task = TaskCenterTask.objects.create(
			operation_type="node_batch_test",
			status="pending",
			detail="任务已创建",
			target_hostnames="node-a,node-b",
			target_ips="10.10.0.11,10.10.0.12",
			source_batch="release-250101-0001",
			trigger_user=self.node_user,
		)
		self.other_node_task = TaskCenterTask.objects.create(
			operation_type="node_batch_test",
			status="pending",
			detail="任务已创建",
			target_hostnames="node-c",
			target_ips="10.10.0.13",
			source_batch="release-250101-0002",
			trigger_user=self.other_user,
		)
		self.release_task = TaskCenterTask.objects.create(
			operation_type="release_publish",
			status="pending",
			detail="待执行任务 1 个",
			target_hostnames="release-node",
			target_ips="10.10.0.14",
			source_batch="release-250101-0003",
			trigger_user=self.other_user,
		)

		self.client.force_login(self.node_user)

	def test_node_operator_can_view_only_own_node_batch_tasks(self):
		response = self.client.get(reverse("releases:history"))

		self.assertEqual(response.status_code, 200)
		tasks = list(response.context["tasks"])
		self.assertEqual(len(tasks), 1)
		self.assertEqual(tasks[0].id, self.own_node_task.id)

	def test_node_operator_can_open_own_task_detail_only(self):
		own_response = self.client.get(
			reverse("releases:task_center_detail", args=[self.own_node_task.id])
		)
		self.assertEqual(own_response.status_code, 200)

		release_response = self.client.get(
			reverse("releases:task_center_detail", args=[self.release_task.id])
		)
		self.assertEqual(release_response.status_code, 404)

	def test_progress_api_returns_only_authorized_tasks(self):
		response = self.client.get(
			reverse("releases:task_center_progress"),
			{
				"ids": f"{self.own_node_task.id},{self.other_node_task.id},{self.release_task.id}"
			},
		)

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(payload["success"])
		self.assertEqual([t["id"] for t in payload["tasks"]], [self.own_node_task.id])


class ReleaseCreateJSONAPITests(TestCase):
    """测试 JSON API 发布任务创建"""
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="pass1234",
        )
        self.client.force_login(self.user)

        self.node_a = Node.objects.create(
            hostname="node-a",
            ip="10.10.0.11",
            created_by=self.user,
        )
        self.node_b = Node.objects.create(
            hostname="node-b",
            ip="10.10.0.12",
            created_by=self.user,
        )

        self.config_a = Config.objects.create(
            name="site-a.conf",
            default_remote_path="/etc/nginx/conf.d/site-a.conf",
            created_by=self.user,
        )
        self.config_b = Config.objects.create(
            name="site-b.conf",
            default_remote_path="/etc/nginx/conf.d/site-b.conf",
            created_by=self.user,
        )

        self.binding_a = ConfigNodeBinding.objects.create(
            config=self.config_a,
            node=self.node_a,
            remote_path="/etc/nginx/conf.d/site-a.conf",
            content="server { listen 80; }",
            current_version=1,
            created_by=self.user,
        )
        self.binding_b = ConfigNodeBinding.objects.create(
            config=self.config_b,
            node=self.node_b,
            remote_path="/etc/nginx/conf.d/site-b.conf",
            content="server { listen 8080; }",
            current_version=1,
            created_by=self.user,
        )

        BindingVersion.objects.create(
            binding=self.binding_a,
            version=1,
            content=self.binding_a.content,
            created_by=self.user,
        )
        BindingVersion.objects.create(
            binding=self.binding_b,
            version=1,
            content=self.binding_b.content,
            created_by=self.user,
        )

    def test_create_release_tasks_json_api(self):
        response = self.client.post(
            reverse("releases:api_create"),
            data=json.dumps({
                "bindings": [
                    {"binding_id": self.binding_a.id, "version": 1},
                    {"binding_id": self.binding_b.id, "version": 1},
                ],
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        resp = json.loads(response.content)
        self.assertTrue(resp["success"])
        self.assertEqual(resp["task_count"], 2)

        tasks = ReleaseTask.objects.order_by("id")
        self.assertEqual(tasks.count(), 2)
        self.assertEqual(tasks[0].node_id, self.node_a.id)
        self.assertEqual(tasks[0].config_id, self.config_a.id)
        self.assertEqual(tasks[1].node_id, self.node_b.id)
        self.assertEqual(tasks[1].config_id, self.config_b.id)
        self.assertEqual(tasks[0].batch_number, tasks[1].batch_number)

    def test_create_release_tasks_json_api_empty_bindings(self):
        response = self.client.post(
            reverse("releases:api_create"),
            data=json.dumps({"bindings": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        resp = json.loads(response.content)
        self.assertFalse(resp["success"])


class TaskCenterTagSearchTests(TestCase):
	def setUp(self):
		user_model = get_user_model()
		self.user = user_model.objects.create_superuser(
			username="search-admin",
			email="search-admin@example.com",
			password="pass1234",
		)
		self.client.force_login(self.user)

		TaskCenterTask.objects.create(
			operation_type="release_publish",
			status="pending",
			detail="任务1",
			target_hostnames="web-a",
			target_ips="10.0.0.1",
			source_batch="release-260629-0001",
			trigger_user=self.user,
		)
		TaskCenterTask.objects.create(
			operation_type="release_publish",
			status="pending",
			detail="任务2",
			target_hostnames="web-b",
			target_ips="10.0.0.2",
			source_batch="release-260629-0002",
			trigger_user=self.user,
		)

	def test_multi_tags_use_and_logic(self):
		response = self.client.get(
			reverse("releases:history"),
			{"search": "web-a,release-260629-0001"},
		)
		self.assertEqual(response.status_code, 200)
		tasks = list(response.context["tasks"])
		self.assertEqual(len(tasks), 1)
		self.assertEqual(tasks[0].target_hostnames, "web-a")

	def test_unmatched_tag_filters_out_results(self):
		response = self.client.get(
			reverse("releases:history"),
			{"search": "web-a,release-260629-9999"},
		)
		self.assertEqual(response.status_code, 200)
		tasks = list(response.context["tasks"])
		self.assertEqual(len(tasks), 0)


