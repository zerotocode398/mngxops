"""验证 Nginx 全新安装向导、任务创建和状态回写。"""

from dataclasses import replace
import hashlib
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ngxops.accounts.models import User
from ngxops.accounts.passwords import make_password
from ngxops.configs.models import ConfigSyncSetting
from ngxops.app import create_app
from ngxops.config import get_settings
from ngxops.credentials.crypto import encrypt_secret
from ngxops.credentials.models import Credential
from ngxops.database.migration_runner import upgrade_database
from ngxops.database.session import session_scope
from ngxops.nginx_install.models import NginxInstallRun
from ngxops.nginx_install.services import (
    _sync_installed_configs,
    _write_node_install_result,
)
from ngxops.nodes.models import Node, NodeSyncSetting
from ngxops.tasks.models import Task
from ngxops.upgrade.models import NginxSourcePackage
from ngxops.upgrade.services import _package_active_task_ids


class RecordingExecutor:
    """保留新任务提交记录但不连接 SSH。"""

    def __init__(self):
        """初始化提交记录。"""
        self.submitted = []

    def submit(self, task_id, runner):
        """记录等待执行的任务回调。"""
        self.submitted.append((task_id, runner))

    def cancel(self, task_id):
        """测试执行器不执行远程任务。"""
        return False

    def shutdown(self, wait=False):
        """测试执行器无需关闭线程。"""
        return None


@pytest.fixture
def install_client(tmp_path):
    """创建隔离数据库、在线节点、锁定节点和平台源码包。"""
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
        package_dir = settings.upgrade_package_dir
        package_dir.mkdir(parents=True, exist_ok=True)
        package_bytes = b"test nginx source archive placeholder"
        package_file = package_dir / "nginx-1.26.1.tar.gz"
        package_file.write_bytes(package_bytes)
        with session_scope(app.state.database.session_factory) as session:
            with session.begin():
                user = User(
                    username="install-admin",
                    password=make_password("install-admin-password-123"),
                    is_superuser=True,
                    is_active=True,
                )
                session.add(user)
                session.flush()
                user_id = user.id
                credential = Credential(
                    name="install-ssh",
                    username="deploy",
                    auth_type="password",
                    password=encrypt_secret(
                        app.state.credential_encryption_key, "ssh-secret-value"
                    ),
                    is_enabled=True,
                    created_by=user_id,
                )
                session.add(credential)
                session.flush()
                node = Node(
                    hostname="install-node",
                    ip="192.0.2.151",
                    port=22,
                    credential_id=credential.id,
                    environment="test",
                    status="online",
                    nginx_available=True,
                    created_by=user_id,
                )
                locked_node = Node(
                    hostname="locked-node",
                    ip="192.0.2.152",
                    port=22,
                    credential_id=credential.id,
                    environment="test",
                    status="offline",
                    is_locked=True,
                    created_by=user_id,
                )
                session.add_all([node, locked_node])
                session.flush()
                node_id = node.id
                locked_node_id = locked_node.id
                package = NginxSourcePackage(
                    name="nginx-1.26.1.tar.gz",
                    version="1.26.1",
                    file_name=package_file.name,
                    file_size=len(package_bytes),
                    file_md5=hashlib.md5(package_bytes).hexdigest(),
                    created_by=user_id,
                )
                session.add(package)
                session.flush()
                package_id = package.id
        login_page = client.get("/login/")
        csrf_token = re.search(
            r'name="csrf_token" value="([^"]+)"', login_page.text
        ).group(1)
        login = client.post(
            "/login/",
            data={
                "csrf_token": csrf_token,
                "username": "install-admin",
                "password": "install-admin-password-123",
            },
        )
        assert login.status_code == 302
        executor = RecordingExecutor()
        app.state.task_executor = executor
        yield client, app, csrf_token, executor, node_id, locked_node_id, package_id, user_id


def _install_payload(node_ids, package_id):
    """生成用于创建源码安装批次的请求体。"""
    return {
        "node_ids": node_ids,
        "source_package_id": package_id,
        "target_prefix": "/opt/nginx-custom",
        "nginx_user": "nginx",
        "nginx_group": "nginx",
        "listen_port": 8080,
        "remote_work_dir": "/tmp/nginx-install-work",
        "make_jobs": 2,
        "added_modules": ["--with-http_ssl_module"],
        "added_third_party": [],
        "extra_opts": "",
    }


