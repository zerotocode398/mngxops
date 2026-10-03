"""提供 Jinja2 页面上下文、权限导航和统一页面响应。"""

from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from fastapi import Request
from sqlalchemy.orm import Session
from starlette.responses import Response

from ngxops.accounts.models import User
from ngxops.security.sessions import consume_permission_alert


ALLOWED_SIDEBAR_NAV = frozenset(("nginx_install", "upgrade", "nginx_service", "nginx_uninstall"))

_NAVIGATION = (
    {
        "id": "dashboard",
        "label": "首页",
        "icon": "bi-house-door",
        "href": "/",
        "permissions": (),
    },
    {
        "section": "功能模块",
        "items": (
            {
                "id": "nodes",
                "label": "节点管理",
                "icon": "bi-server",
                "permissions": (("nodes", "read"),),
                "children": (
                    {"id": "nodes:list", "label": "节点列表", "href": "/nodes/"},
                    {
                        "id": "nodes:group_list",
                        "label": "节点组管理",
                        "href": "/nodes/groups/",
                    },
                ),
            },
            {
                "id": "credentials",
                "label": "凭证管理",
                "icon": "bi-key",
                "href": "/credentials/",
                "permissions": (("credentials", "read"),),
            },
            {
                "id": "configs",
                "label": "配置管理",
                "icon": "bi-file-earmark-code",
                "href": "/configs/",
                "permissions": (("configs", "read"),),
            },
            {
                "id": "releases",
                "label": "发布管理",
                "icon": "bi-rocket-takeoff",
                "permissions": (
                    ("releases", "read"),
                    ("releases", "publish"),
                    ("nodes", "ssh_test"),
                    ("credentials", "enable"),
                    ("configs", "sync"),
                    ("upgrade", "read"),
                    ("upgrade", "execute"),
                    ("nginx_service", "read"),
                    ("nginx_service", "operate"),
                    ("nginx_uninstall", "read"),
                    ("nginx_uninstall", "execute"),
                ),
                "children": (
                    {
                        "id": "releases:center",
                        "label": "发布中心",
                        "href": "/releases/center/",
                        "permissions": (("releases", "read"), ("releases", "publish")),
                    },
                    {
                        "id": "releases:history",
                        "label": "任务中心",
                        "href": "/tasks/",
                        "permissions": (
                            ("releases", "read"),
                            ("releases", "publish"),
                            ("nodes", "ssh_test"),
                            ("credentials", "enable"),
                            ("configs", "sync"),
                            ("upgrade", "read"),
                            ("upgrade", "execute"),
                            ("nginx_service", "operate"),
                            ("nginx_uninstall", "execute"),
                        ),
                    },
                    {
                        "id": "releases:list",
                        "label": "发布历史",
                        "href": "/releases/",
                        "permissions": (("releases", "read"),),
                    },
                ),
            },
        ),
    },
    {
        "section": "运维工具",
        "items": (
            {
                "id": "ops",
                "label": "运维工具",
                "icon": "bi-tools",
                "permissions": (
                    ("upgrade", "read"),
                    ("upgrade", "execute"),
                    ("nginx_service", "read"),
                    ("nginx_uninstall", "read"),
                ),
                "children": (
                    {
                        "id": "upgrade",
                        "label": "Nginx 升级",
                        "icon": "bi-box-seam",
                        "href": "/upgrade/",
                        "permissions": (("upgrade", "read"), ("upgrade", "execute")),
                    },
                    {
                        "id": "nginx_install",
                        "label": "Nginx 安装",
                        "icon": "bi-download",
                        "href": "/nginx-install/",
                        "permissions": (("upgrade", "read"), ("upgrade", "execute")),
                    },
                    {
                        "id": "nginx_service",
                        "label": "Nginx 启停",
                        "icon": "bi-power",
                        "href": "/nginx/service/",
                        "permissions": (("nginx_service", "read"),),
                    },
                    {
                        "id": "nginx_uninstall",
                        "label": "Nginx 卸载",
                        "icon": "bi-trash3",
                        "href": "/nginx/uninstall/",
                        "permissions": (("nginx_uninstall", "read"),),
                    },
                ),
            },
        ),
    },
    {
        "section": "系统管理",
        "items": (
            {
                "id": "users",
                "label": "用户管理",
                "icon": "bi-people",
                "superuser_only": True,
                "children": (
                    {"id": "users:list", "label": "用户列表", "href": "/users/"},
                    {"id": "users:role_list", "label": "角色管理", "href": "/users/roles/"},
                    {
                        "id": "users:team_list",
                        "label": "用户组管理",
                        "href": "/users/groups/",
                    },
                ),
            },
            {
                "id": "audit",
                "label": "审计日志",
                "icon": "bi-journal-text",
                "permissions": (("audit", "read"),),
                "children": (
                    {"id": "audit:list", "label": "操作日志", "href": "/audit/"},
                    {
                        "id": "audit:login_list",
                        "label": "登录日志",
                        "href": "/audit/logins/",
                    },
                ),
            },
            {
                "id": "settings",
                "label": "系统设置",
                "icon": "bi-gear",
                "href": "/settings/",
                "permissions": (("settings", "read"),),
            },
        ),
    },
)


def _has_any_permission(
    user: User,
    permissions: Any,
    checker: Any,
    db_session: Optional[Session],
) -> bool:
    """按超级管理员或已注入 RBAC 解析器判断任一权限。"""
    if user.is_superuser:
        return True
    if checker is None or db_session is None:
        return False
    return any(
        checker(db_session, user, resource, action)
        for resource, action in permissions
    )


