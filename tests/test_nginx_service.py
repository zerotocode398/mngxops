"""验证 Nginx 启停选择、节点日志和历史页面。"""

from dataclasses import replace
import json
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient
from ngxops.accounts.models import User
from ngxops.accounts.passwords import make_password
from ngxops.app import create_app
from ngxops.config import get_settings
from ngxops.credentials.crypto import encrypt_secret
from ngxops.credentials.models import Credential
from ngxops.database.migration_runner import upgrade_database
from ngxops.database.session import session_scope
from ngxops.nodes.models import Node, NodeGroup
from ngxops.settings.models import SystemSetting
from ngxops.tasks.executor import TaskContext
from ngxops.tasks.models import Task, TaskLog


class RecordingExecutor:
    """记录任务回调但不创建后台线程。"""

    def __init__(self):
        """初始化回调列表。"""
        self.submitted = []

    def submit(self, task_id, runner):
        """记录一次任务提交。"""
        self.submitted.append((task_id, runner))

    def cancel(self, task_id):
        """提供执行器取消接口。"""
        return False

    def shutdown(self, wait=False):
        """测试执行器无需释放线程资源。"""
        return None


@pytest.fixture
def service_client(tmp_path, monkeypatch):
    """创建带动态批量上限和在线/离线节点的隔离应用。"""
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
                user = User(
                    username="service-admin",
                    password=make_password("service-admin-password-123"),
                    is_superuser=True,
                    is_active=True,
                )
                session.add(user)
                session.flush()
                credential = Credential(
                    name="service-ssh",
                    username="deploy",
                    auth_type="password",
                    password=encrypt_secret(
                        app.state.credential_encryption_key, "service-ssh-secret"
                    ),
                    is_enabled=True,
                    created_by=user.id,
                )
                session.add(credential)
                session.flush()
                nodes = [
                    Node(
                        hostname="service-node-{}".format(index),
                        ip="192.0.2.{}".format(160 + index),
                        port=22,
                        credential_id=credential.id,
                        environment="prod" if index == 1 else "test",
                        status="online",
                        nginx_available=True,
                        nginx_version="nginx/1.26.{}".format(index),
                        created_by=user.id,
                    )
                    for index in range(1, 4)
                ]
                nodes.extend(
                    [
                        Node(
                            hostname="offline-service-node",
                            ip="192.0.2.170",
                            credential_id=credential.id,
                            status="offline",
                            nginx_available=True,
                            created_by=user.id,
                        ),
                        Node(
                            hostname="unrecognized-service-node",
                            ip="192.0.2.171",
                            credential_id=credential.id,
                            status="online",
                            nginx_available=False,
                            created_by=user.id,
                        ),
                    ]
                )
                group = NodeGroup(name="production", created_by=user.id)
                nodes[0].groups.append(group)
                session.add_all(nodes)
                session.add(
                    SystemSetting(
                        key="node.batch_max_count",
                        value="2",
                        group_name="节点管理",
                        label="批量操作最大节点数",
                    )
                )
                session.flush()
                eligible_ids = [node.id for node in nodes[:3]]
                user_id = user.id
        login_page = client.get("/login/")
        token = re.search(
            r'name="csrf_token" value="([^"]+)"', login_page.text
        ).group(1)
        login = client.post(
            "/login/",
            data={
                "csrf_token": token,
                "username": "service-admin",
                "password": "service-admin-password-123",
            },
        )
        assert login.status_code == 302
        executor = RecordingExecutor()
        app.state.task_executor = executor
        yield client, app, token, executor, eligible_ids, user_id, monkeypatch


