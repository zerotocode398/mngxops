"""验证 Nginx 卸载选择、路径门禁和 systemd 探测。"""

from dataclasses import replace
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
from ngxops.nginx_uninstall.models import NginxUninstallRun
from ngxops.settings.models import SystemSetting
from ngxops.nodes.models import Node
from ngxops.tasks.models import Task


@pytest.fixture
def uninstall_client(tmp_path):
    """创建隔离卸载应用和状态不同的节点。"""
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
                admin = User(
                    username="uninstall-admin",
                    password=make_password("uninstall-admin-password-123"),
                    is_superuser=True,
                    is_active=True,
                )
                operator = User(
                    username="uninstall-operator",
                    password=make_password("uninstall-operator-password-123"),
                    is_active=True,
                )
                session.add_all([admin, operator])
                session.flush()
                credential = Credential(
                    name="uninstall-ssh",
                    username="deploy",
                    auth_type="password",
                    password=encrypt_secret(
                        app.state.credential_encryption_key, "uninstall-ssh-secret"
                    ),
                    is_enabled=True,
                    created_by=admin.id,
                )
                session.add(credential)
                session.flush()
                nodes = [
                    Node(
                        hostname="nginx-live",
                        ip="198.51.100.10",
                        port=22,
                        credential_id=credential.id,
                        status="online",
                        nginx_available=True,
                        nginx_path="/data/app/nginx/sbin/nginx",
                        created_by=admin.id,
                    ),
                    Node(
                        hostname="nginx-offline",
                        ip="198.51.100.11",
                        port=22,
                        credential_id=credential.id,
                        status="offline",
                        nginx_available=True,
                        created_by=admin.id,
                    ),
                    Node(
                        hostname="nginx-unknown",
                        ip="198.51.100.12",
                        port=22,
                        credential_id=credential.id,
                        status="online",
                        nginx_available=False,
                        created_by=admin.id,
                    ),
                ]
                session.add_all(nodes)
                session.flush()
                task = Task(
                    operation_type="nginx_uninstall",
                    status="success",
                    progress=100,
                    detail="Nginx 已卸载",
                    source_batch="UN-261027-0001",
                    trigger_user_id=operator.id,
                )
                session.add(task)
                session.flush()
                session.add(
                    NginxUninstallRun(
                        task_id=task.id,
                        node_id=nodes[0].id,
                        batch_number=task.source_batch,
                        node_hostname=nodes[0].hostname,
                        node_ip=nodes[0].ip,
                        install_origin="source",
                        resolved_prefix="/data/app/nginx",
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
                "username": "uninstall-admin",
                "password": "uninstall-admin-password-123",
            },
        )
        assert login.status_code == 302
        yield client, app


def test_uninstall_systemd_probe_checks_nginx_service_once(monkeypatch):
    """验证卸载探测不重复查询 nginx 的等价 unit 名称。"""
    from ngxops.nginx_uninstall import services

    commands = []

    def fake_run_command(_client, command, context=None, timeout=45):
        """返回未运行且未启用的 systemd 状态及可管理目录。"""
        commands.append(command)
        if command == "command -v systemctl >/dev/null 2>&1":
            return 0, ""
        if command.startswith("systemctl is-active"):
            return 3, "inactive"
        if command.startswith("systemctl is-enabled"):
            return 1, "disabled"
        if command == "test -w /etc/systemd/system":
            return 0, ""
        raise AssertionError("收到意外探测命令：{}".format(command))

    monkeypatch.setattr(services, "_run_command", fake_run_command)
    result = services._systemd_info(object())

    assert result == {
        "mode": "binary",
        "unit": "",
        "running": False,
        "can_manage": True,
        "use_sudo": False,
    }
    assert commands == [
        "command -v systemctl >/dev/null 2>&1",
        "systemctl is-active nginx.service 2>/dev/null || true",
        "systemctl is-enabled nginx.service 2>/dev/null || true",
        "test -w /etc/systemd/system",
    ]


@pytest.mark.parametrize(
    "rpm_result, dpkg_result, expected",
    [
        ((0, "nginx\n"), (1, ""), ("package", "rpm", "nginx")),
        ((1, ""), (0, "nginx: /usr/sbin/nginx\n"), ("package", "deb", "nginx")),
        ((1, ""), (1, ""), ("source", "", "")),
    ],
)
def test_package_origin_uses_nginx_binary_for_detection(
    monkeypatch, rpm_result, dpkg_result, expected
):
    """验证二进制路径可识别 RPM、DPKG 归属或源码安装。"""
    from ngxops.nginx_uninstall import services

    commands = []

    def run_command(_client, command, context=None, timeout=45):
        """记录包归属查询并返回预设结果。"""
        commands.append(command)
        if command.startswith("rpm -qf"):
            return rpm_result
        if command.startswith("dpkg -S"):
            return dpkg_result
        raise AssertionError("收到意外查询命令：{}".format(command))

    monkeypatch.setattr(services, "_run_command", run_command)
    result = services._package_origin(object(), "/usr/sbin/nginx")

    assert commands[0] == "rpm -qf --queryformat '%{NAME}' /usr/sbin/nginx 2>/dev/null"
    assert result["binary"] == "/usr/sbin/nginx"
    assert (result["origin"], result["manager"], result["package"]) == expected


