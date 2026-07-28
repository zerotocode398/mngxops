"""系统设置相关中间件"""


class DataRetentionMiddleware:
    """登录用户请求时，每日最多触发一次过期数据清理（后台线程）"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        """已认证请求触发 maybe_run_daily_purge"""
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            try:
                from utils.data_retention import maybe_run_daily_purge
                maybe_run_daily_purge()
            except Exception:
                pass
        return self.get_response(request)
