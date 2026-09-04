"""settings 视图层测试（系统设置页面与 API）"""

import pytest
from django.urls import reverse

from apps.settings.models import SystemSetting


@pytest.mark.django_db
class TestSettingsIndexView:
    """系统设置页面"""

    def test_index_accessible(self, admin_client):
        resp = admin_client.get(reverse("settings:index"))
        assert resp.status_code == 200

    def test_index_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("settings:index"))
        assert resp.status_code == 302

    def test_index_contains_context(self, admin_client):
        resp = admin_client.get(reverse("settings:index"))
        assert resp.status_code == 200
        assert "group_list" in resp.context
        assert "active_group" in resp.context
        assert "can_update" in resp.context
        assert "total_count" in resp.context

    def test_index_with_group_param(self, admin_client):
        SystemSetting.objects.create(
            key="node.ssh_connect_timeout",
            value="10",
            type="integer",
            group="节点管理",
            label="SSH 连接超时（秒）",
            sort_order=11,
        )
        resp = admin_client.get(reverse("settings:index"), {"group": "节点管理"})
        assert resp.status_code == 200
        assert resp.context["active_group"] == "节点管理"


@pytest.mark.django_db
class TestSettingsSaveAPIView:
    """保存配置 API"""

    def test_save_without_group(self, admin_client):
        resp = admin_client.post(reverse("settings:save"), {})
        payload = resp.json()
        assert payload["success"] is False

    def test_save_settings_success(self, admin_client):
        SystemSetting.objects.create(
            key="node.ssh_connect_timeout",
            value="10",
            type="integer",
            group="节点管理",
            label="SSH 连接超时（秒）",
            sort_order=11,
        )
        resp = admin_client.post(
            reverse("settings:save"),
            {
                "group": "节点管理",
                "node.ssh_connect_timeout": "15",
            },
        )
        payload = resp.json()
        assert payload["success"] is True
        s = SystemSetting.objects.get(key="node.ssh_connect_timeout")
        assert s.value == "15"

    def test_save_empty_integer(self, admin_client):
        SystemSetting.objects.create(
            key="node.ssh_connect_timeout",
            value="10",
            type="integer",
            group="节点管理",
            label="SSH 连接超时（秒）",
            sort_order=11,
        )
        resp = admin_client.post(
            reverse("settings:save"),
            {
                "group": "节点管理",
                "node.ssh_connect_timeout": "",
            },
        )
        payload = resp.json()
        assert payload["success"] is False
        assert "不能为空" in payload["message"]

    def test_save_invalid_integer(self, admin_client):
        SystemSetting.objects.create(
            key="node.ssh_connect_timeout",
            value="10",
            type="integer",
            group="节点管理",
            label="SSH 连接超时（秒）",
            sort_order=11,
        )
        resp = admin_client.post(
            reverse("settings:save"),
            {
                "group": "节点管理",
                "node.ssh_connect_timeout": "abc",
            },
        )
        payload = resp.json()
        assert payload["success"] is False
        assert "必须是整数" in payload["message"]

    def test_save_boolean(self, admin_client):
        SystemSetting.objects.create(
            key="node.ssh_connect_timeout",
            value="10",
            type="integer",
            group="节点管理",
            label="SSH 连接超时（秒）",
            sort_order=11,
        )
        resp = admin_client.post(
            reverse("settings:save"),
            {
                "group": "节点管理",
                "node.ssh_connect_timeout": "25",
            },
        )
        payload = resp.json()
        assert payload["success"] is True
        s = SystemSetting.objects.get(key="node.ssh_connect_timeout")
        assert s.value == "25"


@pytest.mark.django_db
class TestSettingsGroupAPIView:
    """获取分组配置 API"""

    def test_group_api_returns_settings(self, admin_client):
        SystemSetting.objects.create(
            key="node.ssh_connect_timeout",
            value="10",
            type="integer",
            group="节点管理",
            label="SSH 连接超时",
            sort_order=11,
        )
        resp = admin_client.get(reverse("settings:api_group"), {"group": "节点管理"})
        payload = resp.json()
        assert payload["success"] is True
        assert len(payload["settings"]) == 1
        assert payload["settings"][0]["key"] == "node.ssh_connect_timeout"

    def test_group_api_empty(self, admin_client):
        resp = admin_client.get(reverse("settings:api_group"), {"group": "nonexistent"})
        payload = resp.json()
        assert payload["success"] is True
        assert payload["settings"] == []


@pytest.mark.django_db
class TestSettingsAllAPIView:
    """获取全部配置 API"""

    def test_all_api_returns_all(self, admin_client):
        SystemSetting.objects.create(
            key="node.ssh_connect_timeout",
            value="10",
            type="integer",
            group="节点管理",
            label="SSH 连接超时",
            sort_order=11,
        )
        SystemSetting.objects.create(
            key="dashboard.recent_tasks_count",
            value="20",
            type="integer",
            group="仪表盘",
            label="最近任务显示条数",
            sort_order=1,
        )
        resp = admin_client.get(reverse("settings:api_all"))
        payload = resp.json()
        assert payload["success"] is True
        assert payload["settings"]["node.ssh_connect_timeout"] == "10"
        assert payload["settings"]["dashboard.recent_tasks_count"] == "20"
