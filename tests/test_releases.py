"""验证发布目录准备、历史查询入口和绑定状态过滤。"""

from dataclasses import replace
import hashlib
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient

from ngxops.accounts.models import User
from ngxops.accounts.passwords import make_password
from ngxops.app import create_app
from ngxops.config import get_settings
from ngxops.configs.models import Config, ConfigBinding
from ngxops.credentials.crypto import encrypt_secret
from ngxops.credentials.models import Credential
from ngxops.database.migration_runner import upgrade_database
from ngxops.database.session import session_scope
from ngxops.nodes.models import Node
from ngxops.releases import services as release_services


@pytest.fixture
def release_client(tmp_path):
    """创建隔离数据库、可发布节点和超级管理员会话。"""
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
                    username="release-admin",
                    password=make_password("release-admin-password-123"),
                    is_superuser=True,
                    is_active=True,
                )
                session.add(user)
                session.flush()
                user_id = user.id
                credential = Credential(
                    name="release-ssh",
                    username="deploy",
                    auth_type="password",
                    password=encrypt_secret(
                        app.state.credential_encryption_key, "release-secret"
                    ),
                    is_enabled=True,
                    created_by=user_id,
                )
                session.add(credential)
                session.flush()
                node = Node(
                    hostname="release-node",
                    ip="192.0.2.90",
                    port=22,
                    credential_id=credential.id,
                    environment="test",
                    status="online",
                    nginx_available=True,
                    created_by=user_id,
                )
                session.add(node)
                session.flush()
                node_id = node.id
                for index, status in enumerate(
                    (
                        "not_synced",
                        "modified",
                        "synced",
                        "failed",
                        "orphaned",
                        "marked_deleted",
                    )
                ):
                    config = Config(
                        name="release-config-{}".format(index),
                        created_by=user_id,
                    )
                    session.add(config)
                    session.flush()
                    session.add(
                        ConfigBinding(
                            config_id=config.id,
                            node_id=node_id,
                            remote_path="/etc/nginx/conf.d/{}.conf".format(index),
                            content="server {} {{}}".format(index),
                            current_version=1,
                            sync_status=status,
                            created_by=user_id,
                        )
                    )

        login_page = client.get("/login/")
        token = re.search(
            r'name="csrf_token" value="([^"]+)"', login_page.text
        ).group(1)
        login = client.post(
            "/login/",
            data={
                "csrf_token": token,
                "username": "release-admin",
                "password": "release-admin-password-123",
            },
        )
        assert login.status_code == 302
        yield client, node_id


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("pending", {"not_synced", "modified"}),
        ("synced", {"synced"}),
        ("failed", {"failed"}),
        ("orphaned", {"orphaned"}),
        ("marked_deleted", {"marked_deleted"}),
    ],
)
def test_release_binding_api_filters_each_status(release_client, status, expected):
    """验证展开列表只返回所选状态且待推送合并两个本地状态。"""
    client, node_id = release_client

    response = client.get(
        "/api/releases/nodes/{}/bindings".format(node_id),
        params={"sync_status": status, "page_size": 100},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == len(expected)
    assert {item["sync_status"] for item in payload["bindings"]} == expected


def test_release_history_search_deep_link_and_empty_status(release_client):
    """验证批次深链回填默认搜索框且默认历史 API 请求无需空状态值。"""
    client, _node_id = release_client
    batch_number = "release-261009-0004"

    page = client.get("/releases/", params={"search": batch_number})
    history = client.get("/api/releases/history")
    empty_status = client.get("/api/releases/history", params={"status": ""})

    assert page.status_code == 200
    assert 'value="{}"'.format(batch_number) in page.text
    assert 'id="historyBatch"' not in page.text
    assert 'id="historyNodeIp"' not in page.text
    assert "筛选</button>" not in page.text
    assert "data-query-submit-on-enter" in page.text
    assert history.status_code == 200
    assert empty_status.status_code == 422


def test_release_node_rows_toggle_bindings_on_row_click(release_client):
    """验证节点行点击绑定了展开操作且复选框仍由独立事件处理。"""
    client, _node_id = release_client
    page = client.get("/releases/center/")
    script = (Path(__file__).parents[1] / "static/js/releases.js").read_text(
        encoding="utf-8"
    )

    assert page.status_code == 200, page.text
    assert "/static/js/releases.js?v=7" in page.text
    assert '".release-node-row"' in script
    assert 'toggleNode(this.getAttribute("data-node-row"))' in script
    assert '"[data-node-select]"' in script


@pytest.mark.parametrize(
    ("exit_code", "stdout_text", "stderr_text", "expected_log"),
    [
        (0, "directory ready", "", "远程命令输出：directory ready"),
        (1, "", "mkdir: Permission denied", "远程错误输出：mkdir: Permission denied"),
    ],
)
def test_release_remote_step_logs_success_and_failure_output(
    exit_code, stdout_text, stderr_text, expected_log
):
    """验证发布远程步骤保留正常输出和异常诊断。"""
    logs = []

    class FakeChannel:
        """提供模拟命令退出码。"""

        def recv_exit_status(self):
            """返回本次模拟命令退出码。"""
            return exit_code

    class FakeStream:
        """提供 SSH 通道读取和退出状态。"""

        channel = FakeChannel()

        def __init__(self, value):
            self.value = value

        def read(self):
            """返回模拟的 SSH 命令输出字节。"""
            return self.value.encode("utf-8")

    class FakeClient:
        """返回预置的远程命令正常及错误输出。"""

        def exec_command(self, _command, timeout):
            """构造 stdout/stderr 流并忽略超时参数。"""
            del timeout
            return (
                None,
                FakeStream(stdout_text),
                FakeStream(stderr_text),
            )

    class FakeContext:
        """收集供断言使用的任务日志。"""

        def append_log(self, message, level="info"):
            """追加一条任务日志。"""
            logs.append((message, level))

    status, output = release_services._remote_step(
        FakeClient(),
        "mkdir -p -- /data/conf/conf.d",
        "创建远程目标目录",
        context=FakeContext(),
        item={
            "hostname": "release-node",
            "ip": "192.0.2.90",
            "config_name": "grafana",
        },
        log_output=True,
    )

    assert status == exit_code
    assert output == "\n".join(filter(None, (stdout_text, stderr_text)))
    assert any(expected_log in message for message, _level in logs)


def test_release_deploy_creates_target_parent_before_copy(monkeypatch):
    """验证发布以 SSH 命令创建目标父目录后再复制配置。"""
    content = "server { listen 80; }"
    expected_md5 = hashlib.md5(content.encode("utf-8")).hexdigest()
    commands = []

    class FakeSftp:
        """记录临时文件上传路径。"""

        def putfo(self, _file_object, _remote_path):
            """模拟 SFTP 临时文件上传。"""
            return None

        def close(self):
            """模拟关闭 SFTP 客户端。"""
            return None

    class FakeClient:
        """提供发布执行器依赖的 SFTP 接口。"""

        def open_sftp(self):
            """返回模拟 SFTP 客户端。"""
            return FakeSftp()

    def fake_remote_step(_client, command, _label, **_kwargs):
        """记录命令并返回发布流程需要的检查结果。"""
        commands.append(command)
        if command.startswith("wc -c"):
            return 0, str(len(content.encode("utf-8")))
        return 0, ""

    monkeypatch.setattr(
        release_services, "_backup_remote_file", lambda *_args, **_kwargs: (None, "")
    )
    monkeypatch.setattr(
        release_services,
        "_remote_md5",
        lambda *_args, **_kwargs: (expected_md5, ""),
    )
    monkeypatch.setattr(release_services, "_remote_step", fake_remote_step)
    item = {
        "remote_path": "/data/conf/conf.d/grafana.conf",
        "content": content,
        "hostname": "release-node",
        "binding_id": 1,
        "nginx_path": "/usr/sbin/nginx",
    }

    succeeded, _backup, content_md5, message = release_services._deploy_binding_file(
        FakeClient(), item, "/var/backups", 7
    )

    mkdir_index = next(
        index for index, command in enumerate(commands) if command.startswith("mkdir -p")
    )
    copy_index = next(
        index for index, command in enumerate(commands) if command.startswith("cp --")
    )
    assert succeeded, message
    assert content_md5 == expected_md5
    assert commands[mkdir_index] == "mkdir -p -- /data/conf/conf.d"
    assert mkdir_index < copy_index
