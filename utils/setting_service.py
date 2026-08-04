"""系统设置服务 - 统一配置读取入口"""

from django.core.cache import cache

_defaults = {}


def _cast_value(value, value_type):
    """根据类型转换值"""
    if value_type == "integer":
        return int(value)
    elif value_type == "boolean":
        return str(value).lower() in ("true", "1", "yes")
    return value


def get_setting(key, default=None):
    """从缓存读取系统设置，缓存未命中则查数据库"""
    cache_key = f"system_setting:{key}"
    value = cache.get(cache_key)
    if value is not None:
        return value

    try:
        from apps.settings.models import SystemSetting
        obj = SystemSetting.objects.get(key=key)
        value = _cast_value(obj.value, obj.type)
    except Exception:
        value = default

    cache.set(cache_key, value, timeout=3600)
    return value


def refresh_setting_cache(key=None):
    """保存配置后刷新缓存"""
    if key:
        cache.delete(f"system_setting:{key}")
    else:
        from apps.settings.models import preset_key_set
        keys = list(preset_key_set() | set(_defaults.keys()))
        cache.delete_many([f"system_setting:{k}" for k in keys])


# 包大小上限：仅当库内仍为旧默认 500 时迁移为新默认 20
_PACKAGE_MAX_SIZE_LEGACY_DEFAULT = "500"
_PACKAGE_MAX_SIZE_KEY = "upgrade.package_max_size_mb"


def seed_default_settings():
    """初始化预置配置项：upsert 元数据，并删除不在 PRESET 的孤儿键"""
    from apps.settings.models import SystemSetting, PRESET_SETTINGS, preset_key_set

    for item in PRESET_SETTINGS:
        obj, created = SystemSetting.objects.get_or_create(
            key=item["key"],
            defaults={
                "value": item["value"],
                "type": item["type"],
                "group": item["group"],
                "label": item["label"],
                "description": item.get("description", ""),
                "sort_order": item.get("sort_order", 0),
            },
        )
        if not created:
            # 同步展示元数据，不覆盖用户已保存的 value
            mapping = {
                "type": item["type"],
                "group": item["group"],
                "label": item["label"],
                "description": item.get("description", ""),
                "sort_order": item.get("sort_order", 0),
            }
            update_fields = []
            for field, new_val in mapping.items():
                if getattr(obj, field) != new_val:
                    setattr(obj, field, new_val)
                    update_fields.append(field)
            # 包上限：旧默认 500 → 新默认 20（已手工改过则保留）
            if (
                item["key"] == _PACKAGE_MAX_SIZE_KEY
                and obj.value == _PACKAGE_MAX_SIZE_LEGACY_DEFAULT
                and item["value"] != _PACKAGE_MAX_SIZE_LEGACY_DEFAULT
            ):
                obj.value = item["value"]
                update_fields.append("value")
            if update_fields:
                obj.save(update_fields=update_fields)
                if "value" in update_fields:
                    refresh_setting_cache(obj.key)

    # 清理已从 PRESET 移除的孤儿配置
    orphan_keys = list(
        SystemSetting.objects.exclude(key__in=preset_key_set()).values_list("key", flat=True)
    )
    if orphan_keys:
        SystemSetting.objects.filter(key__in=orphan_keys).delete()
        for k in orphan_keys:
            refresh_setting_cache(k)