def test_uninstall_home_history_search_and_default_node_filter(uninstall_client):
    """验证卸载页面按操作人搜索并默认仅列在线且识别 Nginx 的节点。"""
    client, _app = uninstall_client

    home = client.get("/nginx/uninstall/?search=uninstall-operator")
    assert home.status_code == 200, home.text
    assert "UN-261027-0001" in home.text
    assert "uninstall-operator" in home.text
    assert "查看全部" in home.text
    assert "操作人" in home.text

    history = client.get("/nginx/uninstall/history/?search=uninstall-operator")
    assert history.status_code == 200, history.text
    assert "UN-261027-0001" in history.text
    assert "uninstall-operator" in history.text
    assert "操作人" in history.text

    nodes = client.get("/api/nginx/uninstall/nodes")
    assert nodes.status_code == 200, nodes.text
    assert [item["hostname"] for item in nodes.json()["nodes"]] == ["nginx-live"]


def test_uninstall_home_and_center_follow_compact_lifecycle_layout(uninstall_client):
    """验证卸载首页简化操作入口，中心使用升级风格的节点选择器。"""
    client, app = uninstall_client

    home = client.get("/nginx/uninstall/?search=does-not-match")
    assert home.status_code == 200, home.text
    assert "开始卸载" in home.text
    assert "UN-261027-0001" in home.text
    assert "uninstallRecentSearch" not in home.text

    center = client.get("/nginx/uninstall/center/")
    assert center.status_code == 200, center.text
    assert 'data-max-count="3"' in center.text
    assert 'id="uninstallNodePickerModal"' in center.text
    assert 'id="uninstallNodePageSize"' in center.text
    assert 'data-query-tags' in center.text
    assert "已选 0 台 / 上限 3" not in center.text
    assert "1. 选择节点" not in center.text
    assert "nginx-live" in center.text
    assert "nginx-offline" not in center.text
    assert "nginx-unknown" not in center.text

    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            session.add(
                SystemSetting(
                    key="node.batch_max_count",
                    value="2",
                    value_type="integer",
                    group_name="节点管理",
                    label="批量操作最大节点数",
                )
            )
    limited = client.get("/nginx/uninstall/center/")
    assert limited.status_code == 200, limited.text
    assert 'data-max-count="2"' in limited.text
    assert "最多 2 台" in limited.text


def test_uninstall_preview_key_error_is_logged_and_incomplete_metadata_fails_closed(
    uninstall_client, monkeypatch, caplog
):
    """验证预览异常可按节点定位，缺失检查字段时不会放行卸载。"""
    from ngxops.nginx_uninstall import services

    client, app = uninstall_client
    node_response = client.get("/api/nginx/uninstall/nodes")
    node_id = node_response.json()["nodes"][0]["id"]

    def raise_key_error(_target):
        """模拟远程检查中出现的字段访问异常。"""
        raise KeyError("missing inspection field")

    monkeypatch.setattr(services, "_inspect_target", raise_key_error)
    result = services._preview_target(
        app.state.database.session_factory,
        node_id,
        app.state.credential_encryption_key,
        "/tmp/nginx-upgrade",
        "/opt/app/mngxops",
    )
    assert result["eligible"] is False
    assert result["gate_message"] == "远程预览失败（KeyError）"
    assert "node_id={}".format(node_id) in caplog.text

    monkeypatch.setattr(
        services,
        "_inspect_target",
        lambda _target: {
            "package": {"origin": "source", "manager": "", "package": ""},
            "systemd": {"mode": "binary", "unit": "", "can_manage": False},
            "prefix": "/data/app/nginx",
            "running": False,
            "paths": [{"key": "--conf-path"}],
        },
    )
    incomplete = services._preview_target(
        app.state.database.session_factory,
        node_id,
        app.state.credential_encryption_key,
        "/tmp/nginx-upgrade",
        "/opt/app/mngxops",
    )
    assert incomplete["eligible"] is False
    assert incomplete["gate_message"] == "远程预览数据不完整，请重新探测"

    monkeypatch.setattr(
        services,
        "_inspect_target",
        lambda _target: {
            "package": {"origin": "source", "manager": "", "package": ""},
            "systemd": {"mode": "binary", "unit": "", "can_manage": False},
            "prefix": "/data/app/nginx",
            "paths": [],
        },
    )
    missing_runtime_state = services._preview_target(
        app.state.database.session_factory,
        node_id,
        app.state.credential_encryption_key,
        "/tmp/nginx-upgrade",
        "/opt/app/mngxops",
    )
    assert missing_runtime_state["eligible"] is False
    assert missing_runtime_state["gate_message"] == "远程预览数据不完整，请重新探测"


