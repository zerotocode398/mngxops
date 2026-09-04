"""audit 视图层测试（审计日志 / 登录日志）"""

import pytest
from django.urls import reverse

from apps.audit.models import AuditLog, LoginLog


@pytest.mark.django_db
class TestAuditLogListView:
    """审计日志列表"""

    def test_list_accessible(self, admin_client):
        resp = admin_client.get(reverse("audit:list"))
        assert resp.status_code == 200

    def test_list_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("audit:list"))
        assert resp.status_code == 302

    def test_list_shows_audit_log(self, admin_client, admin_user):
        AuditLog.objects.create(
            user=admin_user,
            module="配置管理",
            action="创建配置",
            ip="127.0.0.1",
            result="success",
            detail="test log",
        )
        resp = admin_client.get(reverse("audit:list"))
        assert resp.status_code == 200
        assert "test log" in resp.content.decode()

    def test_list_empty(self, admin_client):
        resp = admin_client.get(reverse("audit:list"))
        assert resp.status_code == 200

    def test_list_search(self, admin_client, admin_user):
        AuditLog.objects.create(
            user=admin_user,
            module="配置管理",
            action="创建配置",
            ip="127.0.0.1",
            result="success",
            detail="search_me",
        )
        AuditLog.objects.create(
            user=admin_user,
            module="节点管理",
            action="测试连接",
            ip="127.0.0.1",
            result="success",
            detail="skip_me",
        )
        resp = admin_client.get(reverse("audit:list"), {"search": "search_me"})
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "search_me" in content
        assert "skip_me" not in content


@pytest.mark.django_db
class TestLoginLogListView:
    """登录日志列表"""

    def test_login_log_list_accessible(self, admin_client):
        resp = admin_client.get(reverse("audit:login_list"))
        assert resp.status_code == 200

    def test_login_log_list_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("audit:login_list"))
        assert resp.status_code == 302

    def test_login_log_shows_records(self, admin_client):
        LoginLog.objects.create(
            username="admin",
            ip="127.0.0.1",
            user_agent="test",
            status="success",
        )
        resp = admin_client.get(reverse("audit:login_list"))
        assert resp.status_code == 200
        assert "admin" in resp.content.decode()
