"""releases 视图层测试（发布中心与任务管理）"""

import pytest
from django.urls import reverse

from apps.releases.models import TaskCenterTask


@pytest.mark.django_db
class TestReleaseListView:
    """发布历史列表"""

    def test_list_accessible(self, admin_client):
        resp = admin_client.get(reverse("releases:list"))
        assert resp.status_code == 200

    def test_list_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("releases:list"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestTaskCenterListView:
    """任务中心列表"""

    def test_history_accessible(self, admin_client):
        resp = admin_client.get(reverse("releases:history"))
        assert resp.status_code == 200

    def test_history_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("releases:history"))
        assert resp.status_code in (302, 403)

    def test_history_shows_tasks(self, admin_client, admin_user):
        TaskCenterTask.objects.create(
            operation_type="node_batch_test",
            status="pending",
            detail="测试任务",
            target_hostnames="node-a",
            target_ips="10.0.0.1",
            source_batch="release-250101-0001",
            trigger_user=admin_user,
        )
        resp = admin_client.get(reverse("releases:history"))
        assert resp.status_code == 200
        assert len(resp.context["tasks"]) == 1


@pytest.mark.django_db
class TestTaskCenterDetailView:
    """任务中心详情"""

    def test_detail_accessible(self, admin_client, admin_user):
        task = TaskCenterTask.objects.create(
            operation_type="node_batch_test",
            status="pending",
            detail="测试任务",
            target_hostnames="node-a",
            target_ips="10.0.0.1",
            source_batch="release-250101-0001",
            trigger_user=admin_user,
        )
        resp = admin_client.get(reverse("releases:task_center_detail", args=[task.id]))
        assert resp.status_code == 200

    def test_detail_not_found(self, admin_client):
        resp = admin_client.get(reverse("releases:task_center_detail", args=[99999]))
        assert resp.status_code == 404


@pytest.mark.django_db
class TestTaskCenterCancelView:
    """任务取消"""

    def test_cancel_non_pending_task(self, admin_client, admin_user):
        task = TaskCenterTask.objects.create(
            operation_type="node_batch_test",
            status="success",
            detail="已完成",
            target_hostnames="node-a",
            target_ips="10.0.0.1",
            source_batch="release-250101-0001",
            trigger_user=admin_user,
        )
        resp = admin_client.post(reverse("releases:task_center_cancel", args=[task.id]))
        payload = resp.json()
        assert payload["success"] is False

    def test_cancel_pending_task(self, admin_client, admin_user):
        task = TaskCenterTask.objects.create(
            operation_type="node_batch_test",
            status="pending",
            detail="待执行",
            target_hostnames="node-a",
            target_ips="10.0.0.1",
            source_batch="release-250101-0001",
            trigger_user=admin_user,
        )
        resp = admin_client.post(reverse("releases:task_center_cancel", args=[task.id]))
        payload = resp.json()
        assert payload["success"] is True
        task.refresh_from_db()
        assert task.status == "cancelled"


@pytest.mark.django_db
class TestTaskCenterProgressAPIView:
    """任务进度 API"""

    def test_progress_api_accessible(self, admin_client, admin_user):
        task = TaskCenterTask.objects.create(
            operation_type="node_batch_test",
            status="pending",
            detail="测试",
            target_hostnames="node-a",
            target_ips="10.0.0.1",
            source_batch="release-250101-0001",
            trigger_user=admin_user,
        )
        resp = admin_client.get(
            reverse("releases:task_center_progress"), {"ids": str(task.id)}
        )
        payload = resp.json()
        assert payload["success"] is True
        assert len(payload["tasks"]) == 1


@pytest.mark.django_db
class TestReleaseCenterView:
    """发布中心"""

    def test_center_accessible(self, admin_client):
        resp = admin_client.get(reverse("releases:center"))
        assert resp.status_code == 200

    def test_center_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("releases:center"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestReleaseNodeListAPIView:
    """发布节点列表 API"""

    def test_api_accessible(self, admin_client, online_node):
        resp = admin_client.get(reverse("releases:api_nodes"))
        payload = resp.json()
        assert payload["success"] is True
        assert len(payload["nodes"]) >= 1

    def test_api_search(self, admin_client, online_node, offline_node):
        resp = admin_client.get(
            reverse("releases:api_nodes"), {"search": online_node.hostname}
        )
        payload = resp.json()
        assert payload["success"] is True
        names = [n["hostname"] for n in payload["nodes"]]
        assert online_node.hostname in names
        assert offline_node.hostname not in names
