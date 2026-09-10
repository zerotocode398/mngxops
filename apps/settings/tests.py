"""settings 模块单元测试（系统设置模型、值类型转换、预置配置、缓存读写）"""

import pytest
from django.core.cache import cache
from django.db import IntegrityError

from apps.settings.models import SystemSetting, PRESET_SETTINGS, preset_key_set
from utils.setting_service import (
    _cast_value,
    get_recent_tasks_limit,
    get_setting,
    refresh_setting_cache,
    seed_default_settings,
)


@pytest.mark.django_db
class TestSystemSettingModel:
    """SystemSetting 模型"""

    def test_create(self):
        setting = SystemSetting.objects.create(
            key="test.key",
            value="123",
            type="integer",
            group="测试",
            label="测试项",
        )
        assert setting.key == "test.key"
        assert setting.value == "123"
        assert str(setting) == "test.key = 123"

    def test_ordering(self):
        SystemSetting.objects.create(
            key="b.key", value="1", type="string", group="组B", label="B", sort_order=2
        )
        SystemSetting.objects.create(
            key="a.key", value="1", type="string", group="组A", label="A", sort_order=1
        )
        items = list(SystemSetting.objects.all())
        assert items[0].key == "a.key"

    def test_update_value(self):
        setting = SystemSetting.objects.create(
            key="update.key", value="old", type="string", group="测试", label="更新测试"
        )
        setting.value = "new"
        setting.save()
        setting.refresh_from_db()
        assert setting.value == "new"

    def test_duplicate_key_raises(self):
        SystemSetting.objects.create(
            key="unique.key", value="first", type="string", group="测试", label="唯一键"
        )
        with pytest.raises(IntegrityError):
            SystemSetting.objects.create(
                key="unique.key",
                value="second",
                type="string",
                group="测试",
                label="重复",
            )


class TestCastValue:
    """_cast_value 类型转换"""

    def test_string_passthrough(self):
        assert _cast_value("hello", "string") == "hello"

    def test_integer_conversion(self):
        assert _cast_value("42", "integer") == 42

    def test_boolean_true(self):
        assert _cast_value("true", "boolean") is True
        assert _cast_value("1", "boolean") is True
        assert _cast_value("yes", "boolean") is True
        assert _cast_value("True", "boolean") is True

    def test_boolean_false(self):
        assert _cast_value("false", "boolean") is False
        assert _cast_value("0", "boolean") is False
        assert _cast_value("no", "boolean") is False
        assert _cast_value("", "boolean") is False


class TestPresetSettings:
    """PRESET_SETTINGS 预置配置"""

    def test_all_have_required_keys(self):
        required = {
            "key",
            "group",
            "type",
            "value",
            "label",
            "description",
            "sort_order",
        }
        for item in PRESET_SETTINGS:
            missing = required - set(item.keys())
            assert not missing, f"Missing keys {missing} in {item.get('key')}"

    def test_keys_are_unique(self):
        keys = [item["key"] for item in PRESET_SETTINGS]
        assert len(keys) == len(set(keys)), "Duplicate keys found"

    def test_integer_types_have_min_max(self):
        for item in PRESET_SETTINGS:
            if item["type"] == "integer":
                assert "min_value" in item, f"Missing min_value in {item['key']}"
                assert "max_value" in item, f"Missing max_value in {item['key']}"

    def test_preset_key_set_returns_set(self):
        keys = preset_key_set()
        assert isinstance(keys, set)
        assert len(keys) > 0


@pytest.mark.django_db
class TestGetSetting:
    """get_setting 缓存读取"""

    def test_returns_default_when_no_setting(self):
        cache.delete("system_setting:nonexistent.key")
        result = get_setting("nonexistent.key", default=42)
        assert result == 42

    def test_returns_value_from_db(self):
        cache.delete("system_setting:getsetting.test")
        SystemSetting.objects.create(
            key="getsetting.test",
            value="hello",
            type="string",
            group="测试",
            label="取值测试",
        )
        result = get_setting("getsetting.test", default="fallback")
        assert result == "hello"

    def test_returns_integer_from_db(self):
        cache.delete("system_setting:getsetting.int")
        SystemSetting.objects.create(
            key="getsetting.int",
            value="99",
            type="integer",
            group="测试",
            label="整数测试",
        )
        result = get_setting("getsetting.int", default=0)
        assert result == 99
        assert isinstance(result, int)

    def test_returns_boolean_from_db(self):
        cache.delete("system_setting:getsetting.bool")
        SystemSetting.objects.create(
            key="getsetting.bool",
            value="true",
            type="boolean",
            group="测试",
            label="布尔测试",
        )
        result = get_setting("getsetting.bool", default=False)
        assert result is True

    def test_caches_value(self):
        cache.delete("system_setting:getsetting.cached")
        SystemSetting.objects.create(
            key="getsetting.cached",
            value="cached-val",
            type="string",
            group="测试",
            label="缓存测试",
        )
        get_setting("getsetting.cached")
        cached = cache.get("system_setting:getsetting.cached")
        assert cached == "cached-val"


@pytest.mark.django_db
class TestGetRecentTasksLimit:
    """get_recent_tasks_limit 函数"""

    def test_returns_default_when_no_setting(self):
        cache.delete("system_setting:dashboard.recent_tasks_count")
        result = get_recent_tasks_limit(20)
        assert result == 20

    def test_returns_custom_default(self):
        cache.delete("system_setting:dashboard.recent_tasks_count")
        result = get_recent_tasks_limit(50)
        assert result == 50

    def test_returns_setting_from_db(self):
        cache.delete("system_setting:dashboard.recent_tasks_count")
        SystemSetting.objects.create(
            key="dashboard.recent_tasks_count",
            value="15",
            type="integer",
            group="仪表盘",
            label="最近任务条数",
        )
        result = get_recent_tasks_limit(20)
        assert result == 15

    def test_zero_falls_back_to_default(self):
        cache.delete("system_setting:dashboard.recent_tasks_count")
        SystemSetting.objects.create(
            key="dashboard.recent_tasks_count",
            value="0",
            type="integer",
            group="仪表盘",
            label="最近任务条数",
        )
        result = get_recent_tasks_limit(20)
        assert result == 20

    def test_invalid_falls_back_to_default(self):
        cache.delete("system_setting:dashboard.recent_tasks_count")
        SystemSetting.objects.create(
            key="dashboard.recent_tasks_count",
            value="not-a-number",
            type="string",
            group="仪表盘",
            label="最近任务条数",
        )
        result = get_recent_tasks_limit(20)
        assert result == 20


@pytest.mark.django_db
class TestSeedDefaultSettings:
    """seed_default_settings 初始化"""

    def test_creates_preset_settings(self):
        seed_default_settings()
        preset_keys = preset_key_set()
        created = set(
            SystemSetting.objects.filter(key__in=preset_keys).values_list(
                "key", flat=True
            )
        )
        assert created == preset_keys

    def test_idempotent(self):
        seed_default_settings()
        count_before = SystemSetting.objects.count()
        seed_default_settings()
        assert SystemSetting.objects.count() == count_before


@pytest.mark.django_db
class TestRefreshSettingCache:
    """refresh_setting_cache 缓存刷新"""

    def test_deletes_single_key(self):
        cache.set("system_setting:refresh.test", "stale", timeout=3600)
        refresh_setting_cache("refresh.test")
        assert cache.get("system_setting:refresh.test") is None

    def test_does_not_touch_other_keys(self):
        cache.set("system_setting:keep.me", "keep", timeout=3600)
        refresh_setting_cache("other.key")
        assert cache.get("system_setting:keep.me") == "keep"
