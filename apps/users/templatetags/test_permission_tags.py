"""权限模板标签测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.users.templatetags.permission_tags import has_perm_code


class TestHasPermCode(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="perm-tag",
            email="perm-tag@example.com",
            password="pass1234",
        )

    def test_superuser_always_true(self):
        self.user.is_superuser = True
        self.user.save()
        self.assertTrue(has_perm_code(self.user, "credentials.read"))

    def test_anonymous_user(self):
        from django.contrib.auth.models import AnonymousUser

        anon = AnonymousUser()
        self.assertFalse(has_perm_code(anon, "credentials.read"))

    def test_none_user(self):
        self.assertFalse(has_perm_code(None, "credentials.read"))

    def test_unauthenticated_user(self):
        from django.contrib.auth.models import AnonymousUser

        anon = AnonymousUser()
        self.assertFalse(has_perm_code(anon, "credentials.read"))

    def test_invalid_format(self):
        self.assertFalse(has_perm_code(self.user, "invalid"))
        self.assertFalse(has_perm_code(self.user, ""))
        self.assertFalse(has_perm_code(self.user, "nodot"))

    def test_valid_format(self):
        result = has_perm_code(self.user, "configs.read")
        self.assertIsInstance(result, bool)
