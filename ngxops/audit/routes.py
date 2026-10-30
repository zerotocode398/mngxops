"""提供操作审计与登录记录的筛选分页页面。"""

from datetime import date, datetime, time, timedelta
from math import ceil
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, case, func, or_, select
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
AUDIT_TIME_RANGES = {"all", "today", "7d", "30d", "custom"}
BEIJING_OFFSET = timedelta(hours=8)
PACKAGE_AUDIT_ACTIONS = frozenset(
    (
        "上传源码包",
        "覆盖源码包",
        "删除源码包",
        "上传模块包",
        "覆盖模块包",
        "删除模块包",
    )
)
ARCHIVE_DETAIL_MARKER = "；归档文件："
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


def _detail_preview(log: AuditLog) -> str:
    """生成操作日志的简短预览并将归档文件名留给展开详情。"""
    detail = log.detail or ""
    preview = detail
    if log.action in PACKAGE_AUDIT_ACTIONS and ARCHIVE_DETAIL_MARKER in detail:
        preview = detail.split(ARCHIVE_DETAIL_MARKER, 1)[0]
    if len(preview) > 80:
        return preview[:80] + "..."
    return preview


def _parse_audit_datetime(value: str, end_of_day: bool = False) -> Optional[datetime]:
    """解析操作日志筛选框中的北京时间日期或秒级时间。"""
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    try:
        if len(raw_value) == 10:
            parsed_date = date.fromisoformat(raw_value)
            boundary = time(23, 59, 59) if end_of_day else time.min
            return datetime.combine(parsed_date, boundary)
        parsed_datetime = datetime.fromisoformat(raw_value)
    except ValueError:
        return None
    if parsed_datetime.tzinfo is not None:
        return None
    return parsed_datetime.replace(microsecond=0)


def _audit_time_bounds(
    time_range: str, date_from: str, date_to: str
) -> Tuple[Optional[datetime], Optional[datetime], str]:
    """转换操作日志预设或自定义范围为 SQLite UTC 查询边界。"""
    local_today = (datetime.utcnow() + BEIJING_OFFSET).date()
    days_back = {"today": 0, "7d": 6, "30d": 29}.get(time_range)
    if days_back is not None:
        start_local = datetime.combine(
            local_today - timedelta(days=days_back), time.min
        )
        end_local = datetime.combine(local_today + timedelta(days=1), time.min)
        return (
            start_local - BEIJING_OFFSET,
            end_local - BEIJING_OFFSET,
            "",
        )

    start_local = _parse_audit_datetime(date_from)
    end_local = _parse_audit_datetime(date_to, end_of_day=True)
    invalid_start = bool(date_from and start_local is None)
    invalid_end = bool(date_to and end_local is None)
    if invalid_start or invalid_end:
        return None, None, "请输入有效的起止时间。"
    if start_local and end_local and start_local > end_local:
        return None, None, "结束时间不能早于开始时间。"
    start = start_local - BEIJING_OFFSET if start_local else None
    end = end_local - BEIJING_OFFSET + timedelta(seconds=1) if end_local else None
    return start, end, ""


