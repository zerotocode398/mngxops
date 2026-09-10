"""users 模块单元测试（权限编码、角色/用户组模型、权限校验）"""

import pytest

from apps.users.perm_defs import (
    permission_code,
    all_permission_items,
    RESOURCE_CHOICES,
    ACTION_CHOICES,
    PERM_DISPLAY_NAMES,
)
from apps.users.models import PermissionItem, UserGroup, UserTeam, UserProfile
from apps.users.permissions import (
    user_has_permission,
    _get_user_role_ids,
    task_center_limited_ops_for_user,
)


class TestPermissionCode:
    """permission_code 权限编码生成"""

    def test_basic_code(self):
        assert permission_code("nodes", "read") == "nodes.read"

    def test_code_with_all_resources_and_actions(self):
        for resource, _ in RESOURCE_CHOICES:
            for action, _ in ACTION_CHOICES:
                expected = f"{resource}.{action}"
                assert permission_code(resource, action) == expected


class TestAllPermissionItems:
    """all_permission_items 权限项列表"""

    def test_returns_list(self):
        items = all_permission_items()
        assert isinstance(items, list)
        assert len(items) > 0

    def test_each_item_has_required_fields(self):
        items = all_permission_items()
        for item in items:
            assert "code" in item
            assert "name" in item
            assert "resource" in item
            assert "action" in item

    def test_all_resources_covered(self):
        items = all_permission_items()
        resources = {item["resource"] for item in items}
        for resource, _ in RESOURCE_CHOICES:
            if resource in PERM_DISPLAY_NAMES:
                assert resource in resources, f"Missing: {resource}"


@pytest.mark.django_db
class TestPermissionItemModel:
    """PermissionItem 模型"""

    def test_create(self):
        perm = PermissionItem.objects.create(
            code="users.tests.create",
            name="测试创建权限",
            resource="users",
            action="create",
        )
        assert perm.code == "users.tests.create"
        assert str(perm) == "测试创建权限"

    def test_ordering(self):
        PermissionItem.objects.create(
            code="users.tests.zzz", name="Z", resource="users", action="update"
        )
        PermissionItem.objects.create(
            code="users.tests.aaa", name="A", resource="users", action="read"
        )
        items = list(PermissionItem.objects.filter(code__startswith="users.tests."))
        assert items[0].code == "users.tests.aaa"


@pytest.mark.django_db
class TestUserGroupModel:
    """UserGroup（角色）模型"""

    def test_create(self, admin_user):
        group = UserGroup.objects.create(
            name="运维角色",
            description="测试角色",
            created_by=admin_user,
        )
        assert group.name == "运维角色"
        assert str(group) == "运维角色"

    def test_add_permissions(self, admin_user):
        group = UserGroup.objects.create(name="测试角色", created_by=admin_user)
        perm = PermissionItem.objects.create(
            code="users.tests.perm", name="测试权限", resource="users", action="create"
        )
        group.permissions.add(perm)
        assert group.permissions.count() == 1
        assert group.permissions.first().code == "users.tests.perm"


@pytest.mark.django_db
class TestUserTeamModel:
    """UserTeam（用户组）模型"""

    def test_create(self, admin_user):
        team = UserTeam.objects.create(
            name="运维组",
            description="运维团队",
            created_by=admin_user,
        )
        assert team.name == "运维组"
        assert str(team) == "运维组"
        assert team.member_count == 0

    def test_add_members(self, admin_user, normal_user):
        team = UserTeam.objects.create(name="运维组", created_by=admin_user)
        team.members.add(normal_user)
        assert team.member_count == 1


@pytest.mark.django_db
class TestUserProfileModel:
    """UserProfile 模型"""

    def test_auto_created(self, normal_user):
        from apps.accounts.login_lock import get_or_create_profile

        profile = get_or_create_profile(normal_user)
        assert profile is not None
        assert profile.failed_login_count == 0

    def test_str(self, normal_user):
        from apps.accounts.login_lock import get_or_create_profile

        profile = get_or_create_profile(normal_user)
        assert normal_user.username in str(profile)


