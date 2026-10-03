"""提供 Jinja2 发布中心页面。"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ngxops.accounts.models import User
from ngxops.database.session import get_session
from ngxops.nodes.models import NodeGroup
from ngxops.releases.api import MAX_SELECTED_BINDINGS
from ngxops.releases.permissions import can_publish, require_release_access
from ngxops.security.dependencies import require_permission
from ngxops.settings.service import read_setting
from ngxops.ui import render_page


router = APIRouter(prefix="/releases", tags=["releases"])


@router.get("/", response_class=Response, summary="发布历史")
def release_history(
    request: Request,
    user: User = Depends(require_permission("releases", "read")),
    session: Session = Depends(get_session),
) -> Response:
    """渲染按批次查看发布和回滚结果的历史页面。"""
    return render_page(
        request,
        "releases/history.html",
        context={"can_publish": can_publish(request, session, user)},
        user=user,
        db_session=session,
    )


@router.get("/center/", response_class=Response, summary="发布中心")
def release_center(
    request: Request,
    user: User = Depends(require_release_access),
    session: Session = Depends(get_session),
) -> Response:
    """渲染按节点选择配置版本的发布中心。"""
    groups = session.scalars(select(NodeGroup).order_by(NodeGroup.name.asc())).all()
    return render_page(
        request,
        "releases/center.html",
        context={
            "groups": groups,
            "can_publish": can_publish(request, session, user),
            "max_node_count": read_setting(session, "node.batch_max_count", 3),
            "max_binding_count": MAX_SELECTED_BINDINGS,
        },
        user=user,
        db_session=session,
    )