def _format_audit_datetime(value: str, end_of_day: bool = False) -> str:
    """将有效的操作日志时间转换为 datetime-local 控件值。"""
    parsed = _parse_audit_datetime(value, end_of_day=end_of_day)
    return parsed.strftime("%Y-%m-%dT%H:%M:%S") if parsed else ""


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
    time_range: str = Query("all", max_length=20),
    date_from: str = Query("", max_length=32),
    date_to: str = Query("", max_length=32),
    user: User = Depends(require_permission("audit", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """显示按标签关键词和时间范围筛选的操作日志。"""
    statement = select(AuditLog).outerjoin(Task, Task.id == AuditLog.task_id)
    count_statement = (
        select(func.count(AuditLog.id))
        .select_from(AuditLog)
        .outerjoin(Task, Task.id == AuditLog.task_id)
    )
    task_audit_result = case(
        (
            and_(
                AuditLog.action != "取消异步任务",
                Task.status == "success",
            ),
            "success",
        ),
        (
            and_(
                AuditLog.action != "取消异步任务",
                Task.status.in_(("failed", "cancelled")),
            ),
            "failed",
        ),
        else_=AuditLog.result,
    )
    conditions = []
    for term in _search_terms(search):
        pattern = "%{}%".format(term)
        conditions.append(
            or_(
                AuditLog.username.ilike(pattern),
                AuditLog.ip.ilike(pattern),
                AuditLog.action.ilike(pattern),
                AuditLog.detail.ilike(pattern),
                AuditLog.source_batch.ilike(pattern),
                Task.target_hostnames.ilike(pattern),
                Task.target_ips.ilike(pattern),
            )
        )
    modules = session.scalars(
        select(AuditLog.module).distinct().order_by(AuditLog.module)
    ).all()
    if module not in modules:
        module = ""
    if result not in ("success", "failed"):
        result = ""
    if module:
        conditions.append(AuditLog.module == module)
    if result in ("success", "failed"):
        conditions.append(task_audit_result == result)
    if time_range not in AUDIT_TIME_RANGES:
        time_range = "all"
    if time_range == "all" and (date_from or date_to):
        time_range = "custom"
    start, end, date_error = _audit_time_bounds(time_range, date_from, date_to)
    if date_error:
        conditions.append(AuditLog.id == 0)
    if start:
        conditions.append(AuditLog.created_at >= start)
    if end:
        conditions.append(AuditLog.created_at < end)
    for condition in conditions:
        statement = statement.where(condition)
        count_statement = count_statement.where(condition)
    page_data = _pagination(session, statement, count_statement, request)
    task_ids = {log.task_id for log in page_data["items"] if log.task_id}
    task_statuses = {}
    visible_task_ids = set()
    if task_ids:
        task_statuses = dict(
            session.execute(
                select(Task.id, Task.status).where(Task.id.in_(task_ids))
            ).all()
        )
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
        task_status = task_statuses.get(log.task_id)
        effective_result = log.result
        if log.action != "取消异步任务":
            effective_result = {
                "success": "success",
                "failed": "failed",
                "cancelled": "failed",
            }.get(task_status, effective_result)
        detail_preview = _detail_preview(log)
        task_link_in_detail = bool(
            log.task_id and (log.detail or "").startswith("创建任务：")
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
                "result": effective_result,
                "task_link_in_detail": task_link_in_detail,
                "detail_preview": detail_preview,
                "show_detail_toggle": (
                    len(log.detail or "") > 80
                    or detail_preview != (log.detail or "")
                    or task_link_in_detail
                ),
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
            "modules": modules,
            "time_range": time_range,
            "date_from": (
                _format_audit_datetime(date_from) if time_range == "custom" else ""
            ),
            "date_to": (
                _format_audit_datetime(date_to, end_of_day=True)
                if time_range == "custom"
                else ""
            ),
            "date_error": date_error,
            "has_filters": bool(
                search or module or result or time_range != "all"
            ),
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
    time_range: str = Query("all", max_length=20),
    date_from: str = Query("", max_length=32),
    date_to: str = Query("", max_length=32),
    user: User = Depends(require_permission("audit", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """显示按关键词、结果和北京时间范围筛选的登录记录。"""
    statement = select(LoginLog)
    count_statement = select(func.count()).select_from(LoginLog)
    conditions = []
    for term in _search_terms(search):
        pattern = "%{}%".format(term)
        conditions.append(
            or_(LoginLog.username.ilike(pattern), LoginLog.ip.ilike(pattern))
        )
    if status not in ("success", "failed"):
        status = ""
    if status:
        conditions.append(LoginLog.status == status)
    if time_range not in AUDIT_TIME_RANGES:
        time_range = "all"
    if time_range == "all" and (date_from or date_to):
        time_range = "custom"
    start, end, date_error = _audit_time_bounds(time_range, date_from, date_to)
    if date_error:
        conditions.append(LoginLog.id == 0)
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
            "time_range": time_range,
            "date_from": (
                _format_audit_datetime(date_from) if time_range == "custom" else ""
            ),
            "date_to": (
                _format_audit_datetime(date_to) if time_range == "custom" else ""
            ),
            "date_error": date_error,
            "has_filters": bool(search or status or time_range != "all"),
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
