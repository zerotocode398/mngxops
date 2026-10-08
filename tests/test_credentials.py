"""验证 SSH 凭证加密、管理页面和受保护 API。"""

from dataclasses import replace
from datetime import datetime
from io import StringIO
from pathlib import Path
import re

import paramiko
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ngxops.accounts.models import User
from ngxops.accounts.passwords import make_password
from ngxops.app import create_app
from ngxops.audit.models import AuditLog
from ngxops.config import get_settings
from ngxops.credentials.models import Credential
from ngxops.database.migration_runner import upgrade_database
from ngxops.database.session import session_scope


@pytest.fixture
def authenticated_client(tmp_path):
    """创建隔离 SQLite 数据目录并登录临时超级管理员。"""
    settings = replace(
        get_settings(),
        data_dir=tmp_path,
        resource_dir=Path.cwd(),
        debug=False,
        reload=False,
    )
    app = create_app(settings)
    with TestClient(app, follow_redirects=False) as client:
        upgrade_database(app.state.database.engine)
        with session_scope(app.state.database.session_factory) as session:
            with session.begin():
                session.add(
                    User(
                        username="credential-admin",
                        password=make_password("admin-password-123"),
                        is_superuser=True,
                        is_active=True,
                    )
                )
        login_page = client.get("/login/")
        token = re.search(
            r'name="csrf_token" value="([^"]+)"', login_page.text
        ).group(1)
        login_response = client.post(
            "/login/",
            data={
                "csrf_token": token,
                "username": "credential-admin",
                "password": "admin-password-123",
            },
        )
        assert login_response.status_code == 302, login_response.text
        yield client, app, token


def test_credential_lifecycle_and_secret_protection(authenticated_client):
    """验证凭证密文持久化、解密授权、启停与删除流程。"""
    client, app, csrf_token = authenticated_client
    invalid_key_response = client.post(
        "/credentials/create/",
        data={
            "csrf_token": csrf_token,
            "name": "invalid-key",
            "username": "deploy",
            "auth_type": "key",
            "private_key": "not-a-private-key",
        },
    )
    assert invalid_key_response.status_code == 200
    assert "私钥格式无效" in invalid_key_response.text
    assert "SSH 认证信息" in invalid_key_response.text
    assert "not-a-private-key" not in invalid_key_response.text
    assert (
        'class="card credential-form-section credential-form-section--basic"'
        in invalid_key_response.text
    )
    assert (
        'class="card credential-form-section credential-form-section--auth"'
        in invalid_key_response.text
    )
    assert "credentials-form.css?v=1" in invalid_key_response.text

    response = client.post(
        "/credentials/create/",
        data={
            "csrf_token": csrf_token,
            "name": "production-ssh",
            "username": "deploy",
            "auth_type": "password",
            "password": "sensitive-password-value",
            "description": "production host access",
        },
    )
    assert response.status_code == 303

    with session_scope(app.state.database.session_factory) as session:
        credential = session.scalar(
            select(Credential).where(Credential.name == "production-ssh")
        )
        credential_id = credential.id
        ciphertext = credential.password
        assert ciphertext.startswith("gAAAA")
        assert "sensitive-password-value" not in ciphertext
        assert credential.get_password(app.state.credential_encryption_key) == (
            "sensitive-password-value"
        )

    list_response = client.get("/credentials/?search=production,deploy&status=enabled")
    assert list_response.status_code == 200
    assert "production-ssh" in list_response.text
    assert "sensitive-password-value" not in list_response.text

    secret_response = client.get(
        "/api/credentials/{}/secret?field=password".format(credential_id)
    )
    assert secret_response.status_code == 200
    assert secret_response.headers["cache-control"] == "no-store, private"
    assert secret_response.json()["value"] == "sensitive-password-value"
    invalid_field_response = client.get(
        "/api/credentials/{}/secret?field=private-secret-value".format(credential_id)
    )
    assert invalid_field_response.status_code == 422
    assert "private-secret-value" not in invalid_field_response.text

    selector_response = client.get("/api/credentials")
    assert selector_response.status_code == 200
    assert "sensitive-password-value" not in selector_response.text

    update_response = client.post(
        "/credentials/{}/edit/".format(credential_id),
        data={
            "csrf_token": csrf_token,
            "name": "production-ssh",
            "username": "deploy",
            "auth_type": "password",
            "password": "",
            "private_key": "",
            "description": "updated description",
        },
    )
    assert update_response.status_code == 303
    edit_form = client.get("/credentials/{}/edit/".format(credential_id))
    assert edit_form.status_code == 200
    assert "凭证信息" in edit_form.text
    assert (
        'class="card credential-form-section credential-form-section--basic"'
        in edit_form.text
    )
    assert (
        'class="card credential-form-section credential-form-section--auth"'
        in edit_form.text
    )
    with session_scope(app.state.database.session_factory) as session:
        credential = session.get(Credential, credential_id)
        assert credential.password == ciphertext
        assert credential.description == "updated description"

    key_buffer = StringIO()
    paramiko.RSAKey.generate(1024).write_private_key(key_buffer)
    private_key = key_buffer.getvalue()
    key_response = client.post(
        "/credentials/create/",
        data={
            "csrf_token": csrf_token,
            "name": "key-credential",
            "username": "deploy",
            "auth_type": "key",
            "private_key": private_key,
        },
    )
    assert key_response.status_code == 303
    with session_scope(app.state.database.session_factory) as session:
        key_credential = session.scalar(
            select(Credential).where(Credential.name == "key-credential")
        )
        assert key_credential.private_key.startswith("gAAAA")
        assert "BEGIN RSA PRIVATE KEY" not in key_credential.private_key
        assert key_credential.get_private_key(app.state.credential_encryption_key) == (
            private_key
        )

    toggle_response = client.post(
        "/api/credentials/{}/toggle-enable".format(credential_id),
        headers={"X-CSRFToken": csrf_token},
    )
    assert toggle_response.status_code == 200
    assert toggle_response.json()["is_enabled"] is False

    toggle_response = client.post(
        "/api/credentials/{}/toggle-enable".format(credential_id),
        headers={"X-CSRFToken": csrf_token},
    )
    assert toggle_response.status_code == 200
    assert toggle_response.json()["is_enabled"] is True
    assert "未启动连接测试" in toggle_response.json()["message"]

    delete_response = client.post(
        "/credentials/{}/delete/".format(credential_id),
        data={"csrf_token": csrf_token},
    )
    assert delete_response.status_code == 303
    with session_scope(app.state.database.session_factory) as session:
        assert session.get(Credential, credential_id) is None


