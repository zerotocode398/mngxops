"""nodes 视图层测试（节点 CRUD 与分组管理）"""

import pytest
from django.urls import reverse

from apps.nodes.models import Node, NodeGroup


@pytest.mark.django_db
class TestNodeListView:
    """节点列表"""

    def test_list_accessible(self, admin_client):
        resp = admin_client.get(reverse("nodes:list"))
        assert resp.status_code == 200

    def test_list_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("nodes:list"))
        assert resp.status_code == 302

    def test_list_shows_nodes(self, admin_client, online_node, offline_node):
        resp = admin_client.get(reverse("nodes:list"))
        assert resp.status_code == 200
        assert len(resp.context["nodes"]) >= 2


@pytest.mark.django_db
class TestNodeCreateView:
    """创建节点"""

    def test_create_page_accessible(self, admin_client):
        resp = admin_client.get(reverse("nodes:create"))
        assert resp.status_code == 200

    def test_create_success(self, admin_client, admin_user, credential):
        resp = admin_client.post(
            reverse("nodes:create"),
            {
                "hostname": "new-node-01",
                "ip": "10.0.0.200",
                "credential": credential.id,
                "port": "22",
                "environment": "prod",
            },
        )
        assert resp.status_code == 302
        assert Node.objects.filter(hostname="new-node-01").exists()


@pytest.mark.django_db
class TestNodeUpdateView:
    """编辑节点"""

    def test_edit_page_accessible(self, admin_client, online_node):
        resp = admin_client.get(reverse("nodes:edit", args=[online_node.id]))
        assert resp.status_code == 200

    def test_edit_success(self, admin_client, online_node):
        resp = admin_client.post(
            reverse("nodes:edit", args=[online_node.id]),
            {
                "hostname": "ngx-renamed-01",
                "ip": online_node.ip,
                "credential": online_node.credential.id,
                "port": "22",
                "environment": "prod",
            },
        )
        assert resp.status_code == 302
        online_node.refresh_from_db()
        assert online_node.hostname == "ngx-renamed-01"


@pytest.mark.django_db
class TestNodeDeleteView:
    """删除节点"""

    def test_delete_success(self, admin_client, online_node):
        resp = admin_client.post(reverse("nodes:delete", args=[online_node.id]))
        assert resp.status_code == 302
        assert not Node.objects.filter(id=online_node.id).exists()


@pytest.mark.django_db
class TestNodeGroupListView:
    """分组列表"""

    def test_group_list_accessible(self, admin_client):
        resp = admin_client.get(reverse("nodes:group_list"))
        assert resp.status_code == 200

    def test_group_list_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("nodes:group_list"))
        assert resp.status_code == 302

    def test_group_list_shows_groups(self, admin_client, admin_user):
        NodeGroup.objects.create(name="prod-core", created_by=admin_user)
        NodeGroup.objects.create(name="stage-api", created_by=admin_user)
        resp = admin_client.get(reverse("nodes:group_list"))
        assert resp.status_code == 200
        assert len(resp.context["node_groups"]) == 2


@pytest.mark.django_db
class TestNodeGroupCreateView:
    """创建分组"""

    def test_create_page_accessible(self, admin_client):
        resp = admin_client.get(reverse("nodes:group_create"))
        assert resp.status_code == 200

    def test_create_success(self, admin_client):
        resp = admin_client.post(
            reverse("nodes:group_create"),
            {
                "name": "new-group",
            },
        )
        assert resp.status_code == 302
        assert NodeGroup.objects.filter(name="new-group").exists()


@pytest.mark.django_db
class TestNodeGroupUpdateView:
    """编辑分组"""

    def test_edit_page_accessible(self, admin_client, admin_user):
        group = NodeGroup.objects.create(name="prod-core", created_by=admin_user)
        resp = admin_client.get(reverse("nodes:group_edit", args=[group.id]))
        assert resp.status_code == 200

    def test_edit_success(self, admin_client, admin_user):
        group = NodeGroup.objects.create(name="prod-core", created_by=admin_user)
        resp = admin_client.post(
            reverse("nodes:group_edit", args=[group.id]),
            {
                "name": "prod-core-v2",
            },
        )
        assert resp.status_code == 302
        group.refresh_from_db()
        assert group.name == "prod-core-v2"


@pytest.mark.django_db
class TestNodeGroupDeleteView:
    """删除分组"""

    def test_delete_success(self, admin_client, admin_user):
        group = NodeGroup.objects.create(name="prod-core", created_by=admin_user)
        resp = admin_client.post(reverse("nodes:group_delete", args=[group.id]))
        assert resp.status_code == 302
        assert not NodeGroup.objects.filter(id=group.id).exists()


@pytest.mark.django_db
class TestNodeGroupManageNodesView:
    """管理分组节点"""

    def test_manage_nodes_page_accessible(self, admin_client, admin_user, online_node):
        group = NodeGroup.objects.create(name="prod-core", created_by=admin_user)
        resp = admin_client.get(reverse("nodes:group_manage_nodes", args=[group.id]))
        assert resp.status_code in (200, 405)

    def test_add_node_to_group(self, admin_client, admin_user, online_node):
        group = NodeGroup.objects.create(name="prod-core", created_by=admin_user)
        resp = admin_client.post(
            reverse("nodes:group_manage_nodes", args=[group.id]),
            {"node_ids": [online_node.id]},
        )
        assert resp.status_code == 302
        assert online_node in group.nodes.all()


@pytest.mark.django_db
class TestNodeSearchAPIView:
    """节点搜索 API"""

    def test_search_api_accessible(self, admin_client, online_node):
        resp = admin_client.get(
            reverse("nodes:api_search_nodes"), {"search": online_node.hostname}
        )
        payload = resp.json()
        assert payload["success"] is True
        assert len(payload["nodes"]) >= 1

    def test_search_api_no_match(self, admin_client):
        resp = admin_client.get(
            reverse("nodes:api_search_nodes"), {"search": "nonexistent-xyz"}
        )
        payload = resp.json()
        assert payload["success"] is True
        assert len(payload["nodes"]) == 0


@pytest.mark.django_db
class TestNodeGroupListAPIView:
    """分组列表 API"""

    def test_api_returns_groups(self, admin_client, admin_user):
        NodeGroup.objects.create(name="prod-core", created_by=admin_user)
        NodeGroup.objects.create(name="stage-api", created_by=admin_user)
        resp = admin_client.get(reverse("nodes:api_groups"))
        payload = resp.json()
        assert payload["success"] is True
        assert len(payload["data"]) >= 2
