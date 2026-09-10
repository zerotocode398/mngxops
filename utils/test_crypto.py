"""加密工具测试。"""

import os
import tempfile

from django.test import TestCase

from utils.crypto import encrypt_value, decrypt_value


class TestEncryptDecrypt(TestCase):
    def test_roundtrip(self):
        plain = "my-secret-password"
        encrypted = encrypt_value(plain)
        self.assertNotEqual(encrypted, plain)
        self.assertEqual(decrypt_value(encrypted), plain)

    def test_empty_plaintext(self):
        self.assertEqual(encrypt_value(""), "")
        self.assertEqual(encrypt_value(None), "")

    def test_empty_ciphertext(self):
        self.assertEqual(decrypt_value(""), "")
        self.assertEqual(decrypt_value(None), "")

    def test_different_inputs_produce_different_outputs(self):
        a = encrypt_value("password1")
        b = encrypt_value("password2")
        self.assertNotEqual(a, b)

    def test_same_input_produces_different_ciphertext(self):
        a = encrypt_value("password")
        b = encrypt_value("password")
        self.assertNotEqual(a, b)

    def test_unicode(self):
        plain = "密码测试123!@#"
        encrypted = encrypt_value(plain)
        self.assertEqual(decrypt_value(encrypted), plain)

    def test_long_text(self):
        plain = "A" * 1000
        encrypted = encrypt_value(plain)
        self.assertEqual(decrypt_value(encrypted), plain)
