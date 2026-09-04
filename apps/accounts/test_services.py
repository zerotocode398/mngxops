"""accounts 服务层测试（登录锁定逻辑）"""

import pytest
from django.utils import timezone
from datetime import timedelta

from apps.accounts.login_lock import (
    clear_login_fail_lock,
    get_or_create_profile,
    is_temp_login_locked,
    record_login_failure,
)


@pytest.mark.django_db
class TestLoginLock:
    """登录失败锁定机制"""

    def test_get_or_create_profile_creates_new(self, normal_user):
        profile = get_or_create_profile(normal_user)
        assert profile is not None
        assert profile.user == normal_user
        assert profile.failed_login_count == 0

    def test_get_or_create_profile_idempotent(self, normal_user):
        p1 = get_or_create_profile(normal_user)
        p2 = get_or_create_profile(normal_user)
        assert p1.id == p2.id

    def test_clear_login_fail_lock(self, normal_user):
        profile = get_or_create_profile(normal_user)
        profile.failed_login_count = 5
        profile.login_locked_until = timezone.now() + timedelta(minutes=15)
        profile.save()
        clear_login_fail_lock(normal_user)
        profile.refresh_from_db()
        assert profile.failed_login_count == 0
        assert profile.login_locked_until is None

    def test_record_login_failure_increments_count(self, normal_user):
        record_login_failure(normal_user)
        profile = get_or_create_profile(normal_user)
        profile.refresh_from_db()
        assert profile.failed_login_count == 1

    def test_no_lock_before_threshold(self, normal_user):
        record_login_failure(normal_user)
        record_login_failure(normal_user)
        record_login_failure(normal_user)
        assert not is_temp_login_locked(normal_user)

    def test_lock_after_threshold(self, normal_user):
        from apps.accounts.login_lock import get_or_create_profile
        from django.utils import timezone

        for _ in range(5):
            record_login_failure(normal_user)

        profile = get_or_create_profile(normal_user)
        assert profile.failed_login_count >= 5
        assert profile.login_locked_until is not None
        assert profile.login_locked_until > timezone.now()

    def test_expired_lock_releases(self, normal_user):
        profile = get_or_create_profile(normal_user)
        profile.failed_login_count = 5
        profile.login_locked_until = timezone.now() - timedelta(minutes=1)
        profile.save()
        assert not is_temp_login_locked(normal_user)
        profile.refresh_from_db()
        assert profile.failed_login_count == 0
        assert profile.login_locked_until is None

    def test_active_user_not_initially_locked(self, normal_user):
        assert not is_temp_login_locked(normal_user)
