"""密码强度校验测试。"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.users.password_validation import CombinedSimilarityAndLengthValidator


class TestCombinedSimilarityAndLengthValidator(TestCase):
    def setUp(self):
        self.validator = CombinedSimilarityAndLengthValidator(min_length=8)
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="pass1234",
        )

    def test_help_text(self):
        help_text = self.validator.get_help_text()
        self.assertIn("8", help_text)
        self.assertIn("相似", help_text)

    def test_strong_password(self):
        try:
            self.validator.validate("ComplexP@ssw0rd!2024", self.user)
        except ValidationError:
            self.fail("Strong password should not raise ValidationError")

    def test_short_password(self):
        with self.assertRaises(ValidationError):
            self.validator.validate("Ab1", self.user)

    def test_similar_to_username(self):
        with self.assertRaises(ValidationError):
            self.validator.validate("admin123", self.user)

    def test_weak_password(self):
        with self.assertRaises(ValidationError):
            self.validator.validate("abc", self.user)

    def test_no_user(self):
        try:
            self.validator.validate("ComplexP@ssw0rd!2024", None)
        except ValidationError:
            self.fail("Password without user should not raise ValidationError")

    def test_empty_password(self):
        with self.assertRaises(ValidationError):
            self.validator.validate("", self.user)
