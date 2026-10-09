"""提供 Jinja2 发布中心页面。"""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ngxops.accounts.models import User
from ngxops.database.session import get_session
from ngxops.nodes.models import Node
from ngxops.releases.api import (
    MAX_SELECTED_BINDINGS,
    _release_node_query,
    _release_status_counts,
)
from ngxops.releases.permissions import can_publish, require_release_access
from ngxops.security.dependencies import require_permission
from ngxops.settings.service import read_setting
from ngxops.ui import render_page


router = APIRouter(prefix="/releases", tags=["releases"])


@router.get("/", response_class=Response, summary="发布历史")
def release_history(
    request: Request,
    search: str = Query("", max_length=200),
    user: User = Depends(require_permission("releases", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """渲染按批次查看发布和回滚结果的历史页面。"""
    return render_page(
        request,
        "releases/history.html",
        context={"can_publish": can_publish(request, session, user), "search": search},
        user=user,
        db_session=session,
    )


@router.get("/center/", response_class=Response, summary="发布中心")
def release_center(
    request: Request,
    search: str = Query("", max_length=200),
    sync_status: str = Query("", max_length=20),
    nginx_available: Literal["true", "false", "all"] = "true",
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
    user: User = Depends(require_release_access),
    session: Session = Depends(get_session),
) -> Response:
    """渲染按节点选择配置版本的发布中心。"""
    if sync_status not in (
        "",
        "pending",
        "synced",
        "failed",
        "orphaned",
        "marked_deleted",
    ):
        sync_status = ""
    query = _release_node_query(
        search,
        None,
        "",
        "",
        sync_status,
        nginx_available,
    )
    total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    count_base = select(Node).where(
        Node.is_deleted.is_(False),
        Node.is_locked.is_(False),
    )
    total_nodes = int(
        session.scalar(select(func.count()).select_from(count_base.subquery())) or 0
    )
    nginx_nodes = int(
        session.scalar(
            select(func.count()).select_from(
                count_base.where(Node.nginx_available.is_(True)).subquery()
            )
        )
        or 0
    )
    return render_page(
        request,
        "releases/center.html",
        context={
            "search": search,
            "sync_status": sync_status,
            "nginx_filter": nginx_available,
            "status_counts": _release_status_counts(session),
            "nginx_available_count": nginx_nodes,
            "total_nodes_count": total_nodes,
            "pagination": {
                "page": page,
                "pages": pages,
                "total": total,
                "per_page": per_page,
                "per_page_options": (10, 25, 50, 100),
            },
            "page_size_options": (10, 25, 50, 100),
            "can_publish": can_publish(request, session, user),
            "max_node_count": read_setting(session, "node.batch_max_count", 3),
            "max_binding_count": MAX_SELECTED_BINDINGS,
        },
        user=user,
        db_session=session,
    )
