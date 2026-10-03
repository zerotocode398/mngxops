"""提供超管专用的用户、角色和用户组管理页面与接口。"""

import math
import re
from datetime import datetime
from typing import Dict, List, Optional, Set

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ngxops.api.contracts import api_error_responses
from ngxops.accounts.models import LoginFailureState, User
from ngxops.audit.service import write_audit_log
from ngxops.accounts.passwords import make_password, validate_new_password
from ngxops.accounts.service import clear_login_fail_lock
from ngxops.database.session import get_session
from ngxops.rbac.models import (
    PermissionItem,
    Role,
    Team,
    UserProfile,
    profile_permissions,
    profile_roles,
    role_permissions,
    team_members,
    team_roles,
)
from ngxops.rbac.service import build_permission_matrix
from ngxops.security.dependencies import require_superuser
from ngxops.ui import render_page


router = APIRouter(prefix="/users", tags=["users"])
api_router = APIRouter(prefix="/api/users/teams", tags=["users"])
_USERNAME_PATTERN = re.compile(r"^[-a-zA-Z0-9_]+$")
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PAGE_SIZES = (10, 25, 50, 100)
_NOTICE_KEY = "ngxops_rbac_notice"


class TeamMemberItem(BaseModel):
    """描述用户组成员管理弹窗中的单个账户。"""

    id: int
    username: str
    email: str
    is_active: bool
    is_superuser: bool
    is_member: bool
    role_count: int


class TeamMembersResponse(BaseModel):
    """描述用户组成员弹窗的分页结果。"""

    users: List[TeamMemberItem]
    page: int
    total_pages: int
    has_next: bool
    has_previous: bool
    total_count: int


class TeamMemberUpdateRequest(BaseModel):
    """描述对用户组成员执行的增删操作。"""

    action: str = Field(pattern="^(add|remove)$")
    user_ids: List[int] = Field(min_length=1, max_length=200)


class TeamMemberUpdateResponse(BaseModel):
    """描述用户组成员变更结果。"""

    success: bool = True
    message: str
    changed_count: int


def _notice_redirect(request: Request, path: str, message: str) -> RedirectResponse:
    """保存一次性成功提示并跳转到管理列表。"""
    request.session[_NOTICE_KEY] = {"message": message, "type": "success"}
    return RedirectResponse(path, status_code=303)


def _page_context(request: Request, extra: Optional[dict] = None) -> dict:
    """读取并清除 RBAC 页面使用的一次性提示。"""
    context = dict(extra or {})
    notice = request.session.pop(_NOTICE_KEY, None)
    if isinstance(notice, dict):
        context["notice"] = notice
    return context


def _render(
    request: Request,
    template: str,
    user: User,
    db_session: Session,
    context: Optional[dict] = None,
    status_code: int = 200,
) -> Response:
    """使用全局页面壳渲染 RBAC 管理页面。"""
    return render_page(
        request,
        template,
        context=_page_context(request, context),
        user=user,
        db_session=db_session,
        status_code=status_code,
    )


def _page_values(page: int, per_page: int) -> tuple:
    """规范列表分页参数并限制每页最大行数。"""
    size = per_page if per_page in _PAGE_SIZES else _PAGE_SIZES[0]
    return max(1, page), size


def _relation_ids(
    db_session: Session,
    model,
    raw_ids: List[str],
) -> Optional[Set[int]]:
    """解析表单关系主键并拒绝非法或已删除的对象。"""
    try:
        ids = {int(value) for value in raw_ids if value}
    except (TypeError, ValueError):
        return None
    if any(value < 1 for value in ids):
        return None
    if not ids:
        return set()
    existing = set(db_session.scalars(select(model.id).where(model.id.in_(ids))).all())
    return ids if existing == ids else None


def _replace_links(
    db_session: Session,
    table,
    owner_column,
    owner_id: int,
    value_column,
    values: Set[int],
) -> None:
    """在当前事务中替换一个对象的多对多关系集合。"""
    db_session.execute(delete(table).where(owner_column == owner_id))
    if values:
        db_session.execute(
            table.insert(),
            [
                {owner_column.name: owner_id, value_column.name: value}
                for value in sorted(values)
            ],
        )


def _permission_form_context(
    db_session: Session,
    selected_ids: Set[int],
) -> List[dict]:
    """构造权限矩阵并标出表单当前选择。"""
    return build_permission_matrix(db_session, selected_ids)


def _form_entities(db_session: Session) -> dict:
    """读取用户表单可选的角色、用户组和权限矩阵。"""
    return {
        "all_roles": db_session.scalars(select(Role).order_by(Role.name)).all(),
        "all_teams": db_session.scalars(select(Team).order_by(Team.name)).all(),
    }


