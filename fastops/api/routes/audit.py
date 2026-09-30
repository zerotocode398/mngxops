"""FastAPI 审计日志 JSON 接口。"""

from fastapi import APIRouter, Depends, Query

from fastops.api.deps import ensure_permission, require_user
from fastops.schemas.audit import (
    AuditLogListResponse,
    AuditModuleListResponse,
    LoginLogListResponse,
)
from fastops.services.audit import list_audit_logs, list_audit_modules, list_login_logs
from fastops.services.pagination import normalize_page
from fastops.services.users import FastUser

router = APIRouter(prefix="/api/v1/audit", tags=["审计"])


@router.get("/modules", response_model=AuditModuleListResponse, summary="查询审计模块")
def audit_modules_api(user: FastUser = Depends(require_user)):
    """查询操作日志模块下拉数据。"""
    ensure_permission(user, "audit", "read")
    return {"items": list_audit_modules()}


@router.get("/operation-logs", response_model=AuditLogListResponse, summary="查询操作日志")
def operation_logs_api(
    search: str = Query("", description="逗号分隔搜索词，匹配用户、动作或详情"),
    module: str = Query("", description="模块名称"),
    result: str = Query("", description="执行结果：success/failed"),
    date_from: str = Query("", description="开始日期：YYYY-MM-DD"),
    date_to: str = Query("", description="结束日期：YYYY-MM-DD"),
    page: int = Query(1, ge=1, description="页码"),
    per_page: int = Query(10, description="每页条数：10/20/50/100"),
    user: FastUser = Depends(require_user),
):
    """分页查询操作日志。"""
    ensure_permission(user, "audit", "read")
    page, per_page = normalize_page(str(page), str(per_page))
    return list_audit_logs(
        search=search,
        module=module,
        result=result,
        date_from=date_from,
        date_to=date_to,
        page=page,
        per_page=per_page,
    )


@router.get("/login-logs", response_model=LoginLogListResponse, summary="查询登录日志")
def login_logs_api(
    search: str = Query("", description="逗号分隔搜索词，匹配用户名或 IP"),
    status: str = Query("", description="登录结果：success/failed"),
    date_from: str = Query("", description="开始日期：YYYY-MM-DD"),
    date_to: str = Query("", description="结束日期：YYYY-MM-DD"),
    page: int = Query(1, ge=1, description="页码"),
    per_page: int = Query(10, description="每页条数：10/20/50/100"),
    user: FastUser = Depends(require_user),
):
    """分页查询登录日志。"""
    ensure_permission(user, "audit", "read")
    page, per_page = normalize_page(str(page), str(per_page))
    return list_login_logs(
        search=search,
        status=status,
        date_from=date_from,
        date_to=date_to,
        page=page,
        per_page=per_page,
    )
