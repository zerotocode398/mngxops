"""实现 RBAC 权限解析和权限矩阵数据整理。"""

from typing import Any, Dict, Iterable, List, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from ngxops.accounts.models import User
from ngxops.rbac.models import (
    PermissionItem,
    profile_permissions,
    profile_roles,
    role_permissions,
    team_members,
    team_roles,
)
from ngxops.rbac.permission_defs import (
    ACTION_CHOICES,
    RESOURCE_CHOICES,
    permission_code,
)


def _role_ids_for_user(db_session: Session, user_id: int) -> Set[int]:
    """优先返回所属用户组角色，没有组角色时回退到个人角色。"""
    team_role_ids = set(
        db_session.scalars(
            select(team_roles.c.role_id)
            .join(team_members, team_members.c.team_id == team_roles.c.team_id)
            .where(team_members.c.user_id == user_id)
        ).all()
    )
    if team_role_ids:
        return team_role_ids
    return set(
        db_session.scalars(
            select(profile_roles.c.role_id).where(profile_roles.c.user_id == user_id)
        ).all()
    )


def user_has_permission(
    db_session: Session,
    user: User,
    resource: str,
    action: str,
) -> bool:
    """依次检查超管、直授及有效角色权限。"""
    if not user.is_active:
        return False
    if user.is_superuser:
        return True
    code = permission_code(resource, action)
    direct_permission = db_session.scalar(
        select(profile_permissions.c.permission_id)
        .join(
            PermissionItem,
            PermissionItem.id == profile_permissions.c.permission_id,
        )
        .where(
            profile_permissions.c.user_id == user.id,
            PermissionItem.code == code,
        )
        .limit(1)
    )
    if direct_permission is not None:
        return True
    role_ids = _role_ids_for_user(db_session, user.id)
    if not role_ids:
        return False
    permission_id = db_session.scalar(
        select(PermissionItem.id).where(PermissionItem.code == code)
    )
    if permission_id is None:
        return False
    return (
        db_session.scalar(
            select(role_permissions.c.role_id)
            .where(
                role_permissions.c.role_id.in_(role_ids),
                role_permissions.c.permission_id == permission_id,
            )
            .limit(1)
        )
        is not None
    )


def build_permission_matrix(
    db_session: Session,
    selected_ids: Iterable[int],
) -> List[Dict[str, Any]]:
    """按资源分组生成角色和用户表单使用的权限矩阵。"""
    selected = set(selected_ids)
    permission_rows = db_session.scalars(
        select(PermissionItem).order_by(PermissionItem.resource, PermissionItem.action)
    ).all()
    by_code = {item.code: item for item in permission_rows}
    action_labels = dict(ACTION_CHOICES)
    matrix = []
    for resource, label in RESOURCE_CHOICES:
        available = {
            item.action for item in permission_rows if item.resource == resource
        }
        actions = []
        for action, _label in ACTION_CHOICES:
            if action not in available:
                continue
            item = by_code.get(permission_code(resource, action))
            if item is None:
                continue
            actions.append(
                {
                    "id": item.id,
                    "code": item.code,
                    "action": item.action,
                    "action_label": action_labels.get(item.action, item.action),
                    "name": item.name,
                    "selected": item.id in selected,
                }
            )
        if actions:
            matrix.append({"resource": resource, "label": label, "actions": actions})
    return matrix