def test_service_selector_batch_limit_and_node_scoped_task_logs(service_client):
    """验证节点资格、设置上限、逐节点日志和终端页面行为。"""
    client, app, csrf, executor, node_ids, _user_id, monkeypatch = service_client
    nodes = client.get("/api/nginx/service/nodes")
    assert nodes.status_code == 200, nodes.text
    assert nodes.json()["total"] == 3
    assert nodes.json()["batch_max_count"] == 2
    assert [item["id"] for item in nodes.json()["data"]] == node_ids
    assert client.get(
        "/api/nginx/service/nodes?search=service-node-1%2Cproduction"
    ).json()["total"] == 1

    home = client.get("/nginx/service/")
    assert 'data-max-count="2"' in home.text
    assert 'data-query-tags' in home.text
    assert 'id="serviceNodePageFirst"' in home.text
    assert 'id="serviceNodePageSize"' in home.text
    assert "progressLive" not in home.text

    too_many = client.post(
        "/api/nginx/service/execute",
        headers={"X-CSRFToken": csrf},
        json={"action": "start", "node_ids": node_ids},
    )
    assert too_many.status_code == 400
    created = client.post(
        "/api/nginx/service/execute",
        headers={"X-CSRFToken": csrf},
        json={"action": "start", "node_ids": node_ids[:2]},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]
    assert len(executor.submitted) == 1

    class FakeClient:
        """提供不连接 SSH 的占位客户端。"""

        def close(self):
            """关闭占位客户端。"""
            return None

    def fake_remote(_client, command, timeout=45, context=None, log_output=True):
        """写入可核对的远程输出并模拟二进制启动成功。"""
        if command.startswith("command -v systemctl"):
            return 1, ""
        context.append_log("remote command: " + command)
        context.append_log("master process started")
        return 0, "master process started"

    monkeypatch.setattr(
        "ngxops.nginx_service.services._connect_target",
        lambda _target, _context: FakeClient(),
    )
    monkeypatch.setattr(
        "ngxops.nginx_service.services._run_remote_command", fake_remote
    )
    task_context = TaskContext(
        task_id,
        app.state.database.session_factory,
        lambda _task_id, _callback: None,
    )
    task_id_from_executor, runner = executor.submitted[0]
    assert task_id_from_executor == task_id
    runner(task_context)

    progress = client.get("/api/nginx/service/tasks/{}".format(task_id))
    assert progress.status_code == 200, progress.text
    node_logs = [
        log for log in progress.json()["logs"] if log["message"].startswith("remote command:")
    ]
    assert {log["node_id"] for log in node_logs} == set(node_ids[:2])

    with session_scope(app.state.database.session_factory) as session:
        task = session.get(Task, task_id)
        assert json.loads(task.result_tree_json)["nodes"][0]["id"] == node_ids[0]
        assert session.query(TaskLog).filter_by(task_id=task_id).count() > 0

    detail = client.get("/nginx/service/task/{}/log/".format(task_id))
    toolbar = detail.text.split('<div class="d-flex flex-wrap gap-2 mb-3">', 1)[1].split("</div>", 1)[0]
    assert "任务中心" not in toolbar
    assert 'id="serviceLogHostFilter"' in detail.text
    assert "master process started" in detail.text
    result_table = detail.text.split('id="taskNodeResults"', 1)[0].rsplit("<thead", 1)[1]
    assert "状态" not in result_table
    assert "upgrade-log-output" in detail.text
    assert 'href="/tasks/{}/"'.format(task_id) not in toolbar

    task_center = client.get("/tasks/{}/".format(task_id))
    assert task_center.status_code == 200, task_center.text
    assert 'href="/nginx/service/history/?search={}"'.format(created.json()["batch_number"]) in task_center.text
    assert 'data-log-mode="plain-text"' in task_center.text
    assert "task-log-row" not in task_center.text
    assert "data-log-node-id" in task_center.text

    history = client.get(
        "/nginx/service/history/?search=service-node-1%2Cstart&status=running&per_page=25"
    )
    assert history.status_code == 200, history.text
    assert created.json()["batch_number"] in history.text
    assert 'data-query-tags' in history.text
    assert 'name="per_page" value="25"' in history.text
    assert 'id="perPageSelect"' in history.text
    assert "筛选" not in history.text
    assert "2026-" in history.text
