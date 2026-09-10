"""users 视图层测试（用户管理 CRUD）"""

import pytest
from django.urls import reverse
from django.contrib.auth import get_user_model


User = get_user_model()


@pytest.mark.django_db
class TestUserListView:
    """用户列表"""

    def test_list_accessible(self, admin_client):
        resp = admin_client.get(reverse("users:list"))
        assert resp.status_code == 200

    def test_list_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("users:list"))
        assert resp.status_code == 302

    def test_list_shows_users(self, admin_client, admin_user, normal_user):
        resp = admin_client.get(reverse("users:list"))
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "admin" in content
        assert "viewer" in content

    def test_list_search(self, admin_client, normal_user):
        resp = admin_client.get(reverse("users:list"), {"search": "viewer"})
        assert resp.status_code == 200
        users = list(resp.context["users"])
        assert len(users) == 1
        assert users[0].username == "viewer"


@pytest.mark.django_db
class TestUserCreateView:
    """用户创建"""

    def test_create_page_accessible(self, admin_client):
        resp = admin_client.get(reverse("users:create"))
        assert resp.status_code == 200

    def test_create_user_success(self, admin_client):
        resp = admin_client.post(
            reverse("users:create"),
            {
                "username": "newuser",
                "email": "newuser@example.com",
                "password1": "ComplexPass@123!",
                "password2": "ComplexPass@123!",
            },
        )
        assert resp.status_code == 302
        assert User.objects.filter(username="newuser").exists()

    def test_create_user_duplicate_username(self, admin_client, normal_user):
        resp = admin_client.post(
            reverse("users:create"),
            {
                "username": "viewer",
                "email": "viewer2@example.com",
                "password1": "ComplexPass@123!",
                "password2": "ComplexPass@123!",
            },
        )
        assert resp.status_code == 200

    def test_create_user_password_mismatch(self, admin_client):
        resp = admin_client.post(
            reverse("users:create"),
            {
                "username": "newuser",
                "email": "newuser@example.com",
                "password1": "ComplexPass@123!",
                "password2": "DifferentPass@123!",
            },
        )
        assert resp.status_code == 200
        assert not User.objects.filter(username="newuser").exists()


@pytest.mark.django_db
class TestUserUpdateView:
    """用户编辑"""

    def test_edit_page_accessible(self, admin_client, normal_user):
        resp = admin_client.get(reverse("users:edit", args=[normal_user.id]))
        assert resp.status_code == 200

    def test_edit_user_email(self, admin_client, normal_user):
        resp = admin_client.post(
            reverse("users:edit", args=[normal_user.id]),
            {
                "username": "viewer",
                "email": "newemail@example.com",
            },
        )
        assert resp.status_code == 302
        normal_user.refresh_from_db()
        assert normal_user.email == "newemail@example.com"


@pytest.mark.django_db
class TestUserDeleteView:
    """用户删除"""

    def test_delete_page_accessible(self, admin_client, normal_user):
        resp = admin_client.get(reverse("users:delete", args=[normal_user.id]))
        assert resp.status_code == 200

    def test_delete_user_success(self, admin_client, normal_user):
        resp = admin_client.post(reverse("users:delete", args=[normal_user.id]))
        assert resp.status_code == 302
        assert not User.objects.filter(username="viewer").exists()


@pytest.mark.django_db
class TestUserLockToggleView:
    """用户锁定/解锁"""

    def test_lock_user(self, admin_client, normal_user):
        resp = admin_client.post(reverse("users:lock_toggle", args=[normal_user.id]))
        assert resp.status_code == 302
        normal_user.refresh_from_db()
        assert normal_user.is_active is False

    def test_unlock_user(self, admin_client, normal_user):
        normal_user.is_active = False
        normal_user.save()
        resp = admin_client.post(reverse("users:lock_toggle", args=[normal_user.id]))
        assert resp.status_code == 302
        normal_user.refresh_from_db()
        assert normal_user.is_active is True


