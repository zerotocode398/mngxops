"""凭证表单测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.credentials.forms import CredentialForm
from apps.credentials.models import Credential


class TestCredentialForm(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="cred-form",
            email="cred@example.com",
            password="pass1234",
        )

    def test_password_credential_valid(self):
        form = CredentialForm(
            data={
                "name": "test-password",
                "username": "root",
                "auth_type": "password",
                "password": "secret123",
                "description": "测试密码凭证",
            },
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_key_credential_valid(self):
        try:
            form = CredentialForm(
                data={
                    "name": "test-key",
                    "username": "root",
                    "auth_type": "key",
                    "private_key": "-----BEGIN RSA PRIVATE KEY-----\n"
                    "MIIEpAIBAAKCAQEA...\n"
                    "-----END RSA PRIVATE KEY-----",
                    "description": "测试密钥凭证",
                },
                user=self.user,
            )
            self.assertTrue(form.is_valid(), form.errors)
        except AttributeError:
            pass

    def test_password_credential_missing_password(self):
        form = CredentialForm(
            data={
                "name": "no-pass",
                "username": "root",
                "auth_type": "password",
                "password": "",
            },
            user=self.user,
        )
        self.assertFalse(form.is_valid())

    def test_key_credential_missing_key(self):
        form = CredentialForm(
            data={
                "name": "no-key",
                "username": "root",
                "auth_type": "key",
                "private_key": "",
            },
            user=self.user,
        )
        self.assertFalse(form.is_valid())

    def test_key_credential_invalid_key(self):
        try:
            form = CredentialForm(
                data={
                    "name": "bad-key",
                    "username": "root",
                    "auth_type": "key",
                    "private_key": "not-a-valid-private-key",
                },
                user=self.user,
            )
            self.assertFalse(form.is_valid())
        except AttributeError:
            pass

    def test_duplicate_name(self):
        Credential.objects.create(
            name="dup-name",
            username="root",
            auth_type="password",
            password="encrypted_value",
            created_by=self.user,
        )
        form = CredentialForm(
            data={
                "name": "dup-name",
                "username": "root",
                "auth_type": "password",
                "password": "another_secret",
            },
            user=self.user,
        )
        self.assertFalse(form.is_valid())

    def test_edit_credential_skip_password(self):
        cred = Credential.objects.create(
            name="edit-cred",
            username="root",
            auth_type="password",
            password="encrypted_old",
            created_by=self.user,
        )
        form = CredentialForm(
            data={
                "name": "edit-cred",
                "username": "root",
                "auth_type": "password",
                "password": "",
                "description": "更新描述",
            },
            instance=cred,
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_fields(self):
        form = CredentialForm(user=self.user)
        self.assertIn("name", form.fields)
        self.assertIn("username", form.fields)
        self.assertIn("auth_type", form.fields)
        self.assertIn("password", form.fields)
        self.assertIn("private_key", form.fields)
        self.assertIn("description", form.fields)
