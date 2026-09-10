"""配置表单测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.configs.forms import ConfigForm, BindingForm
from apps.configs.models import Config, ConfigNodeBinding
from apps.nodes.models import Node


class TestConfigForm(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="cfg-form",
            email="cfg@example.com",
            password="pass1234",
        )

    def test_config_form_valid(self):
        form = ConfigForm(
            data={
                "name": "test.conf",
                "default_remote_path": "/etc/nginx/conf.d/test.conf",
                "template_content": "server { listen 80; }",
                "description": "测试配置",
            }
        )
        self.assertTrue(form.is_valid())

    def test_config_form_empty_name(self):
        form = ConfigForm(
            data={
                "name": "",
                "default_remote_path": "/etc/nginx/conf.d/test.conf",
            }
        )
        self.assertFalse(form.is_valid())

    def test_config_form_fields(self):
        form = ConfigForm()
        self.assertIn("name", form.fields)
        self.assertIn("default_remote_path", form.fields)
        self.assertIn("template_content", form.fields)
        self.assertIn("description", form.fields)


class TestBindingForm(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="bind-form",
            email="bind@example.com",
            password="pass1234",
        )
        self.config = Config.objects.create(
            name="app.conf",
            default_remote_path="/etc/nginx/conf.d/app.conf",
            created_by=self.user,
        )
        self.node = Node.objects.create(
            hostname="web01",
            ip="10.0.0.1",
            status="online",
            created_by=self.user,
        )

    def test_binding_form_valid(self):
        form = BindingForm(
            data={
                "config": self.config.id,
                "remote_path": "/etc/nginx/conf.d/app.conf",
                "content": "server { listen 80; }",
                "remark": "初始绑定",
            }
        )
        self.assertTrue(form.is_valid())

    def test_binding_form_empty_content(self):
        form = BindingForm(
            data={
                "config": self.config.id,
                "remote_path": "/etc/nginx/conf.d/app.conf",
                "content": "",
            }
        )
        self.assertFalse(form.is_valid())

    def test_binding_form_fields(self):
        form = BindingForm()
        self.assertIn("config", form.fields)
        self.assertIn("node", form.fields)
        self.assertIn("remote_path", form.fields)
        self.assertIn("content", form.fields)
        self.assertIn("remark", form.fields)
