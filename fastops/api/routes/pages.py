"""FastAPI HTML 页面路由。"""

from typing import Union

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from fastops.api.deps import require_page_user
from fastops.core.config import get_settings
from fastops.main_templates import templates
from fastops.services.assets import (
    get_migration_snapshot,
    list_credentials,
    list_nodes,
)
from fastops.services.audit import list_audit_logs, list_audit_modules, list_login_logs
from fastops.services.configs import list_config_nodes
from fastops.services.pagination import build_page_query, normalize_page
from fastops.services.releases import list_release_history_batches, release_status_options
from fastops.services.settings import list_setting_groups, list_settings
from fastops.services.tasks import (
    get_task_center_task,
    list_task_center_tasks,
    operation_type_options,
    status_options,
    user_can_read_task_center,
)
from fastops.services.users import FastUser, user_has_permission

router = APIRouter(tags=["页面"])


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    """返回站点图标。"""
    icon_path = get_settings().static_path / "favicon.png"
    return FileResponse(icon_path, media_type="image/png")


def _forbidden(request: Request, message: str = "当前账号没有使用该功能的权限。"):
    """渲染 FastAPI 无权页面。"""
    return templates.TemplateResponse(
        "forbidden.html",
        {"request": request, "message": message},
        status_code=403,
    )


def _page_user(request: Request) -> Union[FastUser, Response]:
    """读取页面当前用户，未登录时返回跳转响应。"""
    return require_page_user(request)


def _resource_page_user(
    request: Request,
    resource: str,
    action: str = "read",
) -> Union[FastUser, Response]:
    """读取页面用户并校验资源权限。"""
    user = _page_user(request)
    if isinstance(user, Response):
        return user
    if not user_has_permission(user, resource, action):
        return _forbidden(request)
    return user


def _task_center_page_user(request: Request) -> Union[FastUser, Response]:
    """读取页面用户并校验任务中心访问权限。"""
    user = _page_user(request)
    if isinstance(user, Response):
        return user
    if not user_can_read_task_center(user):
        return _forbidden(request)
    return user


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def root_redirect():
    """根路径跳转到 FastAPI 控制台。"""
    from fastapi.responses import RedirectResponse

    return RedirectResponse("/fastapi", status_code=302)


@router.get("/fastapi", response_class=HTMLResponse, summary="迁移控制台")
def migration_home(request: Request):
    """渲染 FastAPI 迁移控制台。"""
    user = _page_user(request)
    if isinstance(user, Response):
        return user
    snapshot = get_migration_snapshot()
    return templates.TemplateResponse(
        "migration.html",
        {"request": request, "user": user, "snapshot": snapshot, "active": "home"},
    )


@router.get("/fastapi/nodes", response_class=HTMLResponse, summary="节点只读页面")
def nodes_page(request: Request):
    """渲染 FastAPI 节点只读列表。"""
    user = _resource_page_user(request, "nodes")
    if isinstance(user, Response):
        return user

    page, per_page = normalize_page(
        request.query_params.get("page"), request.query_params.get("per_page")
    )
    filters = {
        "search": request.query_params.get("search", "").strip(),
        "status": request.query_params.get("status", "").strip(),
    }
    result = list_nodes(page=page, per_page=per_page, **filters)
    return templates.TemplateResponse(
        "nodes/list.html",
        {
            "request": request,
            "user": user,
            "items": result["items"],
            "pagination": result["pagination"],
            "filters": filters,
            "per_page_options": (10, 20, 50, 100),
            "route_path": "/fastapi/nodes",
            "page_query": build_page_query(filters, result["pagination"]["per_page"]),
            "active": "nodes",
        },
    )