def _user_form_context(
    db_session: Session,
    values: Optional[dict] = None,
    errors: Optional[dict] = None,
    selected_roles: Optional[Set[int]] = None,
    selected_teams: Optional[Set[int]] = None,
    selected_permissions: Optional[Set[int]] = None,
) -> dict:
    """组装用户创建和编辑表单上下文。"""
    context = _form_entities(db_session)
    context.update(
        {
            "values": values or {},
            "errors": errors or {},
            "selected_roles": selected_roles or set(),
            "selected_teams": selected_teams or set(),
            "permission_matrix": _permission_form_context(
                db_session, selected_permissions or set()
            ),
        }
    )
    return context


def _role_form_context(
    db_session: Session,
    role: Optional[Role] = None,
    errors: Optional[dict] = None,
    values: Optional[dict] = None,
    selected_permissions: Optional[Set[int]] = None,
) -> dict:
    """组装角色权限矩阵表单上下文。"""
    if role is not None and selected_permissions is None:
        selected_permissions = {
            permission.id for permission in role.permissions
        }
    context = {
        "role": role,
        "values": values or {},
        "errors": errors or {},
        "permission_matrix": _permission_form_context(
            db_session, selected_permissions or set()
        ),
    }
    return context


def _team_form_context(
    db_session: Session,
    team: Optional[Team] = None,
    values: Optional[dict] = None,
    errors: Optional[dict] = None,
    selected_roles: Optional[Set[int]] = None,
) -> dict:
    """组装用户组基本信息和角色关联表单上下文。"""
    context = _form_entities(db_session)
    context.update(
        {
            "team": team,
            "values": values or {},
            "errors": errors or {},
            "selected_roles": (
                selected_roles
                if selected_roles is not None
                else {role.id for role in team.roles} if team else set()
            ),
        }
    )
    return context


def _validate_user_values(
    db_session: Session,
    values: dict,
    current_user: Optional[User],
    target: Optional[User],
) -> tuple:
    """校验用户身份字段、密码和授权关系主键。"""
    errors = {}
    username = values["username"].strip()
    email = values["email"].strip()
    if (
        not username
        or len(username) > 150
        or not _USERNAME_PATTERN.fullmatch(username)
    ):
        errors["username"] = "用户名仅支持字母、数字、下划线与连字符。"
    duplicate = db_session.scalars(
        select(User.id).where(User.username == username)
    ).first()
    if duplicate is not None and (target is None or duplicate != target.id):
        errors["username"] = "用户名已存在。"
    if len(email) > 254 or not _EMAIL_PATTERN.fullmatch(email):
        errors["email"] = "请输入有效的邮箱地址。"

    role_ids = _relation_ids(db_session, Role, values["role_ids"])
    team_ids = _relation_ids(db_session, Team, values["team_ids"])
    permission_ids = _relation_ids(
        db_session, PermissionItem, values["permission_ids"]
    )
    if role_ids is None or team_ids is None or permission_ids is None:
        errors["relations"] = "所选角色、用户组或权限已失效，请刷新后重试。"
    elif len(role_ids) > 3:
        errors["roles"] = "用户最多只能关联 3 个角色。"

    password1 = values.get("password1", "")
    password2 = values.get("password2", "")
    if target is not None:
        password1 = password1.strip()
        password2 = password2.strip()
        values["password1"] = password1
        values["password2"] = password2
    if target is None or password1 or password2:
        if not password1:
            errors["password1"] = "请输入密码。"
        elif password1 != password2:
            errors["password2"] = "两次输入的密码不一致。"
        else:
            password_error = validate_new_password(password1, username)
            if password_error:
                errors["password1"] = password_error
    if target is not None and target.id == getattr(current_user, "id", None):
        values["is_superuser"] = True
    return errors, role_ids or set(), team_ids or set(), permission_ids or set()


