"""configs 视图层测试（配置管理与绑定）"""

import pytest
from django.urls import reverse

from apps.configs.models import Config, ConfigNodeBinding
from apps.nodes.models import Node


@pytest.mark.django_db
class TestConfigListView:
    """配置列表"""

    def test_list_accessible(self, admin_client):
        resp = admin_client.get(reverse("configs:list"))
        assert resp.status_code == 200

    def test_list_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("configs:list"))
        assert resp.status_code == 302

    def test_list_shows_configs(self, admin_client, admin_user, online_node):
        Config.objects.create(name="app.conf", created_by=admin_user)
        Config.objects.create(name="site.conf", created_by=admin_user)
        resp = admin_client.get(reverse("configs:list"))
        assert resp.status_code == 200
        assert "nodes" in resp.context


@pytest.mark.django_db
class TestConfigCreateView:
    """创建配置"""

    def test_create_page_accessible(self, admin_client):
        resp = admin_client.get(reverse("configs:create"))
        assert resp.status_code == 200

    def test_create_success(self, admin_client, admin_user):
        resp = admin_client.post(
            reverse("configs:create"),
            {
                "name": "new-config.conf",
                "default_remote_path": "/etc/nginx/conf.d/new-config.conf",
            },
        )
        assert resp.status_code == 302
        assert Config.objects.filter(name="new-config.conf").exists()


@pytest.mark.django_db
class TestConfigDetailView:
    """配置详情"""

    def test_detail_accessible(self, admin_client, admin_user):
        config = Config.objects.create(
            name="app.conf",
            created_by=admin_user,
        )
        resp = admin_client.get(reverse("configs:detail", args=[config.id]))
        assert resp.status_code == 200
        assert resp.context["config"] == config


@pytest.mark.django_db
class TestConfigEditView:
    """编辑配置"""

    def test_edit_page_accessible(self, admin_client, admin_user):
        config = Config.objects.create(
            name="app.conf",
            created_by=admin_user,
        )
        resp = admin_client.get(reverse("configs:edit", args=[config.id]))
        assert resp.status_code == 200

    def test_edit_success(self, admin_client, admin_user):
        config = Config.objects.create(
            name="app.conf",
            default_remote_path="/etc/nginx/conf.d/app.conf",
            created_by=admin_user,
        )
        resp = admin_client.post(
            reverse("configs:edit", args=[config.id]),
            {
                "name": "app-v2.conf",
                "default_remote_path": "/etc/nginx/conf.d/app-v2.conf",
            },
        )
        assert resp.status_code == 302
        config.refresh_from_db()
        assert config.name == "app-v2.conf"


@pytest.mark.django_db
class TestConfigDeleteView:
    """删除配置"""

    def test_delete_success(self, admin_client, admin_user):
        config = Config.objects.create(
            name="app.conf",
            created_by=admin_user,
        )
        resp = admin_client.post(reverse("configs:delete", args=[config.id]))
        assert resp.status_code == 302
        assert not Config.objects.filter(id=config.id).exists()


@pytest.mark.django_db
class TestConfigNodeDetailView:
    """节点维度的配置详情"""

    def test_node_detail_accessible(self, admin_client, admin_user, online_node):
        config = Config.objects.create(
            name="app.conf",
            created_by=admin_user,
        )
        ConfigNodeBinding.objects.create(
            config=config,
            node=online_node,
            remote_path="/etc/nginx/conf.d/app.conf",
            content="server { listen 80; }",
            created_by=admin_user,
        )
        resp = admin_client.get(reverse("configs:node_detail", args=[online_node.id]))
        assert resp.status_code == 200
        assert resp.context["node"] == online_node


