"""系统设置服务 - 统一配置读取入口"""

from django.core.cache import cache

_defaults = {}


def _cast_value(value, value_type):
    """根据类型转换值"""
    if value_type == "integer":
        return int(value)
    elif value_type == "boolean":
        return value.lower() in ("true", "1", "yes")
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
        cache.delete_many(
            [f"system_setting:{k}" for k in _defaults.keys()]
        )


def seed_default_settings():
    """初始化预置配置项（幂等操作）"""
    from apps.settings.models import SystemSetting, PRESET_SETTINGS

    for item in PRESET_SETTINGS:
        SystemSetting.objects.get_or_create(
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