@router.get("/fastapi/credentials", response_class=HTMLResponse, summary="凭证只读页面")
def credentials_page(request: Request):
    """渲染 FastAPI 凭证只读列表。"""
    user = _resource_page_user(request, "credentials")
    if isinstance(user, Response):
        return user

    page, per_page = normalize_page(
        request.query_params.get("page"), request.query_params.get("per_page")
    )
    filters = {
        "search": request.query_params.get("search", "").strip(),
        "auth_type": request.query_params.get("auth_type", "").strip(),
        "status": request.query_params.get("status", "").strip(),
    }
    result = list_credentials(page=page, per_page=per_page, **filters)
    return templates.TemplateResponse(
        "credentials/list.html",
        {
            "request": request,
            "user": user,
            "items": result["items"],
            "pagination": result["pagination"],
            "filters": filters,
            "per_page_options": (10, 20, 50, 100),
            "route_path": "/fastapi/credentials",
            "page_query": build_page_query(filters, result["pagination"]["per_page"]),
            "active": "credentials",
        },
    )


@router.get("/fastapi/audit", response_class=HTMLResponse, summary="操作日志只读页面")
def audit_page(request: Request):
    """渲染 FastAPI 操作日志只读列表。"""
    user = _resource_page_user(request, "audit")
    if isinstance(user, Response):
        return user

    page, per_page = normalize_page(
        request.query_params.get("page"), request.query_params.get("per_page")
    )
    filters = {
        "search": request.query_params.get("search", "").strip(),
        "module": request.query_params.get("module", "").strip(),
        "result": request.query_params.get("result", "").strip(),
        "date_from": request.query_params.get("date_from", "").strip(),
        "date_to": request.query_params.get("date_to", "").strip(),
    }
    result = list_audit_logs(page=page, per_page=per_page, **filters)
    return templates.TemplateResponse(
        "audit/list.html",
        {
            "request": request,
            "user": user,
            "items": result["items"],
            "modules": list_audit_modules(),
            "pagination": result["pagination"],
            "filters": filters,
            "per_page_options": (10, 20, 50, 100),
            "route_path": "/fastapi/audit",
            "page_query": build_page_query(filters, result["pagination"]["per_page"]),
            "active": "audit",
        },
    )


@router.get("/fastapi/audit/login", response_class=HTMLResponse, summary="登录日志只读页面")
def login_audit_page(request: Request):
    """渲染 FastAPI 登录日志只读列表。"""
    user = _resource_page_user(request, "audit")
    if isinstance(user, Response):
        return user

    page, per_page = normalize_page(
        request.query_params.get("page"), request.query_params.get("per_page")
    )
    filters = {
        "search": request.query_params.get("search", "").strip(),
        "status": request.query_params.get("status", "").strip(),
        "date_from": request.query_params.get("date_from", "").strip(),
        "date_to": request.query_params.get("date_to", "").strip(),
    }
    result = list_login_logs(page=page, per_page=per_page, **filters)
    return templates.TemplateResponse(
        "audit/login_list.html",
        {
            "request": request,
            "user": user,
            "items": result["items"],
            "pagination": result["pagination"],
            "filters": filters,
            "per_page_options": (10, 20, 50, 100),
            "route_path": "/fastapi/audit/login",
            "page_query": build_page_query(filters, result["pagination"]["per_page"]),
            "active": "audit",
        },
    )


@router.get("/fastapi/tasks", response_class=HTMLResponse, summary="任务中心只读页面")
def task_center_page(request: Request):
    """渲染 FastAPI 任务中心只读列表。"""
    user = _task_center_page_user(request)
    if isinstance(user, Response):
        return user

    page, per_page = normalize_page(
        request.query_params.get("page"), request.query_params.get("per_page")
    )
    filters = {
        "search": request.query_params.get("search", "").strip(),
        "status": request.query_params.get("status", "").strip(),
        "operation_type": request.query_params.get("operation_type", "").strip(),
    }
    result = list_task_center_tasks(user=user, page=page, per_page=per_page, **filters)
    return templates.TemplateResponse(
        "tasks/list.html",
        {
            "request": request,
            "user": user,
            "items": result["items"],
            "pagination": result["pagination"],
            "filters": filters,
            "status_options": status_options(),
            "operation_options": operation_type_options(),
            "per_page_options": (10, 20, 50, 100),
            "route_path": "/fastapi/tasks",
            "page_query": build_page_query(filters, result["pagination"]["per_page"]),
            "active": "tasks",
        },
    )


