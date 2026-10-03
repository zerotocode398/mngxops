"""定义认证、权限与 CSRF 校验使用的请求异常。"""


class AuthenticationRequired(Exception):
    """表示当前请求需要有效登录态。"""


class PermissionDenied(Exception):
    """携带页面或 JSON 响应使用的统一无权文案。"""

    def __init__(self, title: str, message: str) -> None:
        """保存无权响应的标题和正文。"""
        super().__init__(message)
        self.title = title
        self.message = message


class CsrfViolation(Exception):
    """表示请求未通过 CSRF 来源或令牌校验。"""

