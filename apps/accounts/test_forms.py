"""账户表单测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.forms import LoginForm, CustomPasswordChangeForm


class TestLoginForm(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="form-tester",
            email="tester@example.com",
            password="ValidPass123!",
        )

    def test_login_form_valid(self):
        form = LoginForm(data={"username": "form-tester", "password": "ValidPass123!"})
        self.assertTrue(form.is_valid())

    def test_login_form_wrong_password(self):
        form = LoginForm(data={"username": "form-tester", "password": "WrongPass"})
        self.assertFalse(form.is_valid())

    def test_login_form_empty_username(self):
        form = LoginForm(data={"username": "", "password": "pass"})
        self.assertFalse(form.is_valid())

    def test_login_form_widgets(self):
        form = LoginForm()
        self.assertIn("form-control", str(form["username"]))
        self.assertIn("form-control", str(form["password"]))


class TestCustomPasswordChangeForm(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="pw-changer",
            email="pw@example.com",
            password="OldPass123!",
        )

    def test_password_change_form_valid(self):
        form = CustomPasswordChangeForm(
            user=self.user,
            data={
                "old_password": "OldPass123!",
                "new_password1": "NewPass456!",
                "new_password2": "NewPass456!",
            },
        )
        self.assertTrue(form.is_valid())

    def test_password_change_form_wrong_old(self):
        form = CustomPasswordChangeForm(
            user=self.user,
            data={
                "old_password": "WrongOld",
                "new_password1": "NewPass456!",
                "new_password2": "NewPass456!",
            },
        )
        self.assertFalse(form.is_valid())

    def test_password_change_form_mismatch(self):
        form = CustomPasswordChangeForm(
            user=self.user,
            data={
                "old_password": "OldPass123!",
                "new_password1": "NewPass456!",
                "new_password2": "Mismatch!",
            },
        )
        self.assertFalse(form.is_valid())
