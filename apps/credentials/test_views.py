"""credentials 视图层测试（凭证 CRUD）"""

import pytest
from django.urls import reverse

from apps.credentials.models import Credential


@pytest.mark.django_db
class TestCredentialListView:
    """凭证列表页"""

    def test_list_accessible(self, admin_client):
        resp = admin_client.get(reverse("credentials:list"))
        assert resp.status_code == 200

    def test_list_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("credentials:list"))
        assert resp.status_code == 302

    def test_list_shows_credentials(self, admin_client, credential):
        resp = admin_client.get(reverse("credentials:list"))
        assert resp.status_code == 200
        assert credential.name in resp.content.decode()

    def test_list_search(self, admin_client, credential):
        resp = admin_client.get(
            reverse("credentials:list"), {"search": "test-credential"}
        )
        assert resp.status_code == 200
        assert credential.name in resp.content.decode()

    def test_list_search_not_found(self, admin_client, credential):
        resp = admin_client.get(reverse("credentials:list"), {"search": "nonexistent"})
        assert resp.status_code == 200
        assert credential.name not in resp.content.decode()

    def test_list_filter_by_auth_type(self, admin_client, credential):
        resp = admin_client.get(reverse("credentials:list"), {"auth_type": "password"})
        assert resp.status_code == 200
        assert credential.name in resp.content.decode()

    def test_list_filter_by_status(self, admin_client, credential):
        resp = admin_client.get(reverse("credentials:list"), {"status": "enabled"})
        assert resp.status_code == 200
        assert credential.name in resp.content.decode()


@pytest.mark.django_db
class TestCredentialCreateView:
    """凭证创建"""

    def test_create_page_accessible(self, admin_client):
        resp = admin_client.get(reverse("credentials:create"))
        assert resp.status_code == 200

    def test_create_credential_success(self, admin_client):
        resp = admin_client.post(
            reverse("credentials:create"),
            {
                "name": "new-cred",
                "username": "root",
                "auth_type": "password",
                "password": "secret123",
                "description": "test",
            },
        )
        assert resp.status_code == 302
        assert Credential.objects.filter(name="new-cred").exists()

    def test_create_credential_duplicate_name(self, admin_client, credential):
        resp = admin_client.post(
            reverse("credentials:create"),
            {
                "name": "test-credential",
                "username": "root",
                "auth_type": "password",
                "password": "secret123",
            },
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestCredentialUpdateView:
    """凭证编辑"""

    def test_edit_page_accessible(self, admin_client, credential):
        resp = admin_client.get(reverse("credentials:edit", args=[credential.id]))
        assert resp.status_code == 200

    def test_edit_credential_success(self, admin_client, credential):
        resp = admin_client.post(
            reverse("credentials:edit", args=[credential.id]),
            {
                "name": "updated-credential",
                "username": "root",
                "auth_type": "password",
                "description": "updated",
            },
        )
        assert resp.status_code == 302
        credential.refresh_from_db()
        assert credential.name == "updated-credential"


@pytest.mark.django_db
class TestCredentialDeleteView:
    """凭证删除"""

    def test_delete_page_accessible(self, admin_client, credential):
        resp = admin_client.get(reverse("credentials:delete", args=[credential.id]))
        assert resp.status_code == 200

    def test_delete_credential_success(self, admin_client, credential):
        resp = admin_client.post(reverse("credentials:delete", args=[credential.id]))
        assert resp.status_code == 302
        assert not Credential.objects.filter(id=credential.id).exists()


@pytest.mark.django_db
class TestCredentialDecryptView:
    """凭证密码解密"""

    def test_decrypt_api_accessible(self, admin_client, credential):
        resp = admin_client.get(reverse("credentials:decrypt", args=[credential.id]))
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["success"] is True
        assert payload["value"] == "secret"


@pytest.mark.django_db
class TestCredentialToggleEnableView:
    """凭证启用/禁用切换"""

    def test_toggle_disable(self, admin_client, credential):
        resp = admin_client.post(
            reverse("credentials:toggle_enable", args=[credential.id])
        )
        assert resp.status_code == 302
        credential.refresh_from_db()
        assert credential.is_enabled is False

    def test_toggle_enable(self, admin_client, credential):
        credential.is_enabled = False
        credential.save()
        resp = admin_client.post(
            reverse("credentials:toggle_enable", args=[credential.id])
        )
        assert resp.status_code == 302
        credential.refresh_from_db()
        assert credential.is_enabled is True