def test_credential_api_requires_a_session(tmp_path):
    """拒绝匿名凭证选择和解密请求。"""
    settings = replace(
        get_settings(),
        data_dir=tmp_path,
        resource_dir=Path.cwd(),
        debug=False,
        reload=False,
    )
    with TestClient(create_app(settings), follow_redirects=False) as client:
        response = client.get("/api/credentials")
    assert response.status_code == 401
    assert response.json()["success"] is False


def test_credential_list_query_cards_beijing_time_and_bulk_delete(
    authenticated_client,
):
    """验证凭证列表查询标签、分区布局、北京时间和批量删除。"""
    client, app, csrf_token = authenticated_client
    with session_scope(app.state.database.session_factory) as session:
        admin_id = session.scalar(
            select(User.id).where(User.username == "credential-admin")
        )
        matching = Credential(
            name="production-deploy",
            username="deploy",
            auth_type="password",
            password="encrypted-value",
            private_key="",
            created_by=admin_id,
            updated_at=datetime(2024, 12, 31, 20, 15),
        )
        other_term = Credential(
            name="production-backup",
            username="backup",
            auth_type="password",
            password="encrypted-value",
            private_key="",
            created_by=admin_id,
            updated_at=datetime(2024, 12, 31, 19, 15),
        )
        other_filter = Credential(
            name="production-key",
            username="deploy",
            auth_type="key",
            password="",
            private_key="encrypted-value",
            is_enabled=False,
            created_by=admin_id,
            updated_at=datetime(2024, 12, 31, 18, 15),
        )
        session.add_all([matching, other_term, other_filter])
        session.flush()
        matching_id = matching.id
        other_term_id = other_term.id
        session.commit()

    response = client.get(
        "/credentials/",
        params={
            "search": "prod,deploy",
            "auth_type": "password",
            "status": "enabled",
            "per_page": 25,
        },
    )
    assert response.status_code == 200
    assert "production-deploy" in response.text
    assert "production-backup" not in response.text
    assert "production-key" not in response.text
    assert "2025-01-01 04:15" in response.text
    assert "data-query-submit-on-enter" in response.text
    assert "data-preserve-query-focus" in response.text
    assert "data-query-tags" in response.text
    assert "筛选</button>" not in response.text
    assert 'name="search" value="prod,deploy"' in response.text
    assert 'name="auth_type" value="password"' in response.text
    assert 'name="status" value="enabled"' in response.text
    assert 'value="password" selected' in response.text
    assert 'value="enabled" selected' in response.text
    assert response.text.count('class="card credential-toolbar-card"') == 1
    assert "credential-filter-card" not in response.text
    assert "credential-table-card" not in response.text
    assert "credential-select-col" in response.text
    assert "id=\"deleteSelectedCredentials\"" in response.text
    assert "class=\"btn btn-outline-danger btn-sm credential-delete\"" in response.text
    assert "app.js?v=8" in response.text
    assert "credentials.css?v=1" in response.text

    invalid_bulk_delete = client.post(
        "/credentials/bulk-delete/",
        data={
            "csrf_token": csrf_token,
            "credential_ids": [str(matching_id), "999999"],
        },
    )
    assert invalid_bulk_delete.status_code == 404
    with session_scope(app.state.database.session_factory) as session:
        assert session.get(Credential, matching_id) is not None

    bulk_delete = client.post(
        "/credentials/bulk-delete/",
        data={
            "csrf_token": csrf_token,
            "credential_ids": [str(matching_id), str(other_term_id)],
        },
    )
    assert bulk_delete.status_code == 303
    with session_scope(app.state.database.session_factory) as session:
        assert session.get(Credential, matching_id) is None
        assert session.get(Credential, other_term_id) is None
        audit = session.scalar(
            select(AuditLog).where(AuditLog.action == "批量删除凭证")
        )
        assert audit is not None
        assert "2 条凭证" in audit.detail

    shared_script = client.get("/static/js/app.js?v=8")
    assert shared_script.status_code == 200
    assert "Array.isArray(fields[name])" in shared_script.text
    assert "function (title, message, actionUrl, fields)" in shared_script.text
