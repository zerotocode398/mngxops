"""Nginx 升级模块表单测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.upgrade.forms import (
    NginxSourcePackageForm,
    NginxThirdPartyModulePackageForm,
    NginxUpgradeTaskForm,
)


class TestNginxSourcePackageForm(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="src-pkg",
            email="src-pkg@example.com",
            password="pass1234",
        )

    def test_empty_data(self):
        form = NginxSourcePackageForm(data={}, user=self.user)
        self.assertFalse(form.is_valid())

    def test_required_name(self):
        form = NginxSourcePackageForm(
            data={"version": "1.26.1"},
            user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_required_version(self):
        form = NginxSourcePackageForm(
            data={"name": "test-pkg"},
            user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("version", form.errors)

    def test_form_fields(self):
        form = NginxSourcePackageForm(user=self.user)
        self.assertIn("name", form.fields)
        self.assertIn("version", form.fields)
        self.assertIn("package_file", form.fields)
        self.assertIn("is_official", form.fields)


class TestNginxThirdPartyModulePackageForm(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="tp-module",
            email="tp-module@example.com",
            password="pass1234",
        )

    def test_empty_data(self):
        form = NginxThirdPartyModulePackageForm(data={}, user=self.user)
        self.assertFalse(form.is_valid())

    def test_required_name(self):
        form = NginxThirdPartyModulePackageForm(
            data={"name": ""},
            user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_valid_minimal(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        fake_file = SimpleUploadedFile("test-module.tar.gz", b"test content")
        form = NginxThirdPartyModulePackageForm(
            data={"name": "echo-module", "version": "v1.0"},
            files={"package_file": fake_file},
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_fields(self):
        form = NginxThirdPartyModulePackageForm(user=self.user)
        self.assertIn("name", form.fields)
        self.assertIn("version", form.fields)
        self.assertIn("package_file", form.fields)
        self.assertIn("description", form.fields)


class TestNginxUpgradeTaskForm(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="upgrade-form",
            email="upgrade-form@example.com",
            password="pass1234",
        )

    def test_empty_data(self):
        form = NginxUpgradeTaskForm(data={})
        self.assertFalse(form.is_valid())

    def test_form_fields(self):
        form = NginxUpgradeTaskForm()
        self.assertIn("source_package", form.fields)
        self.assertIn("node", form.fields)