def test_install_batch_history_package_guard_and_path_sync(install_client, monkeypatch):
    """验证批次门禁、进度、历史筛选、包占用和配置路径回写。"""
    client, app, csrf, executor, node_id, locked_node_id, package_id, user_id = install_client
    created = client.post(
        "/api/nginx-install/tasks",
        headers={"X-CSRFToken": csrf},
        json=_install_payload([node_id, locked_node_id], package_id),
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert len(body["task_ids"]) == 1
    assert body["skipped"] == [
        {
            "id": locked_node_id,
            "hostname": "locked-node",
            "ip": "192.0.2.152",
            "reason": "节点已锁定",
        }
    ]
    assert len(executor.submitted) == 1
    task_id = body["task_ids"][0]

    home = client.get("/nginx-install/")
    assert home.status_code == 200, home.text
    center = client.get("/nginx-install/center/")
    assert center.status_code == 200, center.text
    assert "已检测到" in center.text
    detail = client.get("/nginx-install/task/{}/log/".format(task_id))
    assert detail.status_code == 200, detail.text
    assert 'id="cancelInstallTask"' in detail.text

    progress = client.get("/api/nginx-install/batches/{}".format(body["batch_number"]))
    assert progress.status_code == 200, progress.text
    assert progress.json()["tasks"][0]["task_id"] == task_id
    assert progress.json()["tasks"][0]["status"] == "pending"

    history = client.get("/nginx-install/history/?status=running")
    assert history.status_code == 200, history.text
    assert "install-node" in history.text

    with session_scope(app.state.database.session_factory) as session:
        assert _package_active_task_ids(session, package_id, "source") == [task_id]
        run = session.scalar(
            select(NginxInstallRun).where(NginxInstallRun.task_id == task_id)
        )
        assert run is not None
        assert run.target_prefix == "/opt/nginx-custom"
        task = session.get(Task, task_id)
        assert task.operation_type == "nginx_install"
        assert task.source_batch == body["batch_number"]

    duplicate = client.post(
        "/api/nginx-install/tasks",
        headers={"X-CSRFToken": csrf},
        json=_install_payload([node_id], package_id),
    )
    assert duplicate.status_code == 400
    assert "均不满足安装条件" in duplicate.json()["message"]

    _write_node_install_result(
        app.state.database.session_factory,
        {"node_id": node_id, "task_id": task_id},
        "1.26.1",
        "/opt/nginx-custom/sbin/nginx",
        "/opt/nginx-custom/conf/nginx.conf",
    )
    with session_scope(app.state.database.session_factory) as session:
        assert session.get(Node, node_id).nginx_path == "/opt/nginx-custom/sbin/nginx"
        assert session.get(NodeSyncSetting, node_id).main_conf_path == "/opt/nginx-custom/conf/nginx.conf"
        assert session.scalar(
            select(ConfigSyncSetting.id).where(
                ConfigSyncSetting.node_id == node_id
            )
        ) is None

    captured = {}

    def fake_sync_node(*args, **kwargs):
        """捕获安装后配置同步收到的主配置路径。"""
        captured["main_conf_path"] = args[-1]
        return {
            "node_id": node_id,
            "hostname": "install-node",
            "ip": "192.0.2.151",
            "mode": "full",
            "created": [],
            "updated": [],
            "skipped": [{"name": "nginx.conf", "path": "/opt/nginx-custom/conf/nginx.conf"}],
            "orphaned": [],
            "deleted": [],
            "errors": [],
        }

    monkeypatch.setattr("ngxops.configs.tasks._sync_node", fake_sync_node)
    run_snapshot = {
        "node_id": node_id,
        "trigger_user_id": user_id,
        "task_id": task_id,
        "hostname": "install-node",
        "ip": "192.0.2.151",
        "main_conf_path": "/opt/nginx-custom/conf/nginx.conf",
    }
    sync_ok, _detail, _result = _sync_installed_configs(
        app.state.database.session_factory,
        app.state.credential_encryption_key,
        run_snapshot,
        None,
        None,
    )
    assert sync_ok is True
    assert captured["main_conf_path"] == "/opt/nginx-custom/conf/nginx.conf"

    cancelled = client.post(
        "/api/tasks/{}/cancel".format(task_id),
        headers={"X-CSRFToken": csrf},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"


def test_install_configure_preview_rejects_path_override(install_client):
    """验证预览 API 拒绝额外参数覆盖受保护安装路径。"""
    client, _app, csrf, _executor, _node_id, _locked_id, _package_id, _user_id = install_client
    response = client.post(
        "/api/nginx-install/configure-preview",
        headers={"X-CSRFToken": csrf},
        json={
            "target_prefix": "/opt/nginx",
            "remote_work_dir": "/tmp/nginx-work",
            "extra_opts": "--prefix=/etc/nginx",
        },
    )
    assert response.status_code == 400
    assert "不能覆盖安装路径" in response.json()["message"]