@router.get("/fastapi/tasks/{task_id}", response_class=HTMLResponse, summary="任务中心详情只读页面")
def task_center_detail_page(task_id: int, request: Request):
    """渲染 FastAPI 任务中心只读详情。"""
    user = _task_center_page_user(request)
    if isinstance(user, Response):
        return user
    task = get_task_center_task(user, task_id)
    if not task:
        return templates.TemplateResponse(
            "forbidden.html",
            {"request": request, "message": "任务不存在或无权查看。"},
            status_code=404,
        )
    return templates.TemplateResponse(
        "tasks/detail.html",
        {"request": request, "user": user, "task": task, "active": "tasks"},
    )


@router.get("/fastapi/settings", response_class=HTMLResponse, summary="系统设置只读页面")
def settings_page(request: Request):
    """渲染 FastAPI 系统设置只读页面。"""
    user = _resource_page_user(request, "settings")
    if isinstance(user, Response):
        return user

    active_group = request.query_params.get("group", "").strip()
    groups = list_setting_groups()
    group_names = {item["name"] for item in groups}
    if active_group and active_group not in group_names:
        active_group = ""
    items = list_settings(group=active_group)
    return templates.TemplateResponse(
        "settings/list.html",
        {
            "request": request,
            "user": user,
            "items": items,
            "groups": groups,
            "active_group": active_group,
            "active": "settings",
        },
    )


@router.get("/fastapi/configs", response_class=HTMLResponse, summary="配置列表只读页面")
def configs_page(request: Request):
    """渲染 FastAPI 配置列表只读页面。"""
    user = _resource_page_user(request, "configs")
    if isinstance(user, Response):
        return user

    page, per_page = normalize_page(
        request.query_params.get("page"), request.query_params.get("per_page")
    )
    filters = {
        "search": request.query_params.get("search", "").strip(),
        "group_id": request.query_params.get("group_id", "").strip(),
        "sync_status": request.query_params.get("sync_status", "").strip(),
        "nginx_available": request.query_params.get("nginx_available", "true").strip() or "true",
    }
    result = list_config_nodes(page=page, per_page=per_page, **filters)
    return templates.TemplateResponse(
        "configs/list.html",
        {
            "request": request,
            "user": user,
            "items": result["items"],
            "pagination": result["pagination"],
            "filters": filters,
            "groups": result["groups"],
            "status_counts": result["status_counts"],
            "unbound_configs": result["unbound_configs"],
            "nginx_available_count": result["nginx_available_count"],
            "total_nodes_count": result["total_nodes_count"],
            "per_page_options": (10, 20, 50, 100),
            "route_path": "/fastapi/configs",
            "page_query": build_page_query(filters, result["pagination"]["per_page"]),
            "active": "configs",
        },
    )


@router.get("/fastapi/releases", response_class=HTMLResponse, summary="发布历史只读页面")
def release_history_page(request: Request):
    """渲染 FastAPI 发布历史只读页面。"""
    user = _resource_page_user(request, "releases")
    if isinstance(user, Response):
        return user

    page, per_page = normalize_page(
        request.query_params.get("page"), request.query_params.get("per_page")
    )
    filters = {
        "search": request.query_params.get("search", "").strip(),
        "status": request.query_params.get("status", "").strip(),
        "batch": request.query_params.get("batch", "").strip(),
        "node_ip": request.query_params.get("node_ip", "").strip(),
    }
    result = list_release_history_batches(page=page, per_page=per_page, **filters)
    return templates.TemplateResponse(
        "releases/list.html",
        {
            "request": request,
            "user": user,
            "items": result["items"],
            "pagination": result["pagination"],
            "filters": filters,
            "status_options": release_status_options(),
            "expand_all": any(filters.values()),
            "per_page_options": (10, 20, 50, 100),
            "route_path": "/fastapi/releases",
            "page_query": build_page_query(filters, result["pagination"]["per_page"]),
            "active": "releases",
        },
    )
