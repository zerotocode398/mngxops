"""提供操作审计与登录记录的筛选分页页面。"""

from datetime import date, datetime, time, timedelta
from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from ngxops.accounts.models import LoginLog, User
from ngxops.audit.models import AuditLog
from ngxops.database.session import get_session
from ngxops.security.dependencies import require_permission
from ngxops.tasks.api import _task_permissions
from ngxops.tasks.models import Task
from ngxops.ui import render_page


router = APIRouter(prefix="/audit", tags=["audit"])
PAGE_SIZES = (10, 20, 50)
FAIL_REASON_LABELS = {
    "": "未知",
    "user_not_found": "用户不存在",
    "wrong_password": "密码错误",
    "user_locked": "用户已锁定",
    "user_inactive": "用户未激活",
}
MODULE_LINKS = {
    "节点管理": "/nodes/",
    "节点分组": "/nodes/groups/",
    "凭证管理": "/credentials/",
    "配置管理": "/configs/",
    "配置绑定": "/configs/",
    "绑定版本": "/configs/",
    "发布管理": "/releases/center/",
    "Nginx 升级": "/upgrade/",
    "Nginx 安装": "/nginx-install/",
    "Nginx 启停": "/nginx/service/",
    "Nginx 卸载": "/nginx/uninstall/",
    "用户管理": "/users/",
    "角色管理": "/users/roles/",
    "用户组管理": "/users/groups/",
    "登录管理": "/audit/logins/",
}


def _search_terms(value: str) -> list:
    """按中英文逗号拆分并清理多标签搜索词。"""
    return [
        term.strip()
        for term in (value or "").replace("，", ",").split(",")
        if term.strip()
    ]


def _date_range(value: str, end_of_day: bool = False) -> Optional[datetime]:
    """将北京时间日期筛选转换为 SQLite 保存的 UTC 无时区边界。"""
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    local_boundary = datetime.combine(parsed, time.min) - timedelta(hours=8)
    return local_boundary + (timedelta(days=1) if end_of_day else timedelta())


