"""accounts 视图层测试（登录/登出/个人中心/密码修改）"""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestLoginView:
    """登录页面与登录行为"""

    def test_login_page_accessible(self, anonymous_client):
        resp = anonymous_client.get(reverse("accounts:login"))
        assert resp.status_code == 200
        assert "form" in resp.context

    def test_login_page_redirects_authenticated(self, admin_client):
        resp = admin_client.get(reverse("accounts:login"))
        assert resp.status_code == 302

    def test_login_with_valid_credentials(self, anonymous_client, admin_user):
        resp = anonymous_client.post(
            reverse("accounts:login"),
            {"username": "admin", "password": "pass1234"},
        )
        assert resp.status_code == 302

    def test_login_with_wrong_password(self, anonymous_client, admin_user):
        resp = anonymous_client.post(
            reverse("accounts:login"),
            {"username": "admin", "password": "wrong"},
        )
        assert resp.status_code == 200

    def test_login_with_nonexistent_user(self, anonymous_client):
        resp = anonymous_client.post(
            reverse("accounts:login"),
            {"username": "nobody", "password": "pass1234"},
        )
        assert resp.status_code == 200

    def test_login_locked_user_rejected(self, anonymous_client, normal_user):
        from apps.accounts.login_lock import get_or_create_profile
        from django.utils import timezone
        from datetime import timedelta

        profile = get_or_create_profile(normal_user)
        profile.login_locked_until = timezone.now() + timedelta(minutes=15)
        profile.failed_login_count = 5
        profile.save()

        resp = anonymous_client.post(
            reverse("accounts:login"),
            {"username": "viewer", "password": "pass1234"},
        )
        assert resp.status_code == 200

    def test_login_respects_next_parameter(self, anonymous_client, admin_user):
        resp = anonymous_client.post(
            reverse("accounts:login") + "?next=/nodes/",
            {"username": "admin", "password": "pass1234"},
        )
        assert resp.status_code == 302
        assert resp.url == "/nodes/"


@pytest.mark.django_db
class TestLogoutView:
    """登出"""

    def test_logout_redirects_to_login(self, admin_client):
        resp = admin_client.get(reverse("accounts:logout"))
        assert resp.status_code == 302

    def test_logout_anonymous_safe(self, anonymous_client):
        resp = anonymous_client.get(reverse("accounts:logout"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestProfileView:
    """个人中心"""

    def test_profile_accessible(self, admin_client):
        resp = admin_client.get(reverse("accounts:profile"))
        assert resp.status_code == 200

    def test_profile_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("accounts:profile"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestPasswordChangeView:
    """密码修改"""

    def test_page_accessible(self, admin_client):
        resp = admin_client.get(reverse("accounts:password_change"))
        assert resp.status_code == 200

    def test_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("accounts:password_change"))
        assert resp.status_code == 302

    def test_change_password_success(self, admin_client, admin_user):
        resp = admin_client.post(
            reverse("accounts:password_change"),
            {
                "old_password": "pass1234",
                "new_password1": "NewPass@1234!",
                "new_password2": "NewPass@1234!",
            },
        )
        assert resp.status_code == 302
        admin_user.refresh_from_db()
        assert admin_user.check_password("NewPass@1234!")

    def test_change_password_mismatch(self, admin_client):
        resp = admin_client.post(
            reverse("accounts:password_change"),
            {
                "old_password": "pass1234",
                "new_password1": "NewPass@1234!",
                "new_password2": "DifferentPass@1234!",
            },
        )
        assert resp.status_code == 200

    def test_change_password_wrong_old(self, admin_client):
        resp = admin_client.post(
            reverse("accounts:password_change"),
            {
                "old_password": "wrong_old_password",
                "new_password1": "NewPass@1234!",
                "new_password2": "NewPass@1234!",
            },
        )
        assert resp.status_code == 200
