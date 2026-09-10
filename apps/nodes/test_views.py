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


@pytest.mark.django_db
class TestNodeListAPIView:
    """节点列表 API"""

    def test_api_accessible(self, admin_client, online_node):
        resp = admin_client.get(reverse("nodes:api_list"))
        payload = resp.json()
        assert "data" in payload

    def test_api_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("nodes:api_list"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestNodeExportView:
    """节点导出"""

    def test_export_accessible(self, admin_client):
        resp = admin_client.get(reverse("nodes:export"))
        assert resp.status_code == 200

    def test_export_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("nodes:export"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestNodeImportTemplateView:
    """节点导入模板下载"""

    def test_template_accessible(self, admin_client):
        resp = admin_client.get(reverse("nodes:import_template"))
        assert resp.status_code == 200

    def test_template_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("nodes:import_template"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestNodeImportAPIView:
    """节点批量导入 API"""

    def test_import_no_file(self, admin_client):
        resp = admin_client.post(reverse("nodes:import_api"))
        payload = resp.json()
        assert payload["success"] is False

    def test_import_anonymous(self, anonymous_client):
        resp = anonymous_client.post(reverse("nodes:import_api"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestNodeBatchDeleteView:
    """批量删除节点"""

    def test_batch_delete_no_ids(self, admin_client):
        import json

        resp = admin_client.post(
            reverse("nodes:batch_delete"),
            data=json.dumps({"node_ids": []}),
            content_type="application/json",
        )
        payload = resp.json()
        assert payload["success"] is False

    def test_batch_delete_anonymous(self, anonymous_client):
        import json

        resp = anonymous_client.post(
            reverse("nodes:batch_delete"),
            data=json.dumps({"node_ids": [1]}),
            content_type="application/json",
        )
        assert resp.status_code in (400, 403, 405)

    def test_batch_delete_no_permission(self, user_client):
        import json

        resp = user_client.post(
            reverse("nodes:batch_delete"),
            data=json.dumps({"node_ids": [1]}),
            content_type="application/json",
        )
        assert resp.status_code in (400, 403, 405)
        payload = resp.json()
        assert payload["success"] is False
        assert "无权限" in payload["message"]


@pytest.mark.django_db
class TestNodeLockView:
    """节点锁定/解锁"""

    def test_lock_no_ids(self, admin_client):
        import json

        resp = admin_client.post(
            reverse("nodes:lock"),
            data=json.dumps({"action": "lock", "node_ids": []}),
            content_type="application/json",
        )
        payload = resp.json()
        assert payload["success"] is False

    def test_lock_anonymous(self, anonymous_client):
        import json

        resp = anonymous_client.post(
            reverse("nodes:lock"),
            data=json.dumps({"action": "lock", "node_ids": [1]}),
            content_type="application/json",
        )
        assert resp.status_code in (302, 403)


@pytest.mark.django_db
class TestNodeConnectionTestView:
    """SSH 连接测试"""

    def test_test_requires_post(self, admin_client):
        resp = admin_client.get(reverse("nodes:test"))
        payload = resp.json()
        assert payload["success"] is False

    def test_test_anonymous(self, anonymous_client):
        import json

        resp = anonymous_client.post(
            reverse("nodes:test"),
            data=json.dumps({"node_id": 1}),
            content_type="application/json",
        )
        assert resp.status_code in (403,)


@pytest.mark.django_db
class TestNodeBatchTestView:
    """批量连接测试"""

    def test_batch_test_no_ids(self, admin_client):
        import json

        resp = admin_client.post(
            reverse("nodes:batch_test"),
            data=json.dumps({"node_ids": []}),
            content_type="application/json",
        )
        payload = resp.json()
        assert payload["success"] is False

    def test_batch_test_anonymous(self, anonymous_client):
        import json

        resp = anonymous_client.post(
            reverse("nodes:batch_test"),
            data=json.dumps({"node_ids": [1]}),
            content_type="application/json",
        )
        assert resp.status_code in (403,)


@pytest.mark.django_db
class TestNodeDetailView:
    """节点详情 API"""

    def test_detail_requires_post(self, admin_client):
        resp = admin_client.get(reverse("nodes:detail"))
        payload = resp.json()
        assert payload["success"] is False

    def test_detail_anonymous(self, anonymous_client):
        import json

        resp = anonymous_client.post(
            reverse("nodes:detail"),
            data=json.dumps({"node_id": 1}),
            content_type="application/json",
        )
        assert resp.status_code in (403,)


@pytest.mark.django_db
class TestNodeSystemInfoView:
    """系统信息查询"""

    def test_system_info_requires_post(self, admin_client):
        resp = admin_client.get(reverse("nodes:system-info"))
        payload = resp.json()
        assert payload["success"] is False

    def test_system_info_anonymous(self, anonymous_client):
        import json

        resp = anonymous_client.post(
            reverse("nodes:system-info"),
            data=json.dumps({"node_id": 1}),
            content_type="application/json",
        )
        assert resp.status_code in (302, 403)


@pytest.mark.django_db
class TestNodeNginxVersionView:
    """Nginx 版本查询"""

    def test_nginx_version_requires_post(self, admin_client):
        resp = admin_client.get(reverse("nodes:nginx-version"))
        payload = resp.json()
        assert payload["success"] is False

    def test_nginx_version_anonymous(self, anonymous_client):
        import json

        resp = anonymous_client.post(
            reverse("nodes:nginx-version"),
            data=json.dumps({"node_id": 1}),
            content_type="application/json",
        )
        assert resp.status_code in (403,)

    def test_nginx_version_no_permission(self, user_client):
        """已登录但无 nodes.read 权限"""
        import json

        resp = user_client.post(
            reverse("nodes:nginx-version"),
            data=json.dumps({"node_id": 1}),
            content_type="application/json",
        )
        assert resp.status_code == 403
        assert resp.json()["success"] is False

    def test_nginx_version_disabled_credential(
        self, admin_client, admin_user, credential
    ):
        """节点关联的凭证已被禁用"""
        import json

        credential.is_enabled = False
        credential.save()
        node = Node.objects.create(
            hostname="disabled-cred-node",
            ip="10.0.0.99",
            credential=credential,
            created_by=admin_user,
        )
        resp = admin_client.post(
            reverse("nodes:nginx-version"),
            data=json.dumps({"node_id": node.id}),
            content_type="application/json",
        )
        payload = resp.json()
        assert payload["success"] is False
        assert "禁用" in payload["message"]