def test_configure_paths_keep_nested_nginx_locations():
    """验证 nginx -V 的子路径保留以支持目录树联动选择。"""
    from ngxops.nginx_uninstall.services import _configure_paths

    output = (
        "nginx version: nginx/1.26.1\n"
        "configure arguments: --prefix=/data/app/nginx "
        "--conf-path=/data/app/nginx/conf/nginx.conf "
        "--sbin-path=/data/app/nginx/sbin/nginx"
    )
    prefix, paths = _configure_paths(output, "")

    assert prefix == "/data/app/nginx"
    assert {item["path"] for item in paths} == {
        "/data/app/nginx",
        "/data/app/nginx/conf/nginx.conf",
        "/data/app/nginx/sbin/nginx",
    }


def test_uninstall_prefix_check_allows_nginx_tree_and_rejects_foreign_entries(monkeypatch):
    """验证源码 prefix 仅在顶层内容归属可识别时允许递归删除。"""
    from ngxops.nginx_uninstall import services

    commands = []
    listing = "/data/app/nginx/conf\n/data/app/nginx/sbin\n"

    def fake_run_command(_client, command, context=None, timeout=45):
        """模拟检查 prefix 类型和顶层目录内容。"""
        commands.append(command)
        if command.startswith("test -e "):
            return 0, ""
        if command.startswith("test -d "):
            return 0, ""
        if command.startswith("find "):
            return 0, listing
        raise AssertionError("收到意外远程命令：{}".format(command))

    monkeypatch.setattr(services, "_run_command", fake_run_command)
    services._assert_nginx_prefix_contents(
        object(),
        "/data/app/nginx",
        [
            {"key": "prefix", "path": "/data/app/nginx"},
            {"key": "--conf-path", "path": "/data/app/nginx/conf/nginx.conf"},
            {"key": "--sbin-path", "path": "/data/app/nginx/sbin/nginx"},
        ],
        object(),
    )
    assert commands[-1].startswith("find ")

    listing = "/data/app/nginx/conf\n/data/app/nginx/payment-api\n"
    with pytest.raises(ValueError, match="已阻止递归删除"):
        services._assert_nginx_prefix_contents(
            object(),
            "/data/app/nginx",
            [{"key": "--conf-path", "path": "/data/app/nginx/conf/nginx.conf"}],
            object(),
        )


def test_uninstall_aborts_when_live_nginx_v_cannot_be_read(monkeypatch):
    """验证不能读取实时编译参数时不会进入任何删除路径。"""
    from ngxops.nginx_uninstall import services

    class FakeContext:
        """提供卸载单元测试需要的任务接口。"""

        def check_cancelled(self):
            """测试任务始终保持运行。"""
            return None

        def append_log(self, message, level="info"):
            """忽略任务日志。"""
            return None

        def append_detail_log(self, message, level="info"):
            """忽略完整日志。"""
            return None

    class FakeClient:
        """记录 SSH 客户端关闭状态。"""

        def __init__(self):
            """初始化客户端状态。"""
            self.closed = False

        def close(self):
            """标记 SSH 客户端已关闭。"""
            self.closed = True

    client = FakeClient()
    commands = []
    monkeypatch.setattr(
        services,
        "_load_run_target",
        lambda *_args: (
            {"id": 1, "hostname": "nginx-live", "ip": "198.51.100.10"},
            {"origin": "source", "prefix": "/data/app/nginx", "options": {}},
            "",
        ),
    )
    monkeypatch.setattr(services, "_phase", lambda *_args: None)
    monkeypatch.setattr(services, "_connect_target", lambda *_args: client)
    monkeypatch.setattr(
        services,
        "_inspect_client",
        lambda *_args: {
            "package": {"origin": "source"},
            "nginx_v_ok": False,
            "prefix": "/data/app/nginx",
            "paths": [],
            "running": False,
            "systemd": {"mode": "binary"},
        },
    )
    monkeypatch.setattr(
        services,
        "_run_command",
        lambda _client, command, *_args, **_kwargs: (
            commands.append(command) or (0, "")
        ),
    )
    outcome = services._run_uninstall(
        object(), b"unused", 1, FakeContext()
    )

    assert outcome.status == "failed"
    assert "nginx -V" in outcome.detail
    assert commands == []
    assert client.closed is True
