from django.utils.deprecation import MiddlewareMixin


class PartialResponseMiddleware(MiddlewareMixin):
    """检测 X-Partial 请求头，标记 request.is_partial 供视图使用。

    前端在局部刷新时发送 X-Partial: 1 头，视图据此切换为局部模板，
    仅返回主内容区 HTML，避免侧栏等壳层重载。
    """

    def process_request(self, request):
        request.is_partial = request.headers.get("X-Partial") == "1"


class DataRetentionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)