def _persist_user(
    db_session: Session,
    current_user: User,
    target: Optional[User],
    values: dict,
) -> dict:
    """在一个事务中保存用户字段、个人角色、用户组和直授权限。"""
    errors, role_ids, team_ids, permission_ids = _validate_user_values(
        db_session, values, current_user, target
    )
    if errors:
        return {
            "errors": errors,
            "roles": role_ids,
            "teams": team_ids,
            "permissions": permission_ids,
        }
    current_roles = set()
    current_teams = set()
    current_permissions = set()
    if target is not None:
        current_roles = set(
            db_session.scalars(
                select(profile_roles.c.role_id).where(
                    profile_roles.c.user_id == target.id
                )
            ).all()
        )
        current_teams = set(
            db_session.scalars(
                select(team_members.c.team_id).where(
                    team_members.c.user_id == target.id
                )
            ).all()
        )
        current_permissions = set(
            db_session.scalars(
                select(profile_permissions.c.permission_id).where(
                    profile_permissions.c.user_id == target.id
                )
            ).all()
        )
    try:
        with db_session.begin_nested():
            user = target or User(
                username=values["username"].strip(),
                password=make_password(values["password1"]),
            )
            user.username = values["username"].strip()
            user.email = values["email"].strip()
            if target is not None:
                user.is_superuser = bool(values.get("is_superuser"))
                if values.get("password1"):
                    user.password = make_password(values["password1"])
            else:
                user.is_active = True
                user.is_staff = False
                db_session.add(user)
            db_session.flush()
            profile = db_session.get(UserProfile, user.id)
            if profile is None:
                profile = UserProfile(user_id=user.id, remark=values["remark"].strip())
                db_session.add(profile)
            else:
                profile.remark = values["remark"].strip()
            db_session.flush()
            _replace_links(
                db_session,
                profile_roles,
                profile_roles.c.user_id,
                user.id,
                profile_roles.c.role_id,
                role_ids,
            )
            _replace_links(
                db_session,
                team_members,
                team_members.c.user_id,
                user.id,
                team_members.c.team_id,
                team_ids,
            )
            _replace_links(
                db_session,
                profile_permissions,
                profile_permissions.c.user_id,
                user.id,
                profile_permissions.c.permission_id,
                permission_ids,
            )
        if target is not None and (
            current_roles != role_ids
            or current_teams != team_ids
            or current_permissions != permission_ids
        ):
            write_audit_log(
                db_session,
                "用户管理",
                "更新用户授权",
                "用户「{}」授权关系已更新（个人角色 {} 项、用户组 {} 个、直授权限 {} 项）".format(
                    user.username,
                    len(role_ids),
                    len(team_ids),
                    len(permission_ids),
                ),
            )
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
        return {
            "errors": {"username": "用户名已存在，或数据已被其他请求修改。"},
            "roles": role_ids,
            "teams": team_ids,
            "permissions": permission_ids,
        }
    return {
        "user": user,
        "roles": role_ids,
        "teams": team_ids,
        "permissions": permission_ids,
    }


def _parse_user_form(
    username: str,
    email: str,
    remark: str,
    role_ids: List[str],
    team_ids: List[str],
    permission_ids: List[str],
    password1: str = "",
    password2: str = "",
    is_superuser: bool = False,
) -> dict:
    """规整浏览器提交的用户字段和关联主键。"""
    return {
        "username": username,
        "email": email,
        "remark": remark,
        "role_ids": role_ids,
        "team_ids": team_ids,
        "permission_ids": permission_ids,
        "password1": password1,
        "password2": password2,
        "is_superuser": is_superuser,
    }