def _visible_item(
    item: Dict[str, Any],
    user: User,
    checker: Any,
    db_session: Optional[Session],
) -> Optional[Dict[str, Any]]:
    """筛选有权限访问的导航项并递归处理子菜单。"""
    if item.get("superuser_only") and not user.is_superuser:
        return None
    permissions = item.get("permissions", ())
    if permissions and not _has_any_permission(user, permissions, checker, db_session):
        return None

    visible = dict(item)
    children = item.get("children")
    if children:
        visible_children = [
            child
            for child in (
                _visible_item(entry, user, checker, db_session)
                for entry in children
            )
            if child is not None
        ]
        if not visible_children:
            return None
        visible["children"] = visible_children
    return visible


def _flatten_navigation(navigation: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """返回用于当前路由匹配的一级与子级链接。"""
    links = []
    for section in navigation:
        entries = section.get("items", [section])
        for item in entries:
            if item.get("href"):
                links.append(item)
            links.extend(
                child for child in item.get("children", ()) if child.get("href")
            )
    return links


def _resolve_active_nav(
    path: str,
    navigation: List[Dict[str, Any]],
    nav_override: str,
) -> str:
    """按路径最长匹配或允许的共享页覆盖值确定活动菜单。"""
    links = _flatten_navigation(navigation)
    if nav_override in ALLOWED_SIDEBAR_NAV:
        if any(item["id"] == nav_override for item in links):
            return nav_override
    normalized_path = path.rstrip("/") or "/"
    matches = []
    for item in links:
        href = item["href"].rstrip("/") or "/"
        if normalized_path == href or (
            href != "/" and normalized_path.startswith(href + "/")
        ):
            matches.append((len(href), item["id"]))
    return max(matches)[1] if matches else ""


def _mark_active_navigation(
    navigation: List[Dict[str, Any]],
    active_nav: str,
) -> List[Dict[str, Any]]:
    """为活动链接和包含活动链接的分组添加模板状态。"""
    marked = []
    for section in navigation:
        section_copy = dict(section)
        items = section.get("items", [section])
        marked_items = []
        for item in items:
            item_copy = dict(item)
            children = [dict(child) for child in item.get("children", ())]
            for child in children:
                child["active"] = child["id"] == active_nav
            item_copy["children"] = children
            item_copy["active"] = item["id"] == active_nav or any(
                child.get("active") for child in children
            )
            marked_items.append(item_copy)
        section_copy["items"] = marked_items
        if "section" not in section:
            marked.extend(marked_items)
        elif marked_items:
            marked.append(section_copy)
    return marked


def build_page_context(
    request: Request,
    context: Optional[Dict[str, Any]] = None,
    user: Optional[User] = None,
    db_session: Optional[Session] = None,
) -> Dict[str, Any]:
    """添加公共 Jinja2 布局、导航、CSRF 与一次性提示上下文。"""
    page_context = dict(context or {})
    current_user = user or page_context.get("current_user")
    permission_checker = getattr(request.app.state, "permission_checker", None)
    permission_cache = {}

    def checker(db: Session, current: User, resource: str, action: str) -> bool:
        """缓存当前页面内重复出现的权限检查结果。"""
        key = (resource, action)
        if key not in permission_cache:
            permission_cache[key] = permission_checker(db, current, resource, action)
        return permission_cache[key]

    active_checker = checker if permission_checker is not None else None
    navigation = []
    if current_user is not None and current_user.is_active:
        for entry in _NAVIGATION:
            if "section" not in entry:
                visible = _visible_item(entry, current_user, active_checker, db_session)
                if visible is not None:
                    navigation.append(visible)
                continue
            visible_items = [
                item
                for item in (
                    _visible_item(candidate, current_user, active_checker, db_session)
                    for candidate in entry["items"]
                )
                if item is not None
            ]
            if visible_items:
                navigation.append({"section": entry["section"], "items": visible_items})

    nav_override = request.query_params.get("nav", "").strip()
    sidebar_nav = nav_override if nav_override in ALLOWED_SIDEBAR_NAV else ""
    active_nav = _resolve_active_nav(request.url.path, navigation, nav_override)
    page_context.update(
        {
            "request": request,
            "current_user": current_user,
            "navigation": _mark_active_navigation(navigation, active_nav),
            "active_nav": active_nav,
            "sidebar_nav": sidebar_nav,
            "nav_qs": "?" + urlencode({"nav": sidebar_nav}) if sidebar_nav else "",
            "nav_query_suffix": "&" + urlencode({"nav": sidebar_nav}) if sidebar_nav else "",
            "csrf_token": getattr(request.state, "csrf_token", ""),
            "permission_alert": consume_permission_alert(request.session),
        }
    )
    if db_session is not None:
        from ngxops.settings.service import read_setting

        page_context["task_poll_interval"] = read_setting(
            db_session, "system.task_progress_poll_interval", 2
        )
    else:
        page_context["task_poll_interval"] = 2
    return page_context


def render_page(
    request: Request,
    template_name: str,
    context: Optional[Dict[str, Any]] = None,
    user: Optional[User] = None,
    db_session: Optional[Session] = None,
    status_code: int = 200,
) -> Response:
    """渲染继承全局布局的 Jinja2 页面。"""
    page_context = build_page_context(request, context, user, db_session)
    return request.app.state.templates.TemplateResponse(
        name=template_name,
        request=request,
        context=page_context,
        status_code=status_code,
    )
