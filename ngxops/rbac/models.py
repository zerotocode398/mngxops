"""映射 RBAC 权限项、角色、用户组和用户授权关系。"""

from datetime import datetime
from typing import List

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ngxops.accounts.models import User
from ngxops.database.base import Base


role_permissions = Table(
    "ngxops_role_permissions",
    Base.metadata,
    Column(
        "role_id",
        ForeignKey("ngxops_roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        ForeignKey("ngxops_permission_items.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)
team_members = Table(
    "ngxops_team_members",
    Base.metadata,
    Column(
        "team_id",
        ForeignKey("ngxops_user_teams.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("user_id", ForeignKey("auth_user.id", ondelete="CASCADE"), primary_key=True),
)
team_roles = Table(
    "ngxops_team_roles",
    Base.metadata,
    Column(
        "team_id",
        ForeignKey("ngxops_user_teams.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        ForeignKey("ngxops_roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)
profile_roles = Table(
    "ngxops_profile_roles",
    Base.metadata,
    Column("user_id", ForeignKey("auth_user.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "role_id",
        ForeignKey("ngxops_roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)
profile_permissions = Table(
    "ngxops_profile_permissions",
    Base.metadata,
    Column("user_id", ForeignKey("auth_user.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "permission_id",
        ForeignKey("ngxops_permission_items.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class PermissionItem(Base):
    """描述一个可在角色或用户上授予的资源动作权限。"""

    __tablename__ = "ngxops_permission_items"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    resource: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)


class Role(Base):
    """保存角色名称、描述和授予的权限项。"""

    __tablename__ = "ngxops_roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("auth_user.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    creator: Mapped[User] = relationship()
    permissions: Mapped[List[PermissionItem]] = relationship(secondary=role_permissions)


class Team(Base):
    """保存用户组成员和角色继承关系。"""

    __tablename__ = "ngxops_user_teams"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("auth_user.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    creator: Mapped[User] = relationship()
    members: Mapped[List[User]] = relationship(secondary=team_members)
    roles: Mapped[List[Role]] = relationship(secondary=team_roles)


class UserProfile(Base):
    """保存用户备注、个人角色和直授权限。"""

    __tablename__ = "ngxops_user_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("auth_user.id", ondelete="CASCADE"), primary_key=True
    )
    remark: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    user: Mapped[User] = relationship()
    roles: Mapped[List[Role]] = relationship(
        secondary=profile_roles,
        primaryjoin=lambda: UserProfile.user_id == profile_roles.c.user_id,
        secondaryjoin=lambda: Role.id == profile_roles.c.role_id,
    )
    direct_permissions: Mapped[List[PermissionItem]] = relationship(
        secondary=profile_permissions,
        primaryjoin=lambda: UserProfile.user_id == profile_permissions.c.user_id,
        secondaryjoin=lambda: PermissionItem.id == profile_permissions.c.permission_id,
    )