def _list_users(
    db_session: Session,
    search: str,
    page: int,
    per_page: int,
) -> dict:
    """查询用户列表及每个账户的角色、用户组和登录状态。"""
    page, per_page = _page_values(page, per_page)
    statement = select(User)
    if search.strip():
        pattern = "%{}%".format(search.strip())
        statement = statement.where(
            or_(User.username.ilike(pattern), User.email.ilike(pattern))
        )
    total = db_session.scalar(
        select(func.count()).select_from(statement.subquery())
    ) or 0
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)
    users = db_session.scalars(
        statement.order_by(User.date_joined.desc(), User.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    rows = []
    now = datetime.utcnow()
    for user in users:
        role_names = db_session.scalars(
            select(Role.name)
            .join(profile_roles, profile_roles.c.role_id == Role.id)
            .where(profile_roles.c.user_id == user.id)
            .order_by(Role.name)
        ).all()
        team_names = db_session.scalars(
            select(Team.name)
            .join(team_members, team_members.c.team_id == Team.id)
            .where(team_members.c.user_id == user.id)
            .order_by(Team.name)
        ).all()
        profile = db_session.get(UserProfile, user.id)
        lock = db_session.get(LoginFailureState, user.id)
        temp_locked = bool(
            lock and lock.login_locked_until and lock.login_locked_until > now
        )
        rows.append(
            {
                "user": user,
                "role_names": role_names,
                "team_names": team_names,
                "remark": profile.remark if profile else "",
                "login_enabled": user.is_active and not temp_locked,
                "temporarily_locked": temp_locked,
            }
        )
    return {
        "users": rows,
        "pagination": {
            "page": page,
            "pages": pages,
            "total": total,
            "per_page": per_page,
            "per_page_options": _PAGE_SIZES,
        },
        "search": search,
    }


@router.get("/", include_in_schema=False)
def user_list(
    request: Request,
    search: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=10, ge=1),
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示可搜索、分页的用户清单。"""
    context = _list_users(db_session, search, page, per_page)
    return _render(request, "rbac/users_list.html", admin, db_session, context)


@router.get("/create/", include_in_schema=False)
def user_create_page(
    request: Request,
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示新增用户表单。"""
    return _render(
        request,
        "rbac/user_form.html",
        admin,
        db_session,
        _user_form_context(db_session),
    )


@router.post("/create/", include_in_schema=False)
def user_create(
    request: Request,
    username: str = Form(default=""),
    email: str = Form(default=""),
    remark: str = Form(default=""),
    password1: str = Form(default=""),
    password2: str = Form(default=""),
    role_ids: List[str] = Form(default=[]),
    team_ids: List[str] = Form(default=[]),
    permission_ids: List[str] = Form(default=[]),
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """验证并创建普通用户及其初始授权。"""
    values = _parse_user_form(
        username,
        email,
        remark,
        role_ids,
        team_ids,
        permission_ids,
        password1,
        password2,
    )
    result = _persist_user(db_session, admin, None, values)
    if "errors" not in result:
        return _notice_redirect(
            request, "/users/", "用户 {} 创建成功".format(username.strip())
        )
    context = _user_form_context(
        db_session,
        values,
        result["errors"],
        result["roles"],
        result["teams"],
        result["permissions"],
    )
    return _render(request, "rbac/user_form.html", admin, db_session, context, 400)


@router.get("/{user_id}/edit/", include_in_schema=False)
def user_edit_page(
    user_id: int,
    request: Request,
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示账户和现有授权的编辑表单。"""
    target = db_session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    profile = db_session.get(UserProfile, user_id)
    roles = set(
        db_session.scalars(
            select(profile_roles.c.role_id).where(profile_roles.c.user_id == user_id)
        ).all()
    )
    teams = set(
        db_session.scalars(
            select(team_members.c.team_id).where(team_members.c.user_id == user_id)
        ).all()
    )
    permissions = set(
        db_session.scalars(
            select(profile_permissions.c.permission_id).where(
                profile_permissions.c.user_id == user_id
            )
        ).all()
    )
    values = {
        "username": target.username,
        "email": target.email,
        "remark": profile.remark if profile else "",
        "is_superuser": target.is_superuser,
    }
    return _render(
        request,
        "rbac/user_form.html",
        admin,
        db_session,
        dict(
            _user_form_context(db_session, values, None, roles, teams, permissions),
            target=target,
        ),
    )


@router.post("/{user_id}/edit/", include_in_schema=False)
def user_edit(
    user_id: int,
    request: Request,
    username: str = Form(default=""),
    email: str = Form(default=""),
    remark: str = Form(default=""),
    password1: str = Form(default=""),
    password2: str = Form(default=""),
    is_superuser: bool = Form(default=False),
    role_ids: List[str] = Form(default=[]),
    team_ids: List[str] = Form(default=[]),
    permission_ids: List[str] = Form(default=[]),
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """校验并更新账户资料、密码与授权关系。"""
    target = db_session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    values = _parse_user_form(
        username,
        email,
        remark,
        role_ids,
        team_ids,
        permission_ids,
        password1,
        password2,
        is_superuser,
    )
    result = _persist_user(db_session, admin, target, values)
    if "errors" not in result:
        return _notice_redirect(
            request, "/users/", "用户 {} 更新成功".format(username.strip())
        )
    context = _user_form_context(
        db_session,
        values,
        result["errors"],
        result["roles"],
        result["teams"],
        result["permissions"],
    )
    context["target"] = target
    return _render(request, "rbac/user_form.html", admin, db_session, context, 400)


@router.get("/{user_id}/delete/", include_in_schema=False)
def user_delete_page(
    user_id: int,
    request: Request,
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示删除用户的确认页面。"""
    target = db_session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.id == admin.id:
        return _render(
            request,
            "rbac/confirm_delete.html",
            admin,
            db_session,
            {"target": target, "kind": "用户", "error": "不能删除当前登录用户。"},
            400,
        )
    return _render(
        request,
        "rbac/confirm_delete.html",
        admin,
        db_session,
        {"target": target, "kind": "用户"},
    )


@router.post("/{user_id}/delete/", include_in_schema=False)
def user_delete(
    user_id: int,
    request: Request,
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """删除指定用户并级联撤销其会话和授权关系。"""
    target = db_session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.id == admin.id:
        return _notice_redirect(request, "/users/", "不能删除当前登录用户")
    name = target.username
    db_session.delete(target)
    db_session.commit()
    return _notice_redirect(request, "/users/", "用户 {} 删除成功".format(name))


@router.post("/{user_id}/lock/", include_in_schema=False)
def user_lock_toggle(
    user_id: int,
    request: Request,
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """切换用户登录启用状态并在解锁时清除失败锁定。"""
    target = db_session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    lock = db_session.get(LoginFailureState, target.id)
    now = datetime.utcnow()
    temp_locked = bool(
        lock and lock.login_locked_until and lock.login_locked_until > now
    )
    enabled = target.is_active and not temp_locked
    if target.id == admin.id and enabled:
        return _notice_redirect(request, "/users/", "不能停用当前登录用户")
    if enabled:
        target.is_active = False
        message = "用户 {} 已停用".format(target.username)
    else:
        target.is_active = True
        clear_login_fail_lock(db_session, target.id)
        if temp_locked:
            write_audit_log(
                db_session,
                "用户管理",
                "解除登录锁定",
                "用户「{}」的登录失败锁定已清除".format(target.username),
            )
        message = "用户 {} 已启用".format(target.username)
    db_session.commit()
    return _notice_redirect(request, "/users/", message)


def _list_roles(db_session: Session, search: str, page: int, per_page: int) -> dict:
    """查询角色列表及权限、用户和用户组关联数量。"""
    page, per_page = _page_values(page, per_page)
    statement = select(Role)
    if search.strip():
        statement = statement.where(Role.name.ilike("%{}%".format(search.strip())))
    total = db_session.scalar(
        select(func.count()).select_from(statement.subquery())
    ) or 0
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)
    roles = db_session.scalars(
        statement.order_by(Role.created_at.desc(), Role.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    rows = []
    for role in roles:
        permission_count = db_session.scalar(
            select(func.count()).select_from(role_permissions).where(
                role_permissions.c.role_id == role.id
            )
        ) or 0
        user_count = db_session.scalar(
            select(func.count()).select_from(profile_roles).where(
                profile_roles.c.role_id == role.id
            )
        ) or 0
        team_count = db_session.scalar(
            select(func.count())
            .select_from(team_roles)
            .where(team_roles.c.role_id == role.id)
        ) or 0
        rows.append(
            {
                "role": role,
                "permission_count": permission_count,
                "user_count": user_count,
                "team_count": team_count,
            }
        )
    return {
        "roles": rows,
        "search": search,
        "pagination": {
            "page": page,
            "pages": pages,
            "total": total,
            "per_page": per_page,
            "per_page_options": _PAGE_SIZES,
        },
    }


@router.get("/roles/", include_in_schema=False)
@router.get("/groups/", include_in_schema=False)
def role_list(
    request: Request,
    search: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=10, ge=1),
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示角色列表，并兼容原项目角色别名路径。"""
    context = _list_roles(db_session, search, page, per_page)
    return _render(request, "rbac/roles_list.html", admin, db_session, context)


def _persist_role(
    db_session: Session,
    admin: User,
    role: Optional[Role],
    name: str,
    description: str,
    permission_ids_raw: List[str],
) -> dict:
    """校验角色数据并保存权限矩阵关联。"""
    name = name.strip()
    errors = {}
    if not name or len(name) > 100:
        errors["name"] = "角色名称不能为空且不能超过 100 个字符。"
    duplicate = db_session.scalars(select(Role.id).where(Role.name == name)).first()
    if duplicate is not None and (role is None or duplicate != role.id):
        errors["name"] = "角色名称已存在。"
    permission_ids = _relation_ids(db_session, PermissionItem, permission_ids_raw)
    if permission_ids is None:
        errors["permissions"] = "权限项已更新，请刷新后重试。"
        permission_ids = set()
    if errors:
        return {"errors": errors, "permissions": permission_ids}
    try:
        with db_session.begin_nested():
            saved = role or Role(name=name, created_by=admin.id)
            saved.name = name
            saved.description = description.strip()
            saved.updated_at = datetime.utcnow()
            if role is None:
                db_session.add(saved)
            db_session.flush()
            _replace_links(
                db_session,
                role_permissions,
                role_permissions.c.role_id,
                saved.id,
                role_permissions.c.permission_id,
                permission_ids,
            )
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
        return {"errors": {"name": "角色名称已存在。"}, "permissions": permission_ids}
    return {"role": saved, "permissions": permission_ids}


@router.get("/roles/create/", include_in_schema=False)
def role_create_page(
    request: Request,
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示新增角色及权限矩阵表单。"""
    return _render(
        request,
        "rbac/role_form.html",
        admin,
        db_session,
        _role_form_context(db_session),
    )


@router.post("/roles/create/", include_in_schema=False)
@router.post("/groups/create/", include_in_schema=False)
def role_create(
    request: Request,
    name: str = Form(default=""),
    description: str = Form(default=""),
    permission_ids: List[str] = Form(default=[]),
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """创建角色及其所选权限项。"""
    result = _persist_role(db_session, admin, None, name, description, permission_ids)
    if "errors" not in result:
        return _notice_redirect(
            request,
            "/users/roles/",
            "角色 {} 创建成功".format(name.strip()),
        )
    context = _role_form_context(
        db_session,
        errors=result["errors"],
        values={"name": name, "description": description},
        selected_permissions=result["permissions"],
    )
    return _render(request, "rbac/role_form.html", admin, db_session, context, 400)


@router.get("/roles/{role_id}/edit/", include_in_schema=False)
@router.get("/groups/{role_id}/edit/", include_in_schema=False)
def role_edit_page(
    role_id: int,
    request: Request,
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示角色详情和当前权限矩阵选择。"""
    role = db_session.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    return _render(
        request,
        "rbac/role_form.html",
        admin,
        db_session,
        _role_form_context(db_session, role),
    )


@router.post("/roles/{role_id}/edit/", include_in_schema=False)
@router.post("/groups/{role_id}/edit/", include_in_schema=False)
def role_edit(
    role_id: int,
    request: Request,
    name: str = Form(default=""),
    description: str = Form(default=""),
    permission_ids: List[str] = Form(default=[]),
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """保存角色名称、描述和权限项。"""
    role = db_session.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    result = _persist_role(db_session, admin, role, name, description, permission_ids)
    if "errors" not in result:
        return _notice_redirect(
            request,
            "/users/roles/",
            "角色 {} 更新成功".format(name.strip()),
        )
    context = _role_form_context(
        db_session,
        role,
        result["errors"],
        {"name": name, "description": description},
        result["permissions"],
    )
    return _render(request, "rbac/role_form.html", admin, db_session, context, 400)


@router.get("/roles/{role_id}/delete/", include_in_schema=False)
@router.get("/groups/{role_id}/delete/", include_in_schema=False)
def role_delete_page(
    role_id: int,
    request: Request,
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示删除角色的确认页面。"""
    role = db_session.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    return _render(
        request,
        "rbac/confirm_delete.html",
        admin,
        db_session,
        {"target": role, "kind": "角色"},
    )


@router.post("/roles/{role_id}/delete/", include_in_schema=False)
@router.post("/groups/{role_id}/delete/", include_in_schema=False)
def role_delete(
    role_id: int,
    request: Request,
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """删除角色并级联清除用户和用户组中的该角色关系。"""
    role = db_session.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    name = role.name
    db_session.delete(role)
    db_session.commit()
    return _notice_redirect(request, "/users/roles/", "角色 {} 删除成功".format(name))


@router.get("/roles/{role_id}/manage-users/", include_in_schema=False)
@router.get("/groups/{role_id}/manage-users/", include_in_schema=False)
def role_manage_users_page(
    role_id: int,
    request: Request,
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示角色成员选择页。"""
    role = db_session.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    users = db_session.scalars(select(User).order_by(User.username)).all()
    selected = set(
        db_session.scalars(
            select(profile_roles.c.user_id).where(profile_roles.c.role_id == role_id)
        ).all()
    )
    return _render(
        request,
        "rbac/role_members.html",
        admin,
        db_session,
        {"role": role, "users": users, "selected_users": selected},
    )


@router.post("/roles/{role_id}/manage-users/", include_in_schema=False)
@router.post("/groups/{role_id}/manage-users/", include_in_schema=False)
def role_manage_users(
    role_id: int,
    request: Request,
    user_ids: List[str] = Form(default=[]),
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """按角色成员选择同步个人角色关联并遵守每人三角色上限。"""
    role = db_session.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    desired = _relation_ids(db_session, User, user_ids)
    if desired is None:
        return _notice_redirect(request, "/users/roles/", "所选用户已失效，请刷新后重试")
    current = set(
        db_session.scalars(
            select(profile_roles.c.user_id).where(profile_roles.c.role_id == role_id)
        ).all()
    )
    skipped = 0
    with db_session.begin_nested():
        for user_id in sorted(desired - current):
            count = db_session.scalar(
                select(func.count()).select_from(profile_roles).where(
                    profile_roles.c.user_id == user_id
                )
            ) or 0
            if count >= 3:
                skipped += 1
                continue
            db_session.execute(
                profile_roles.insert().values(user_id=user_id, role_id=role_id)
            )
        for user_id in current - desired:
            db_session.execute(
                delete(profile_roles).where(
                    profile_roles.c.user_id == user_id,
                    profile_roles.c.role_id == role_id,
                )
            )
    message = "角色 {} 成员已更新".format(role.name)
    if skipped:
        message += "，跳过 {} 个已达角色上限的用户".format(skipped)
    changed_count = len(current - desired) + len(desired - current) - skipped
    if changed_count:
        write_audit_log(
            db_session,
            "角色管理",
            "更新角色成员",
            "角色「{}」成员关系已更新，变更 {} 个用户".format(
                role.name, changed_count
            ),
        )
    db_session.commit()
    return _notice_redirect(request, "/users/roles/", message)


def _list_teams(db_session: Session, search: str, page: int, per_page: int) -> dict:
    """查询用户组列表和成员、角色关联统计。"""
    page, per_page = _page_values(page, per_page)
    statement = select(Team)
    search_terms = [
        item.strip()
        for item in search.replace("，", ",").split(",")
        if item.strip()
    ]
    for term in search_terms:
        statement = statement.where(Team.name.ilike("%{}%".format(term)))
    total = db_session.scalar(
        select(func.count()).select_from(statement.subquery())
    ) or 0
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)
    teams = db_session.scalars(
        statement.order_by(Team.created_at.desc(), Team.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    rows = []
    for team in teams:
        member_count = db_session.scalar(
            select(func.count()).select_from(team_members).where(
                team_members.c.team_id == team.id
            )
        ) or 0
        role_names = db_session.scalars(
            select(Role.name)
            .join(team_roles, team_roles.c.role_id == Role.id)
            .where(team_roles.c.team_id == team.id)
            .order_by(Role.name)
        ).all()
        rows.append(
            {"team": team, "member_count": member_count, "role_names": role_names}
        )
    return {
        "teams": rows,
        "search": search,
        "pagination": {
            "page": page,
            "pages": pages,
            "total": total,
            "per_page": per_page,
            "per_page_options": _PAGE_SIZES,
        },
    }


@router.get("/teams/", include_in_schema=False)
def team_list(
    request: Request,
    search: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=10, ge=1),
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示支持名称标签搜索的用户组列表。"""
    context = _list_teams(db_session, search, page, per_page)
    return _render(request, "rbac/teams_list.html", admin, db_session, context)


def _persist_team(
    db_session: Session,
    admin: User,
    team: Optional[Team],
    name: str,
    description: str,
    role_ids_raw: List[str],
) -> dict:
    """校验用户组字段并保存关联角色。"""
    name = name.strip()
    errors = {}
    if not name or len(name) > 100:
        errors["name"] = "用户组名称不能为空且不能超过 100 个字符。"
    duplicate = db_session.scalars(select(Team.id).where(Team.name == name)).first()
    if duplicate is not None and (team is None or duplicate != team.id):
        errors["name"] = "用户组已存在。"
    role_ids = _relation_ids(db_session, Role, role_ids_raw)
    if role_ids is None:
        errors["roles"] = "所选角色已失效，请刷新后重试。"
        role_ids = set()
    if errors:
        return {"errors": errors, "roles": role_ids}
    try:
        with db_session.begin_nested():
            saved = team or Team(name=name, created_by=admin.id)
            saved.name = name
            saved.description = description.strip()
            saved.updated_at = datetime.utcnow()
            if team is None:
                db_session.add(saved)
            db_session.flush()
            _replace_links(
                db_session,
                team_roles,
                team_roles.c.team_id,
                saved.id,
                team_roles.c.role_id,
                role_ids,
            )
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
        return {"errors": {"name": "用户组已存在。"}, "roles": role_ids}
    return {"team": saved, "roles": role_ids}


@router.get("/teams/create/", include_in_schema=False)
def team_create_page(
    request: Request,
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示新建用户组表单。"""
    return _render(
        request,
        "rbac/team_form.html",
        admin,
        db_session,
        _team_form_context(db_session),
    )


@router.post("/teams/create/", include_in_schema=False)
def team_create(
    request: Request,
    name: str = Form(default=""),
    description: str = Form(default=""),
    role_ids: List[str] = Form(default=[]),
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """创建用户组并关联所选角色。"""
    result = _persist_team(db_session, admin, None, name, description, role_ids)
    if "errors" not in result:
        return _notice_redirect(
            request,
            "/users/teams/",
            "用户组 {} 创建成功".format(name.strip()),
        )
    context = _team_form_context(
        db_session,
        values={"name": name, "description": description},
        errors=result["errors"],
        selected_roles=result["roles"],
    )
    return _render(request, "rbac/team_form.html", admin, db_session, context, 400)


@router.get("/teams/{team_id}/edit/", include_in_schema=False)
def team_edit_page(
    team_id: int,
    request: Request,
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示用户组基本信息和关联角色。"""
    team = db_session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="用户组不存在")
    return _render(
        request,
        "rbac/team_form.html",
        admin,
        db_session,
        _team_form_context(db_session, team),
    )


@router.post("/teams/{team_id}/edit/", include_in_schema=False)
def team_edit(
    team_id: int,
    request: Request,
    name: str = Form(default=""),
    description: str = Form(default=""),
    role_ids: List[str] = Form(default=[]),
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """保存用户组名称、描述和角色继承关系。"""
    team = db_session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="用户组不存在")
    result = _persist_team(db_session, admin, team, name, description, role_ids)
    if "errors" not in result:
        return _notice_redirect(
            request,
            "/users/teams/",
            "用户组 {} 更新成功".format(name.strip()),
        )
    context = _team_form_context(
        db_session,
        team,
        {"name": name, "description": description},
        result["errors"],
        result["roles"],
    )
    return _render(request, "rbac/team_form.html", admin, db_session, context, 400)


@router.get("/teams/{team_id}/delete/", include_in_schema=False)
def team_delete_page(
    team_id: int,
    request: Request,
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示删除用户组的确认页面。"""
    team = db_session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="用户组不存在")
    return _render(
        request,
        "rbac/confirm_delete.html",
        admin,
        db_session,
        {"target": team, "kind": "用户组", "details": "成员账户不会被删除。"},
    )


@router.post("/teams/{team_id}/delete/", include_in_schema=False)
def team_delete(
    team_id: int,
    request: Request,
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> Response:
    """删除用户组并保留其成员用户。"""
    team = db_session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="用户组不存在")
    name = team.name
    db_session.delete(team)
    db_session.commit()
    return _notice_redirect(request, "/users/teams/", "用户组 {} 删除成功".format(name))


def _member_search_terms(search: str) -> List[str]:
    """拆分逗号和中文逗号分隔的用户搜索标签。"""
    return [
        item.strip()
        for item in search.replace("，", ",").split(",")
        if item.strip()
    ]


@api_router.get(
    "/{team_id}/members",
    response_model=TeamMembersResponse,
    tags=["users"],
    summary="查询用户组成员管理列表",
    responses=api_error_responses((401, 403, 404, 422, 500)),
)
def team_members_api(
    team_id: int,
    page: int = Query(default=1, ge=1),
    search: str = Query(default="", max_length=500),
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> TeamMembersResponse:
    """按用户名和邮箱标签分页查询用户组成员状态。"""
    team = db_session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="用户组不存在")
    statement = select(User)
    for term in _member_search_terms(search):
        pattern = "%{}%".format(term)
        statement = statement.where(
            or_(User.username.ilike(pattern), User.email.ilike(pattern))
        )
    total = db_session.scalar(
        select(func.count()).select_from(statement.subquery())
    ) or 0
    total_pages = max(1, math.ceil(total / 10))
    page = min(page, total_pages)
    users = db_session.scalars(
        statement.order_by(User.username).offset((page - 1) * 10).limit(10)
    ).all()
    member_ids = set(
        db_session.scalars(
            select(team_members.c.user_id).where(team_members.c.team_id == team_id)
        ).all()
    )
    items = []
    for user in users:
        role_count = db_session.scalar(
            select(func.count()).select_from(profile_roles).where(
                profile_roles.c.user_id == user.id
            )
        ) or 0
        items.append(
            TeamMemberItem(
                id=user.id,
                username=user.username,
                email=user.email or "-",
                is_active=user.is_active,
                is_superuser=user.is_superuser,
                is_member=user.id in member_ids,
                role_count=role_count,
            )
        )
    return TeamMembersResponse(
        users=items,
        page=page,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_previous=page > 1,
        total_count=total,
    )


@api_router.post(
    "/{team_id}/members",
    response_model=TeamMemberUpdateResponse,
    tags=["users"],
    summary="增删用户组成员",
    responses=api_error_responses((400, 401, 403, 404, 422, 500)),
)
def update_team_members_api(
    team_id: int,
    payload: TeamMemberUpdateRequest,
    admin: User = Depends(require_superuser),
    db_session: Session = Depends(get_session),
) -> TeamMemberUpdateResponse:
    """在一个事务中为用户组增加或移除一批账户。"""
    team = db_session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="用户组不存在")
    user_ids = set(payload.user_ids)
    existing = set(
        db_session.scalars(select(User.id).where(User.id.in_(user_ids))).all()
    )
    if existing != user_ids:
        raise HTTPException(status_code=400, detail="部分用户不存在")
    current = set(
        db_session.scalars(
            select(team_members.c.user_id).where(team_members.c.team_id == team_id)
        ).all()
    )
    if payload.action == "add":
        changed = user_ids - current
        if changed:
            db_session.execute(
                team_members.insert(),
                [
                    {"team_id": team_id, "user_id": user_id}
                    for user_id in sorted(changed)
                ],
            )
        message = "已将 {} 个用户加入用户组".format(len(changed))
    else:
        changed = user_ids & current
        if changed:
            db_session.execute(
                delete(team_members).where(
                    team_members.c.team_id == team_id,
                    team_members.c.user_id.in_(changed),
                )
            )
        message = "已将 {} 个用户移出用户组".format(len(changed))
    if changed:
        action_label = "添加用户组成员" if payload.action == "add" else "移除用户组成员"
        write_audit_log(
            db_session,
            "用户组管理",
            action_label,
            "用户组「{}」成员关系已更新，变更 {} 个用户".format(
                team.name, len(changed)
            ),
        )
    db_session.commit()
    return TeamMemberUpdateResponse(message=message, changed_count=len(changed))