def _pagination(session: Session, statement, count_statement, request: Request) -> dict:
    """按当前查询生成统一的每页十、二十、五十条分页结果。"""
    total = int(session.scalar(count_statement) or 0)
    try:
        requested_page = max(1, int(request.query_params.get("page", "1")))
        per_page = int(request.query_params.get("per_page", "10"))
    except ValueError:
        requested_page, per_page = 1, 10
    if per_page not in PAGE_SIZES:
        per_page = 10
    pages = max(1, int(ceil(total / float(per_page))))
    page = min(requested_page, pages)
    items = session.scalars(
        statement.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    return {
        "items": items,
        "pagination": {
            "page": page,
            "pages": pages,
            "total": total,
            "per_page": per_page,
            "per_page_options": PAGE_SIZES,
        },
    }


def _format_created_at(value: datetime) -> str:
    """按原管理页面使用的北京时间格式展示日志时间。"""
    return (value + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


@router.get("/", response_class=Response, include_in_schema=False)
def audit_log_list(
    request: Request,
    search: str = Query("", max_length=500),
    module: str = Query("", max_length=100),
    result: str = Query("", max_length=20),
    date_from: str = Query("", max_length=10),
    date_to: str = Query("", max_length=10),
    user: User = Depends(require_permission("audit", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """显示支持模块、结果、日期和多标签关键词的操作日志。"""
    statement = select(AuditLog)
    count_statement = select(func.count()).select_from(AuditLog)
    conditions = []
    for term in _search_terms(search):
        pattern = "%{}%".format(term)
        conditions.append(
            or_(
                AuditLog.username.ilike(pattern),
                AuditLog.action.ilike(pattern),
                AuditLog.detail.ilike(pattern),
            )
        )
    if module:
        conditions.append(AuditLog.module == module)
    if result in ("success", "failed"):
        conditions.append(AuditLog.result == result)
    start = _date_range(date_from)
    end = _date_range(date_to, end_of_day=True)
    if start:
        conditions.append(AuditLog.created_at >= start)
    if end:
        conditions.append(AuditLog.created_at < end)
    for condition in conditions:
        statement = statement.where(condition)
        count_statement = count_statement.where(condition)
    page_data = _pagination(session, statement, count_statement, request)
    modules = session.scalars(
        select(AuditLog.module).distinct().order_by(AuditLog.module)
    ).all()
    task_ids = {log.task_id for log in page_data["items"] if log.task_id}
    visible_task_ids = set()
    if task_ids:
        can_read_all_tasks, allowed_operations, _ = _task_permissions(
            request, session, user, include_poll_permissions=True
        )
        task_query = select(Task.id).where(Task.id.in_(task_ids))
        if not can_read_all_tasks:
            if allowed_operations:
                task_query = task_query.where(
                    Task.operation_type.in_(allowed_operations),
                    Task.trigger_user_id == user.id,
                )
            else:
                task_query = None
        if task_query is not None:
            visible_task_ids = set(session.scalars(task_query).all())
    rows = []
    for log in page_data["items"]:
        can_view_task = log.task_id in visible_task_ids
        task_detail_prefix = "创建任务：#{}".format(log.task_id or "")
        task_link_in_detail = bool(
            log.task_id and log.detail.startswith(task_detail_prefix)
        )
        task_detail_suffix = (
            log.detail[len(task_detail_prefix) :] if task_link_in_detail else ""
        )
        rows.append(
            {
                "log": log,
                "module_link": (
                    "/tasks/{}/".format(log.task_id)
                    if can_view_task
                    else MODULE_LINKS.get(log.module)
                ),
                "can_view_task": can_view_task,
                "task_link_in_detail": task_link_in_detail,
                "task_detail_suffix": task_detail_suffix,
                "task_detail_preview_suffix": task_detail_suffix[
                    : max(0, 80 - len(task_detail_prefix))
                ],
                "created_at_display": _format_created_at(log.created_at),
            }
        )
    return render_page(
        request,
        "audit/list.html",
        context={
            "rows": rows,
            "search": search,
            "module_filter": module,
            "result_filter": result,
            "date_from": date_from,
            "date_to": date_to,
            "modules": modules,
            "has_filters": bool(search or module or result or date_from or date_to),
            "pagination": page_data["pagination"],
        },
        user=user,
        db_session=session,
    )


@router.get("/logins/", response_class=Response, include_in_schema=False)
def login_log_list(
    request: Request,
    search: str = Query("", max_length=500),
    status: str = Query("", max_length=20),
    date_from: str = Query("", max_length=10),
    date_to: str = Query("", max_length=10),
    user: User = Depends(require_permission("audit", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """显示成功与失败的登录记录和失败原因。"""
    statement = select(LoginLog)
    count_statement = select(func.count()).select_from(LoginLog)
    conditions = []
    for term in _search_terms(search):
        pattern = "%{}%".format(term)
        conditions.append(
            or_(LoginLog.username.ilike(pattern), LoginLog.ip.ilike(pattern))
        )
    if status in ("success", "failed"):
        conditions.append(LoginLog.status == status)
    start = _date_range(date_from)
    end = _date_range(date_to, end_of_day=True)
    if start:
        conditions.append(LoginLog.created_at >= start)
    if end:
        conditions.append(LoginLog.created_at < end)
    for condition in conditions:
        statement = statement.where(condition)
        count_statement = count_statement.where(condition)
    total = int(session.scalar(count_statement) or 0)
    try:
        requested_page = max(1, int(request.query_params.get("page", "1")))
        per_page = int(request.query_params.get("per_page", "10"))
    except ValueError:
        requested_page, per_page = 1, 10
    if per_page not in PAGE_SIZES:
        per_page = 10
    pages = max(1, int(ceil(total / float(per_page))))
    page = min(requested_page, pages)
    logs = session.scalars(
        statement.order_by(LoginLog.created_at.desc(), LoginLog.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    return render_page(
        request,
        "audit/login_list.html",
        context={
            "logs": logs,
            "created_at_display": {
                log.id: _format_created_at(log.created_at) for log in logs
            },
            "search": search,
            "status_filter": status,
            "date_from": date_from,
            "date_to": date_to,
            "has_filters": bool(search or status or date_from or date_to),
            "fail_reason_labels": FAIL_REASON_LABELS,
            "pagination": {
                "page": page,
                "pages": pages,
                "total": total,
                "per_page": per_page,
                "per_page_options": PAGE_SIZES,
            },
        },
        user=user,
        db_session=session,
    )
