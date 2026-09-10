"""accounts 模块单元测试（登录表单、用户状态校验）"""

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta

from apps.accounts.forms import LoginForm, CustomPasswordChangeForm
from apps.accounts.login_lock import (
    get_or_create_profile,
    record_login_failure,
    user_login_enabled,
)

User = get_user_model()


class TestLoginForm:
    """登录表单校验"""

    def test_empty_username_invalid(self):
        form = LoginForm(data={"username": "", "password": "pass1234"})
        assert not form.is_valid()
        assert "username" in form.errors

    def test_empty_password_invalid(self):
        form = LoginForm(data={"username": "admin", "password": ""})
        assert not form.is_valid()
        assert "password" in form.errors

    def test_both_empty_invalid(self):
        form = LoginForm(data={"username": "", "password": ""})
        assert not form.is_valid()

    @pytest.mark.django_db
    def test_valid_credentials(self):
        user = User.objects.create_superuser(
            username="testuser", email="test@example.com", password="pass1234"
        )
        form = LoginForm(data={"username": "testuser", "password": "pass1234"})
        assert form.is_valid()

    @pytest.mark.django_db
    def test_wrong_password_invalid(self):
        user = User.objects.create_superuser(
            username="testuser", email="test@example.com", password="pass1234"
        )
        form = LoginForm(data={"username": "testuser", "password": "wrong"})
        assert not form.is_valid()


class TestCustomPasswordChangeForm:
    """密码修改表单校验"""

    @pytest.mark.django_db
    def test_empty_fields_invalid(self):
        user = User.objects.create_superuser(
            username="testuser", email="test@example.com", password="pass1234"
        )
        form = CustomPasswordChangeForm(
            user=user,
            data={
                "old_password": "",
                "new_password1": "",
                "new_password2": "",
            },
        )
        assert not form.is_valid()

    @pytest.mark.django_db
    def test_mismatched_new_passwords_invalid(self):
        user = User.objects.create_superuser(
            username="testuser", email="test@example.com", password="pass1234"
        )
        form = CustomPasswordChangeForm(
            user=user,
            data={
                "old_password": "pass1234",
                "new_password1": "NewPass123!",
                "new_password2": "Different456!",
            },
        )
        assert not form.is_valid()


@pytest.mark.django_db
class TestUserLoginEnabled:
    """用户登录状态校验"""

    def test_active_user_is_enabled(self, normal_user):
        assert user_login_enabled(normal_user)

    def test_inactive_user_is_disabled(self):
        user = User.objects.create_user(
            username="inactive",
            email="in@example.com",
            password="pass1234",
            is_active=False,
        )
        assert not user_login_enabled(user)

    def test_locked_user_is_disabled(self, normal_user):
        profile = get_or_create_profile(normal_user)
        profile.failed_login_count = 5
        profile.login_locked_until = timezone.now() + timedelta(minutes=15)
        profile.save()
        assert not user_login_enabled(normal_user)

    def test_expired_lock_allows_login(self, normal_user):
        profile = get_or_create_profile(normal_user)
        profile.failed_login_count = 5
        profile.login_locked_until = timezone.now() - timedelta(minutes=1)
        profile.save()
        assert user_login_enabled(normal_user)
