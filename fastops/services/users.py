"""用户认证与权限查询服务。"""

from dataclasses import dataclass
from typing import FrozenSet, Optional

from fastops.core.security import verify_django_password
from fastops.db.sqlite import connect, table_exists


@dataclass(frozen=True)
class FastUser:
    """FastAPI 侧最小用户对象。"""

    id: int
    username: str
    is_superuser: bool


def get_user_by_id(user_id: int) -> Optional[FastUser]:
    """按 ID 查询有效用户。"""
    with connect() as conn:
        if not table_exists(conn, "auth_user"):
            return None
        row = conn.execute(
            """
            select id, username, is_superuser
            from auth_user
            where id = ? and is_active = 1
            """,
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return FastUser(
        id=int(row["id"]),
        username=row["username"],
        is_superuser=bool(row["is_superuser"]),
    )


def authenticate_user(username: str, password: str) -> Optional[FastUser]:
    """校验用户名密码并返回 FastAPI 用户。"""
    with connect() as conn:
        if not table_exists(conn, "auth_user"):
            return None
        row = conn.execute(
            """
            select id, username, password, is_superuser
            from auth_user
            where username = ? and is_active = 1
            """,
            ((username or "").strip(),),
        ).fetchone()
    if not row or not verify_django_password(password or "", row["password"]):
        return None
    return FastUser(
        id=int(row["id"]),
        username=row["username"],
        is_superuser=bool(row["is_superuser"]),
    )


def _permission_tables_exist(conn) -> bool:
    """判断权限相关表是否齐备。"""
    return all(
        table_exists(conn, name)
        for name in (
            "users_permissionitem",
            "users_usergroup_permissions",
            "users_userprofile",
            "users_userprofile_direct_permissions",
            "users_userprofile_groups",
            "users_userteam_members",
            "users_userteam_roles",
        )
    )


def user_permission_codes(user: FastUser) -> FrozenSet[str]:
    """读取用户拥有的权限编码集合。"""
    if user.is_superuser:
        return frozenset({"*"})
    with connect() as conn:
        if not _permission_tables_exist(conn):
            return frozenset()

        direct_rows = conn.execute(
            """
            select pi.code
            from users_userprofile p
            join users_userprofile_direct_permissions dp
              on dp.userprofile_id = p.id
            join users_permissionitem pi
              on pi.id = dp.permissionitem_id
            where p.user_id = ?
            """,
            (user.id,),
        ).fetchall()
        codes = {row["code"] for row in direct_rows}

        # 与 Django 侧保持一致：个人角色存在时优先使用个人角色。
        personal_rows = conn.execute(
            """
            select pi.code
            from users_userprofile p
            join users_userprofile_groups pg on pg.userprofile_id = p.id
            join users_usergroup_permissions rp on rp.usergroup_id = pg.usergroup_id
            join users_permissionitem pi on pi.id = rp.permissionitem_id
            where p.user_id = ?
            """,
            (user.id,),
        ).fetchall()
        codes.update(row["code"] for row in personal_rows)

        personal_role_row = conn.execute(
            """
            select 1
            from users_userprofile p
            join users_userprofile_groups pg on pg.userprofile_id = p.id
            where p.user_id = ?
            limit 1
            """,
            (user.id,),
        ).fetchone()
        if personal_role_row:
            return frozenset(codes)

        team_rows = conn.execute(
            """
            select pi.code
            from users_userteam_members tm
            join users_userteam_roles tr on tr.userteam_id = tm.userteam_id
            join users_usergroup_permissions rp
              on rp.usergroup_id = tr.usergroup_id
            join users_permissionitem pi on pi.id = rp.permissionitem_id
            where tm.user_id = ?
            """,
            (user.id,),
        ).fetchall()
        codes.update(row["code"] for row in team_rows)
    return frozenset(codes)


def user_has_permission(user: FastUser, resource: str, action: str) -> bool:
    """判断用户是否拥有指定资源动作权限。"""
    if user.is_superuser:
        return True
    return f"{resource}.{action}" in user_permission_codes(user)


def task_center_limited_ops_for_user(user: FastUser) -> FrozenSet[str]:
    """按节点/运维权限汇总可见的本人任务类型。"""
    codes = user_permission_codes(user)
    if "*" in codes:
        return frozenset()
    ops = []
    if "nodes.ssh_test" in codes:
        ops.extend(["node_ssh_test", "node_batch_test"])
    if "credentials.enable" in codes:
        ops.append("credential_enable_test")
    if "configs.sync" in codes:
        ops.append("config_batch_sync")
    if "nginx_service.operate" in codes:
        ops.append("nginx_service_control")
    if "upgrade.execute" in codes:
        ops.append("nginx_install")
    if "nginx_uninstall.execute" in codes:
        ops.append("nginx_uninstall")
    return frozenset(ops)


def user_can_access_limited_task_center(user: FastUser) -> bool:
    """判断用户是否可通过节点/运维权限访问任务中心。"""
    return bool(task_center_limited_ops_for_user(user))
