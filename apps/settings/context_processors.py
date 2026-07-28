"""系统设置模板上下文：向前端注入轮询等运行时参数"""

from utils.setting_service import get_setting


def system_runtime_settings(request):
    """注入全局前端可用的系统设置（毫秒）"""
    try:
        poll_sec = max(1, int(get_setting("system.task_progress_poll_interval", "2") or 2))
    except (TypeError, ValueError):
        poll_sec = 2
    try:
        dash_sec = max(5, int(get_setting("system.dashboard_refresh_interval", "30") or 30))
    except (TypeError, ValueError):
        dash_sec = 30
    return {
        "sys_poll_interval_ms": poll_sec * 1000,
        "sys_dashboard_refresh_ms": dash_sec * 1000,
    }