@pytest.mark.django_db
class TestUserHasPermission:
    """user_has_permission 权限校验"""

    def test_superuser_has_all(self, admin_user):
        assert user_has_permission(admin_user, "nodes", "read")
        assert user_has_permission(admin_user, "configs", "delete")
        assert user_has_permission(admin_user, "nonexistent", "nonexistent")

    def test_unauthenticated_has_none(self, anonymous_client):
        from django.contrib.auth.models import AnonymousUser

        user = AnonymousUser()
        assert not user_has_permission(user, "nodes", "read")

    def test_normal_user_no_permission(self, normal_user):
        assert not user_has_permission(normal_user, "nodes", "read")

    def test_user_with_direct_permission(self, normal_user):
        from apps.accounts.login_lock import get_or_create_profile

        profile = get_or_create_profile(normal_user)
        perm, _ = PermissionItem.objects.get_or_create(
            code="nodes.read",
            defaults={"name": "节点查看", "resource": "nodes", "action": "read"},
        )
        profile.direct_permissions.add(perm)
        assert user_has_permission(normal_user, "nodes", "read")
        assert not user_has_permission(normal_user, "nodes", "update")

    def test_user_with_role_permission(self, admin_user, normal_user):
        from apps.accounts.login_lock import get_or_create_profile

        perm, _ = PermissionItem.objects.get_or_create(
            code="nodes.read",
            defaults={"name": "节点查看", "resource": "nodes", "action": "read"},
        )
        role = UserGroup.objects.create(name="查看角色X", created_by=admin_user)
        role.permissions.add(perm)
        profile = get_or_create_profile(normal_user)
        profile.groups.add(role)
        assert user_has_permission(normal_user, "nodes", "read")


@pytest.mark.django_db
class TestGetUserRoleIds:
    """_get_user_role_ids 角色 ID 获取"""

    def test_no_roles(self, normal_user):
        assert _get_user_role_ids(normal_user) == set()

    def test_personal_role(self, admin_user, normal_user):
        from apps.accounts.login_lock import get_or_create_profile

        role = UserGroup.objects.create(name="角色A", created_by=admin_user)
        profile = get_or_create_profile(normal_user)
        profile.groups.add(role)
        role_ids = _get_user_role_ids(normal_user)
        assert role.id in role_ids

    def test_personal_priority_over_team(self, admin_user, normal_user):
        from apps.accounts.login_lock import get_or_create_profile

        personal_role = UserGroup.objects.create(name="个人角色", created_by=admin_user)
        team_role = UserGroup.objects.create(name="团队角色", created_by=admin_user)
        team = UserTeam.objects.create(name="团队", created_by=admin_user)
        team.roles.add(team_role)
        team.members.add(normal_user)
        profile = get_or_create_profile(normal_user)
        profile.groups.add(personal_role)
        role_ids = _get_user_role_ids(normal_user)
        assert personal_role.id in role_ids
        assert team_role.id not in role_ids


@pytest.mark.django_db
class TestTaskCenterLimitedOps:
    """task_center_limited_ops_for_user 权限汇总"""

    def test_user_with_no_perms(self, normal_user):
        ops = task_center_limited_ops_for_user(normal_user)
        assert ops == []

    def test_user_with_node_ssh_test(self, normal_user):
        from apps.accounts.login_lock import get_or_create_profile

        profile = get_or_create_profile(normal_user)
        perm, _ = PermissionItem.objects.get_or_create(
            code="nodes.ssh_test",
            defaults={"name": "SSH测试", "resource": "nodes", "action": "ssh_test"},
        )
        profile.direct_permissions.add(perm)
        ops = task_center_limited_ops_for_user(normal_user)
        assert "node_ssh_test" in ops
        assert "node_batch_test" in ops
