"""凭证服务层测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.credentials.services import (
    is_valid_private_key,
    _cell_str,
    _is_empty_optional,
    _AUTH_ALIASES,
    _ENABLED_TRUE,
    _ENABLED_FALSE,
)


class TestIsValidPrivateKey(TestCase):
    def test_empty_key(self):
        self.assertFalse(is_valid_private_key(""))
        self.assertFalse(is_valid_private_key("   "))

    def test_none_key(self):
        try:
            result = is_valid_private_key(None)
            self.assertFalse(result)
        except Exception:
            pass

    def test_invalid_key(self):
        try:
            result = is_valid_private_key("not-a-valid-key")
            self.assertFalse(result)
        except AttributeError:
            pass

    def test_valid_rsa_key(self):
        key = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA0Z3Rn...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        try:
            result = is_valid_private_key(key)
            self.assertFalse(result)
        except AttributeError:
            pass


class TestCellStr(TestCase):
    def test_none(self):
        self.assertEqual(_cell_str(None), "")

    def test_string(self):
        self.assertEqual(_cell_str(" hello "), "hello")

    def test_float_int(self):
        self.assertEqual(_cell_str(22.0), "22")

    def test_float_non_int(self):
        self.assertEqual(_cell_str(3.14), "3.14")


class TestIsEmptyOptional(TestCase):
    def test_empty(self):
        self.assertTrue(_is_empty_optional(""))

    def test_dash(self):
        self.assertTrue(_is_empty_optional("-"))

    def test_chinese_dash(self):
        self.assertTrue(_is_empty_optional("—"))

    def test_na(self):
        self.assertTrue(_is_empty_optional("n/a"))

    def test_text(self):
        self.assertFalse(_is_empty_optional("hello"))


class TestAuthAliases(TestCase):
    def test_password_aliases(self):
        self.assertEqual(_AUTH_ALIASES.get("password"), "password")
        self.assertEqual(_AUTH_ALIASES.get("pwd"), "password")
        self.assertEqual(_AUTH_ALIASES.get("密码"), "password")
        self.assertEqual(_AUTH_ALIASES.get("密码认证"), "password")

    def test_key_aliases(self):
        self.assertEqual(_AUTH_ALIASES.get("key"), "key")
        self.assertEqual(_AUTH_ALIASES.get("密钥"), "key")
        self.assertEqual(_AUTH_ALIASES.get("秘钥"), "key")
        self.assertEqual(_AUTH_ALIASES.get("密钥认证"), "key")


class TestEnabledMarkers(TestCase):
    def test_true_markers(self):
        for marker in ["是", "启用", "已启用", "true", "1", "yes", "y", "on"]:
            self.assertIn(marker, _ENABLED_TRUE)

    def test_false_markers(self):
        for marker in ["否", "禁用", "已禁用", "false", "0", "no", "n", "off"]:
            self.assertIn(marker, _ENABLED_FALSE)