@pytest.mark.django_db
class TestBindingCreateView:
    """创建绑定"""

    def test_create_page_accessible(self, admin_client, admin_user, online_node):
        Config.objects.create(name="app.conf", created_by=admin_user)
        resp = admin_client.get(reverse("configs:binding_create"))
        assert resp.status_code == 200

    def test_create_success(self, admin_client, admin_user, online_node):
        config = Config.objects.create(
            name="app.conf",
            default_remote_path="/etc/nginx/conf.d/app.conf",
            created_by=admin_user,
        )
        resp = admin_client.post(
            reverse("configs:binding_create"),
            {
                "config": config.id,
                "node": online_node.id,
                "remote_path": "/etc/nginx/conf.d/app.conf",
                "content": "server { listen 80; }",
            },
        )
        assert resp.status_code == 302
        assert ConfigNodeBinding.objects.filter(
            config=config, node=online_node
        ).exists()


@pytest.mark.django_db
class TestBindingDetailView:
    """绑定详情"""

    def test_detail_accessible(self, admin_client, admin_user, online_node):
        config = Config.objects.create(name="app.conf", created_by=admin_user)
        binding = ConfigNodeBinding.objects.create(
            config=config,
            node=online_node,
            remote_path="/etc/nginx/conf.d/app.conf",
            content="server { listen 80; }",
            created_by=admin_user,
        )
        resp = admin_client.get(reverse("configs:binding_detail", args=[binding.id]))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestBindingEditView:
    """编辑绑定"""

    def test_edit_page_accessible(self, admin_client, admin_user, online_node):
        config = Config.objects.create(name="app.conf", created_by=admin_user)
        binding = ConfigNodeBinding.objects.create(
            config=config,
            node=online_node,
            remote_path="/etc/nginx/conf.d/app.conf",
            content="server { listen 80; }",
            created_by=admin_user,
        )
        resp = admin_client.get(reverse("configs:binding_edit", args=[binding.id]))
        assert resp.status_code == 200

    def test_edit_success(self, admin_client, admin_user, online_node):
        config = Config.objects.create(name="app.conf", created_by=admin_user)
        binding = ConfigNodeBinding.objects.create(
            config=config,
            node=online_node,
            remote_path="/etc/nginx/conf.d/app.conf",
            content="server { listen 80; }",
            created_by=admin_user,
        )
        resp = admin_client.post(
            reverse("configs:binding_edit", args=[binding.id]),
            {
                "config": config.id,
                "node": online_node.id,
                "remote_path": "/etc/nginx/conf.d/app-v2.conf",
                "content": "server { listen 443 ssl; }",
                "remark": "测试修改",
                "confirm_save": "yes",
            },
        )
        assert resp.status_code == 302
        binding.refresh_from_db()
        assert binding.remote_path == "/etc/nginx/conf.d/app-v2.conf"


@pytest.mark.django_db
class TestBindingDeleteView:
    """删除绑定"""

    def test_delete_success(self, admin_client, admin_user, online_node):
        config = Config.objects.create(name="app.conf", created_by=admin_user)
        binding = ConfigNodeBinding.objects.create(
            config=config,
            node=online_node,
            remote_path="/etc/nginx/conf.d/app.conf",
            content="server { listen 80; }",
            created_by=admin_user,
        )
        resp = admin_client.post(reverse("configs:binding_delete", args=[binding.id]))
        assert resp.status_code == 302
        assert not ConfigNodeBinding.objects.filter(id=binding.id).exists()


@pytest.mark.django_db
class TestBindingVersionListView:
    """绑定版本历史"""

    def test_versions_accessible(self, admin_client, admin_user, online_node):
        config = Config.objects.create(name="app.conf", created_by=admin_user)
        binding = ConfigNodeBinding.objects.create(
            config=config,
            node=online_node,
            remote_path="/etc/nginx/conf.d/app.conf",
            content="server { listen 80; }",
            created_by=admin_user,
        )
        resp = admin_client.get(reverse("configs:binding_versions", args=[binding.id]))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestConfigByNodesAPIView:
    """按节点查配置 API"""

    def test_api_returns_configs(self, admin_client, admin_user, online_node):
        resp = admin_client.get(
            reverse("configs:api_by_nodes"), {"node_id": online_node.id}
        )
        payload = resp.json()
        assert "configs" in payload
