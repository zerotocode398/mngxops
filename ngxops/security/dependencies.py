"""提供当前用户、登录态与权限校验依赖。"""

from typing import Callable, Optional

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ngxops.accounts.models import User
from ngxops.audit.service import prepare_audit_session
from ngxops.database.session import get_session
from ngxops.security.errors import AuthenticationRequired, PermissionDenied
from ngxops.settings.service import maybe_run_daily_purge


PERM_DENIED_TITLE = "无访问权限"
PERM_DENIED_MESSAGE = "当前账号没有使用该功能的权限。请联系管理员分配相应权限后再试。"
PERM_CONFIG_ERROR_TITLE = "权限配置错误"
PERM_CONFIG_ERROR_MESSAGE = "系统权限配置异常，请联系管理员检查后再试。"
PermissionChecker = Callable[[Session, User, str, str], bool]


def get_current_user(
    request: Request,
    db_session: Session = Depends(get_session),
) -> Optional[User]:
    """从服务端会话加载有效账户，停用或不存在的账户会被登出。"""
    raw_user_id = request.session.get("user_id")
    if raw_user_id is None:
        return None
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        request.session.clear()
        return None
    if user_id < 1:
        request.session.clear()
        return None

    user = db_session.scalars(select(User).where(User.id == user_id)).one_or_none()
    if user is None or not user.is_active:
        request.session.clear()
        return None
    prepare_audit_session(db_session, actor_id=user.id, username=user.username)
    maybe_run_daily_purge(request.app.state.database.session_factory)
    return user


def require_authenticated_user(
    user: Optional[User] = Depends(get_current_user),
) -> User:
    """要求请求已关联启用账户。"""
    if user is None:
        raise AuthenticationRequired()
    return user


def require_superuser(
    user: User = Depends(require_authenticated_user),
) -> User:
    """要求请求账户拥有超级管理员标记。"""
    if not user.is_superuser:
        raise PermissionDenied(PERM_DENIED_TITLE, PERM_DENIED_MESSAGE)
    return user


def require_permission(resource: str, action: str) -> Callable[..., User]:
    """构造按资源和动作检查 RBAC 的 FastAPI 依赖。"""
    def permission_dependency(
        request: Request,
        user: User = Depends(require_authenticated_user),
        db_session: Session = Depends(get_session),
    ) -> User:
        """允许超级管理员绕过检查，其余用户交由 RBAC 解析器判断。"""
        if user.is_superuser:
            return user
        if not resource or not action:
            raise PermissionDenied(
                PERM_CONFIG_ERROR_TITLE,
                PERM_CONFIG_ERROR_MESSAGE,
            )
        checker: Optional[PermissionChecker] = getattr(
            request.app.state,
            "permission_checker",
            None,
        )
        if checker is None:
            raise PermissionDenied(
                PERM_CONFIG_ERROR_TITLE,
                PERM_CONFIG_ERROR_MESSAGE,
            )
        if checker(db_session, user, resource, action):
            return user
        raise PermissionDenied(PERM_DENIED_TITLE, PERM_DENIED_MESSAGE)

    return permission_dependency


def login_user(request: Request, user: User) -> None:
    """轮换会话键并记录已经通过凭证校验的账户 ID。"""
    if not user.is_active or not user.id:
        raise ValueError("未持久化或停用用户不能建立登录会话")
    request.session.cycle_key()
    request.session["user_id"] = user.id


def logout_user(request: Request) -> None:
    """清空并撤销当前服务端会话。"""
    request.session.clear()
