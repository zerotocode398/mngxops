"""节点表单测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.nodes.forms import NodeGroupForm, NodeForm, CredentialChoiceField
from apps.nodes.models import Node, NodeGroup
from apps.credentials.models import Credential


class TestNodeGroupForm(TestCase):
    def test_valid_form(self):
        form = NodeGroupForm(data={"name": "生产组", "description": "生产环境节点组"})
        self.assertTrue(form.is_valid(), form.errors)

    def test_empty_name(self):
        form = NodeGroupForm(data={"name": "", "description": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_form_fields(self):
        form = NodeGroupForm()
        self.assertIn("name", form.fields)
        self.assertIn("description", form.fields)


class TestCredentialChoiceField(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="cred-field",
            email="cred-field@example.com",
            password="pass1234",
        )

    def test_empty_choices(self):
        field = CredentialChoiceField()
        choices = field.choices
        self.assertIsNotNone(choices)

    def test_choices_with_credentials(self):
        Credential.objects.create(
            name="pwd-creds",
            username="root",
            auth_type="password",
            password="encrypted",
            is_enabled=True,
            created_by=self.user,
        )
        Credential.objects.create(
            name="key-creds",
            username="root",
            auth_type="key",
            private_key="-----BEGIN RSA PRIVATE KEY-----\n...",
            is_enabled=True,
            created_by=self.user,
        )
        field = CredentialChoiceField()
        choices = field.choices
        self.assertIsNotNone(choices)

    def test_disabled_credentials_excluded(self):
        # 创建后禁用凭证
        cred = Credential.objects.create(
            name="disabled-cred",
            username="root",
            auth_type="password",
            password="encrypted",
            is_enabled=True,
            created_by=self.user,
        )
        cred.is_enabled = False
        cred.save()
        field = CredentialChoiceField()
        self.assertNotIn(
            cred.pk,
            Credential.objects.filter(is_enabled=True).values_list("pk", flat=True),
        )


class TestNodeForm(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="node-form",
            email="node-form@example.com",
            password="pass1234",
        )

    def test_valid_form(self):
        form = NodeForm(
            data={
                "hostname": "web-prod-01",
                "ip": "192.168.1.100",
                "port": 22,
                "environment": "prod",
                "nginx_path": "/usr/local/nginx/sbin/nginx",
            },
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_empty_hostname(self):
        form = NodeForm(
            data={
                "hostname": "",
                "ip": "192.168.1.100",
                "environment": "prod",
            },
            user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("hostname", form.errors)

    def test_empty_ip(self):
        form = NodeForm(
            data={
                "hostname": "web-prod-01",
                "ip": "",
                "environment": "prod",
            },
            user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("ip", form.errors)

    def test_duplicate_ip(self):
        Node.objects.create(
            hostname="existing-node",
            ip="192.168.1.100",
            status="online",
            created_by=self.user,
        )
        form = NodeForm(
            data={
                "hostname": "web-prod-02",
                "ip": "192.168.1.100",
                "port": 22,
                "environment": "prod",
            },
            user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("ip", form.errors)

    def test_groups_limit(self):
        group1 = NodeGroup.objects.create(name="group1", created_by=self.user)
        group2 = NodeGroup.objects.create(name="group2", created_by=self.user)
        group3 = NodeGroup.objects.create(name="group3", created_by=self.user)
        group4 = NodeGroup.objects.create(name="group4", created_by=self.user)
        form = NodeForm(
            data={
                "hostname": "web-prod-01",
                "ip": "192.168.1.100",
                "port": 22,
                "environment": "prod",
                "groups": [group1.pk, group2.pk, group3.pk, group4.pk],
            },
            user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("groups", form.errors)

    def test_form_fields(self):
        form = NodeForm(user=self.user)
        self.assertIn("hostname", form.fields)
        self.assertIn("ip", form.fields)
        self.assertIn("port", form.fields)
        self.assertIn("credential", form.fields)
        self.assertIn("environment", form.fields)
        self.assertIn("nginx_path", form.fields)

    def test_initial_values_new(self):
        form = NodeForm(user=self.user)
        self.assertIsNotNone(form.fields["port"].initial)

    def test_edit_form_retains_ip(self):
        node = Node.objects.create(
            hostname="edit-node",
            ip="192.168.1.200",
            status="online",
            created_by=self.user,
        )
        form = NodeForm(
            instance=node,
            data={
                "hostname": "edit-node",
                "ip": "192.168.1.200",
                "port": 22,
                "environment": "prod",
            },
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
