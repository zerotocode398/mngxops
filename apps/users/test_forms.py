"""用户模块表单测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.users.forms import (
    UserGroupForm,
    UserCreateForm,
    UserTeamForm,
    UserUpdateForm,
    validate_ascii_username,
)
from apps.users.models import UserGroup, UserTeam, PermissionItem


class TestValidateAsciiUsername(TestCase):
    def test_valid_username(self):
        self.assertEqual(validate_ascii_username("test_user"), "test_user")
        self.assertEqual(validate_ascii_username("user-01"), "user-01")

    def test_invalid_chinese(self):
        from django import forms

        with self.assertRaises(forms.ValidationError):
            validate_ascii_username("中文用户")

    def test_invalid_empty(self):
        from django import forms

        with self.assertRaises(forms.ValidationError):
            validate_ascii_username("")


class TestUserGroupForm(TestCase):
    def test_valid_form(self):
        form = UserGroupForm(data={"name": "运维组", "description": "运维工程师组"})
        self.assertTrue(form.is_valid(), form.errors)

    def test_empty_name(self):
        form = UserGroupForm(data={"name": "", "description": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_form_fields(self):
        form = UserGroupForm()
        self.assertIn("name", form.fields)
        self.assertIn("description", form.fields)
        self.assertIn("permissions", form.fields)


class TestUserCreateForm(TestCase):
    def test_valid_form(self):
        form = UserCreateForm(
            data={
                "username": "newuser",
                "email": "newuser@example.com",
                "password1": "ComplexPass123!",
                "password2": "ComplexPass123!",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_empty_username(self):
        form = UserCreateForm(
            data={
                "username": "",
                "email": "test@example.com",
                "password1": "ComplexPass123!",
                "password2": "ComplexPass123!",
            }
        )
        self.assertFalse(form.is_valid())

    def test_password_mismatch(self):
        form = UserCreateForm(
            data={
                "username": "newuser",
                "email": "newuser@example.com",
                "password1": "ComplexPass123!",
                "password2": "DifferentPass123!",
            }
        )
        self.assertFalse(form.is_valid())

    def test_form_fields(self):
        form = UserCreateForm()
        self.assertIn("username", form.fields)
        self.assertIn("email", form.fields)
        self.assertIn("password1", form.fields)
        self.assertIn("password2", form.fields)


class TestUserTeamForm(TestCase):
    def test_valid_form(self):
        form = UserTeamForm(data={"name": "后端团队", "description": "后端开发团队"})
        self.assertTrue(form.is_valid(), form.errors)

    def test_empty_name(self):
        form = UserTeamForm(data={"name": "", "description": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_form_fields(self):
        form = UserTeamForm()
        self.assertIn("name", form.fields)
        self.assertIn("description", form.fields)


class TestUserUpdateForm(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="edit-user",
            email="edit-user@example.com",
            password="pass1234",
        )

    def test_valid_form(self):
        form = UserUpdateForm(
            instance=self.user,
            data={
                "username": "edit-user",
                "email": "new-email@example.com",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_fields(self):
        form = UserUpdateForm(instance=self.user)
        self.assertIn("username", form.fields)
        self.assertIn("email", form.fields)
