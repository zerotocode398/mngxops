"""dashboard 视图层测试（首页）"""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestDashboardIndex:
    """首页仪表盘"""

    def test_index_accessible(self, admin_client):
        resp = admin_client.get(reverse("dashboard:index"))
        assert resp.status_code == 200

    def test_index_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("dashboard:index"))
        assert resp.status_code == 302

    def test_index_contains_stats(self, admin_client):
        resp = admin_client.get(reverse("dashboard:index"))
        assert resp.status_code == 200
        assert "node_count" in resp.context
        assert "online_count" in resp.context
        assert "offline_count" in resp.context
        assert "pending_push_count" in resp.context
        assert "running_count" in resp.context
        assert "failed_7d_count" in resp.context

    def test_index_shows_recent_tasks(self, admin_client):
        resp = admin_client.get(reverse("dashboard:index"))
        assert resp.status_code == 200
        assert "recent_tasks" in resp.context


@pytest.mark.django_db
class TestDashboardStatsAPI:
    """统计卡片轮询 API"""

    def test_stats_api_accessible(self, admin_client):
        resp = admin_client.get(reverse("dashboard:stats_api"))
        assert resp.status_code == 200
        payload = resp.json()
        assert "node_count" in payload
        assert "online_count" in payload
        assert "offline_count" in payload

    def test_stats_api_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("dashboard:stats_api"))
        assert resp.status_code == 302
