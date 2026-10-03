"""集中检查发布中心页面和只读选择接口权限。"""

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ngxops.accounts.models import User
from ngxops.database.session import get_session
from ngxops.security.dependencies import require_authenticated_user
from ngxops.security.errors import PermissionDenied


def require_release_access(
    request: Request,
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> User:
    """允许拥有发布查看或发布执行权限的账户进入发布中心。"""
    if user.is_superuser:
        return user
    checker = getattr(request.app.state, "permission_checker", None)
    if checker and (
        checker(session, user, "releases", "read")
        or checker(session, user, "releases", "publish")
    ):
        return user
    raise PermissionDenied("无访问权限", "当前账号没有查看发布中心的权限。")


def can_publish(request: Request, session: Session, user: User) -> bool:
    """判断账户是否拥有发布执行权限。"""
    if user.is_superuser:
        return True
    checker = getattr(request.app.state, "permission_checker", None)
    return bool(checker and checker(session, user, "releases", "publish"))