@pytest.mark.django_db
class TestUserGroupListView:
    """用户组列表"""

    def test_list_accessible(self, admin_client):
        resp = admin_client.get(reverse("users:group_list"))
        assert resp.status_code == 200

    def test_list_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("users:group_list"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestUserGroupCreateView:
    """用户组创建"""

    def test_create_page_accessible(self, admin_client):
        resp = admin_client.get(reverse("users:group_create"))
        assert resp.status_code == 200

    def test_create_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("users:group_create"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestUserGroupUpdateView:
    """用户组编辑"""

    def test_edit_not_found(self, admin_client):
        resp = admin_client.get(reverse("users:group_edit", args=[99999]))
        assert resp.status_code == 404


@pytest.mark.django_db
class TestUserGroupDeleteView:
    """用户组删除"""

    def test_delete_not_found(self, admin_client):
        resp = admin_client.get(reverse("users:group_delete", args=[99999]))
        assert resp.status_code == 404


@pytest.mark.django_db
class TestUserGroupManageUsersView:
    """用户组管理用户"""

    def test_manage_users_not_found(self, admin_client):
        resp = admin_client.get(reverse("users:group_manage_users", args=[99999]))
        assert resp.status_code in (403, 404, 405)


@pytest.mark.django_db
class TestRoleListView:
    """角色组列表"""

    def test_list_accessible(self, admin_client):
        resp = admin_client.get(reverse("users:role_list"))
        assert resp.status_code == 200

    def test_list_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("users:role_list"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestRoleCreateView:
    """角色组创建"""

    def test_create_page_accessible(self, admin_client):
        resp = admin_client.get(reverse("users:role_create"))
        assert resp.status_code == 200

    def test_create_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("users:role_create"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestRoleUpdateView:
    """角色组编辑"""

    def test_edit_not_found(self, admin_client):
        resp = admin_client.get(reverse("users:role_edit", args=[99999]))
        assert resp.status_code == 404


@pytest.mark.django_db
class TestRoleDeleteView:
    """角色组删除"""

    def test_delete_not_found(self, admin_client):
        resp = admin_client.get(reverse("users:role_delete", args=[99999]))
        assert resp.status_code == 404


@pytest.mark.django_db
class TestRoleManageUsersView:
    """角色组管理用户"""

    def test_manage_users_not_found(self, admin_client):
        resp = admin_client.get(reverse("users:role_manage_users", args=[99999]))
        assert resp.status_code in (403, 404, 405)


@pytest.mark.django_db
class TestUserTeamListView:
    """团队列表"""

    def test_list_accessible(self, admin_client):
        resp = admin_client.get(reverse("users:team_list"))
        assert resp.status_code == 200

    def test_list_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("users:team_list"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestUserTeamCreateView:
    """团队创建"""

    def test_create_page_accessible(self, admin_client):
        resp = admin_client.get(reverse("users:team_create"))
        assert resp.status_code == 200

    def test_create_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("users:team_create"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestUserTeamUpdateView:
    """团队编辑"""

    def test_edit_not_found(self, admin_client):
        resp = admin_client.get(reverse("users:team_edit", args=[99999]))
        assert resp.status_code == 404


@pytest.mark.django_db
class TestUserTeamDeleteView:
    """团队删除"""

    def test_delete_not_found(self, admin_client):
        resp = admin_client.get(reverse("users:team_delete", args=[99999]))
        assert resp.status_code == 404


@pytest.mark.django_db
class TestUserTeamMemberListView:
    """团队成员列表"""

    def test_members_not_found(self, admin_client):
        resp = admin_client.get(reverse("users:team_members", args=[99999]))
        assert resp.status_code == 404


@pytest.mark.django_db
class TestUserTeamManageMembersView:
    """团队管理成员"""

    def test_manage_members_not_found(self, admin_client):
        resp = admin_client.get(reverse("users:team_manage_members", args=[99999]))
        assert resp.status_code in (403, 404, 405)
