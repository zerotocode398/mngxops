"""pytest 共享 fixture —— 所有 app 的测试可复用。"""

import os
import tempfile
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import Client


def pytest_configure():
    """在 Django 加载前设置 MNGXOPS_HOME 到临时目录，避免污染项目数据库。"""
    if "MNGXOPS_HOME" not in os.environ:
        os.environ["MNGXOPS_HOME"] = tempfile.mkdtemp(prefix="mngxops_test_")


@pytest.fixture
def admin_user(db):
    """超级管理员用户。"""
    User = get_user_model()
    return User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="pass1234",
    )


@pytest.fixture
def normal_user(db):
    """普通用户（无任何权限）。"""
    User = get_user_model()
    return User.objects.create_user(
        username="viewer",
        email="viewer@example.com",
        password="pass1234",
    )


@pytest.fixture
def admin_client(admin_user):
    """已登录管理员用户的 Django TestClient。"""
    client = Client()
    client.force_login(admin_user)
    return client


@pytest.fixture
def user_client(normal_user):
    """已登录普通用户的 Django TestClient。"""
    client = Client()
    client.force_login(normal_user)
    return client


@pytest.fixture
def anonymous_client():
    """未登录的匿名客户端。"""
    return Client()


@pytest.fixture
def credential(admin_user):
    """创建一个测试凭据。"""
    from apps.credentials.models import Credential

    return Credential.objects.create(
        name="test-credential",
        auth_type="password",
        username="root",
        password="secret",
        is_enabled=True,
        created_by=admin_user,
    )


@pytest.fixture
def online_node(admin_user, credential):
    """创建一个在线节点。"""
    from apps.nodes.models import Node

    return Node.objects.create(
        hostname="ngx-test-01",
        ip="10.0.0.101",
        status="online",
        nginx_available=True,
        credential=credential,
        created_by=admin_user,
    )


@pytest.fixture
def offline_node(admin_user, credential):
    """创建一个离线节点。"""
    from apps.nodes.models import Node

    return Node.objects.create(
        hostname="ngx-test-02",
        ip="10.0.0.102",
        status="offline",
        nginx_available=False,
        credential=credential,
        created_by=admin_user,
    )
