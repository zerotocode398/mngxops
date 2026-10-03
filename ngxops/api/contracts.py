"""定义 JSON API 共用的错误结构与响应文档。"""

from typing import Dict, List, Mapping, Optional, Sequence

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ApiFieldError(BaseModel):
    """描述一个请求字段的校验错误。"""

    field: str
    message: str


class ApiError(BaseModel):
    """描述 JSON API 使用的稳定错误响应。"""

    success: bool = False
    message: str
    redirect: Optional[str] = None
    errors: Optional[List[ApiFieldError]] = None


_ERROR_DESCRIPTIONS = {
    400: "请求格式或业务参数无效。",
    401: "缺少有效登录态。",
    403: "请求未通过权限或 CSRF 校验。",
    404: "资源不存在或当前用户无权查看。",
    409: "并发变更导致资源状态冲突。",
    413: "上传内容超过接口允许的大小。",
    422: "请求参数未通过校验。",
    500: "服务器发生未预期错误。",
    503: "服务当前无法执行该操作。",
}


def api_error_responses(status_codes: Sequence[int]) -> Dict[int, dict]:
    """为 OpenAPI 响应表生成共享错误模型声明。"""
    return {
        status_code: {
            "model": ApiError,
            "description": _ERROR_DESCRIPTIONS[status_code],
        }
        for status_code in status_codes
    }


def api_error_response(
    status_code: int,
    message: str,
    redirect: Optional[str] = None,
    errors: Optional[List[ApiFieldError]] = None,
    headers: Optional[Mapping[str, str]] = None,
) -> JSONResponse:
    """使用稳定错误模型创建 JSON 响应。"""
    body = ApiError(
        message=message,
        redirect=redirect,
        errors=errors,
    ).model_dump(exclude_none=True)
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def is_json_api_request(request: Request) -> bool:
    """识别 JSON API、探活接口和明确请求 JSON 的浏览器请求。"""
    accept = request.headers.get("accept", "").lower()
    return (
        request.url.path == "/health"
        or request.url.path.startswith("/api/")
        or request.headers.get("x-requested-with", "").lower()
        == "xmlhttprequest"
        or "application/json" in accept
    )


def http_error_message(detail: object) -> str:
    """提取可安全返回的 HTTP 错误文案。"""
    return detail if isinstance(detail, str) else "请求失败"
