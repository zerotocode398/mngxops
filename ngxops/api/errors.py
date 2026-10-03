"""将框架异常转换成统一 JSON API 错误响应。"""

from typing import Any, Dict, List

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from ngxops.api.contracts import (
    ApiFieldError,
    api_error_response,
    http_error_message,
    is_json_api_request,
)


async def handle_http_error(
    request: Request,
    exc: StarletteHTTPException,
) -> Response:
    """按 JSON API 或普通页面请求返回 HTTP 异常。"""
    if is_json_api_request(request):
        return api_error_response(
            exc.status_code,
            http_error_message(exc.detail),
            headers=exc.headers,
        )
    return PlainTextResponse(
        http_error_message(exc.detail),
        status_code=exc.status_code,
        headers=exc.headers,
    )


def _validation_errors(errors: List[Dict[str, Any]]) -> List[ApiFieldError]:
    """移除原始请求值並整理字段校验信息。"""
    messages = {
        "greater_than_equal": "数值超出允许范围",
        "greater_than": "数值超出允许范围",
        "less_than_equal": "数值超出允许范围",
        "less_than": "数值超出允许范围",
        "string_too_long": "文本长度超出限制",
        "string_too_short": "文本长度不足",
        "missing": "字段必填",
        "json_invalid": "JSON 格式无效",
    }
    return [
        ApiFieldError(
            field=".".join(str(part) for part in error.get("loc", ())) or "request",
            message=messages.get(error.get("type"), "字段格式无效"),
        )
        for error in errors
    ]


async def handle_request_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> Response:
    """将 JSON API 参数错误压缩为不回显输入值的字段列表。"""
    if is_json_api_request(request):
        return api_error_response(
            422,
            "请求参数无效",
            errors=_validation_errors(exc.errors()),
        )
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors())},
    )
