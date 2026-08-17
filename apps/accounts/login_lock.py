"""登录连续失败锁定：读设置、计数、临时锁与管理员解锁。"""
from datetime import timedelta

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from utils.setting_service import get_setting

DEFAULT_FAIL_COUNT = 5
DEFAULT_LOCK_MINUTES = 15


def _int_setting(key, default, min_value, max_value):
    """读取整数设置并限制在允许范围内。"""
    try:
        value = int(get_setting(key, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


def get_fail_lock_count():
    """连续失败达到该次数后锁定。"""
    return _int_setting("auth.login_fail_lock_count", DEFAULT_FAIL_COUNT, 3, 30)


def get_fail_lock_minutes():
    """临时锁定分钟数。"""
    return _int_setting(
        "auth.login_fail_lock_minutes", DEFAULT_LOCK_MINUTES, 1, 1440,
    )


def get_or_create_profile(user):
    """确保用户有资料行，供失败计数落库。"""
    from apps.users.models import UserProfile

    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def clear_login_fail_lock(user):
    """清零失败次数并解除临时锁定。"""
    profile = get_or_create_profile(user)
    if profile.failed_login_count == 0 and profile.login_locked_until is None:
        return profile
    profile.failed_login_count = 0
    profile.login_locked_until = None
    profile.save(update_fields=["failed_login_count", "login_locked_until", "updated_at"])
    return profile


def expire_temp_lock_if_needed(profile):
    """锁定到期则清零计数并解除，返回是否仍处于临时锁定。"""
    if profile.login_locked_until is None:
        return False
    if profile.login_locked_until > timezone.now():
        return True
    profile.failed_login_count = 0
    profile.login_locked_until = None
    profile.save(update_fields=["failed_login_count", "login_locked_until", "updated_at"])
    return False


def is_temp_login_locked(user):
    """用户是否处于未到期的登录失败临时锁定。"""
    try:
        profile = user.profile
    except ObjectDoesNotExist:
        profile = get_or_create_profile(user)
    return expire_temp_lock_if_needed(profile)


def record_login_failure(user):
    """累加连续失败次数，达到阈值则写入临时锁定截止时间。"""
    profile = get_or_create_profile(user)
    if expire_temp_lock_if_needed(profile):
        return profile
    threshold = get_fail_lock_count()
    minutes = get_fail_lock_minutes()
    profile.failed_login_count = (profile.failed_login_count or 0) + 1
    fields = ["failed_login_count", "updated_at"]
    if profile.failed_login_count >= threshold:
        profile.login_locked_until = timezone.now() + timedelta(minutes=minutes)
        fields.append("login_locked_until")
    profile.save(update_fields=fields)
    return profile


def user_login_enabled(user):
    """账号可登录：已启用且未处于临时锁定。"""
    if not user.is_active:
        return False
    return not is_temp_login_locked(user)
