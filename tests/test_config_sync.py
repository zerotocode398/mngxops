"""验证配置发现、单节点同步和批量同步任务边界。"""

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import re
from threading import Event
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ngxops.accounts.models import User
from ngxops.accounts.passwords import make_password
from ngxops.app import create_app
from ngxops.config import get_settings
from ngxops.configs.discovery import _quote_glob_pattern, discover_remote_configs
from ngxops.configs.tasks import (
    MAX_RESULT_DETAIL_ITEMS,
    MAX_RESULT_ERROR_ITEMS,
    _compact_node_result,
)
from ngxops.configs.models import (
    BindingVersion,
    Config,
    ConfigBinding,
    ConfigSyncSetting,
)
from ngxops.credentials.crypto import encrypt_secret
from ngxops.credentials.models import Credential
from ngxops.database.migration_runner import upgrade_database
from ngxops.database.session import session_scope
from ngxops.nodes.models import Node, NodeGroup, NodeSyncSetting
from ngxops.tasks.executor import MAX_RESULT_LENGTH, _serialize_result_tree
from ngxops.tasks.models import Task


@pytest.fixture
def config_client(tmp_path):
    """创建隔离 SQLite、启用凭证、在线节点和超级管理员会话。"""
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
                    username="config-admin",
                    password=make_password("config-admin-password-123"),
                    is_superuser=True,
                    is_active=True,
                )
                session.add(user)
                session.flush()
                user_id = user.id
                credential = Credential(
                    name="config-ssh",
                    username="deploy",
                    auth_type="password",
                    password=encrypt_secret(
                        app.state.credential_encryption_key,
                        "not-for-task-storage",
                    ),
                    is_enabled=True,
                    created_by=user_id,
                )
                session.add(credential)
                session.flush()
                node = Node(
                    hostname="config-node",
                    ip="192.0.2.80",
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
        login_page = client.get("/login/")
        token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(
            1
        )
        login = client.post(
            "/login/",
            data={
                "csrf_token": token,
                "username": "config-admin",
                "password": "config-admin-password-123",
            },
        )
        assert login.status_code == 302
        yield client, app, token, node_id, user_id


def _wait_for_task(client, task_id):
    """轮询真实任务状态直到进入终态。"""
    for _index in range(150):
        response = client.get("/api/tasks/{}".format(task_id))
        assert response.status_code == 200, response.text
        task = response.json()
        if task["status"] in ("success", "failed", "cancelled"):
            return task
        time.sleep(0.02)
    raise AssertionError("后台任务未在时限内结束")


def _create_binding(session, node_id, user_id, name, path, content, status="synced"):
    """创建配置标签、节点绑定及其初始版本快照。"""
    config = Config(name=name, default_remote_path=path, created_by=user_id)
    session.add(config)
    session.flush()
    binding = ConfigBinding(
        config_id=config.id,
        node_id=node_id,
        remote_path=path,
        content=content,
        current_version=1,
        sync_status=status,
        synced_version=1 if status == "synced" else None,
        created_by=user_id,
    )
    session.add(binding)
    session.flush()
    session.add(
        BindingVersion(
            binding_id=binding.id,
            version=1,
            content=content,
            remark="初始版本",
            created_by=user_id,
        )
    )
    return binding.id


def test_remote_discovery_expands_nested_and_relative_includes(monkeypatch):
    """扫描器复用 SSH 连接、解析相对路径和 glob 并安全引用命令路径。"""
    commands = []
    response_map = {
        "cat -- /etc/nginx/nginx.conf": (
            b"include conf.d/*.conf; include ../sites/site.conf;"
        ),
        "ls -1d -- /etc/nginx/conf.d/*.conf 2>/dev/null": (
            b"/etc/nginx/conf.d/app.conf\n/etc/nginx/conf.d/mime.types\n"
        ),
        "cat -- /etc/nginx/conf.d/app.conf": b"server { listen 80; }",
        "cat -- /etc/sites/site.conf": b"server { listen 443; }",
    }

    class FakeChannel:
        """提供伪造 SSH 流使用的成功退出状态。"""

        def recv_exit_status(self):
            """返回成功命令状态。"""
            return 0

    class FakeStream:
        """将预设命令输出映射为 Paramiko 风格流。"""

        def __init__(self, value):
            """保存一个命令的伪造输出。"""
            self.value = value
            self.channel = FakeChannel()

        def read(self):
            """返回预设字节输出。"""
            return self.value

    class FakeSshClient:
        """记录命令并提供预设主配置和 include 内容。"""

        def exec_command(self, command, timeout=None):
            """返回匹配命令的伪造 stdout/stderr 流。"""
            commands.append(command)
            return None, FakeStream(response_map[command]), FakeStream(b"")

        def close(self):
            """模拟关闭 SSH 连接。"""
            return None

    class FakeContext:
        """提供发现任务需要的协作式取消检查点。"""

        def check_cancelled(self):
            """保持测试中的远程扫描继续运行。"""
            return None

    client = FakeSshClient()
    monkeypatch.setattr(
        "ngxops.configs.discovery._connect_ssh",
        lambda *args: (client, ""),
    )
    paths = []
    files, errors = discover_remote_configs(
        {
            "id": 1,
            "hostname": "scan-node",
            "ip": "192.0.2.1",
            "port": 22,
            "username": "deploy",
            "auth_type": "password",
            "password": "secret",
            "private_key": "",
        },
        "/etc/nginx/nginx.conf",
        FakeContext(),
        progress_callback=lambda _count, path: paths.append(path),
    )
    assert errors == []
    assert [item["path"] for item in files] == [
        "/etc/nginx/nginx.conf",
        "/etc/nginx/conf.d/app.conf",
        "/etc/sites/site.conf",
    ]
    assert len(commands) == 4
    assert paths == [item["path"] for item in files]
    quoted = _quote_glob_pattern("/etc/nginx/conf.d/*.conf; touch /tmp/pwned")
    assert "touch /tmp/pwned" in quoted
    assert "'" in quoted


def test_discovery_task_returns_paths_without_file_contents(config_client, monkeypatch):
    """发现任务持久化路径清单、真实进度且不暴露正文或凭证明文。"""
    client, app, csrf_token, node_id, _user_id = config_client
    main_conf_path = "/opt/nginx/conf/nginx.conf"
    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            session.get(Node, node_id).sync_setting = NodeSyncSetting(
                main_conf_path=main_conf_path
            )
    page = client.get("/configs/sync/")
    assert page.status_code == 200, page.text
    assert "config-node" in page.text
    assert 'value="{}"'.format(main_conf_path) in page.text
    assert "/static/js/config-sync.js" in page.text
    seen_paths = []

    def fake_discovery(
        target, main_path, context, progress_callback=None, max_depth=3, client=None
    ):
        """返回一份隔离测试使用的远程发现结果。"""
        seen_paths.append(main_path)
        if progress_callback:
            progress_callback(1, main_path)
        return [
            {
                "path": main_path,
                "name": "nginx.conf",
                "content": "server { # private config body }",
            }
        ], []

    monkeypatch.setattr(
        "ngxops.configs.tasks.discover_remote_configs",
        fake_discovery,
    )
    response = client.post(
        "/api/configs/discover",
        headers={"X-CSRFToken": csrf_token},
        json={"node_id": node_id},
    )
    assert response.status_code == 202, response.text
    task = _wait_for_task(client, response.json()["task_id"])
    assert task["status"] == "success"
    assert task["progress"] == 100
    assert task["result_tree"]["nodes"][0]["files"] == [{"path": main_conf_path}]
    assert seen_paths == [main_conf_path]
    assert "private config body" not in str(task["result_tree"])
    with session_scope(app.state.database.session_factory) as session:
        stored = session.get(Task, task["id"])
        assert "not-for-task-storage" not in (stored.detail + stored.result_tree_json)
        assert session.scalar(select(Config).where(Config.name == "nginx.conf")) is None
        config_sync_setting = session.scalar(
            select(ConfigSyncSetting).where(ConfigSyncSetting.node_id == node_id)
        )
        assert config_sync_setting is None
        assert session.get(NodeSyncSetting, node_id).main_conf_path == main_conf_path

    edited_path = "/opt/nginx/alternate.conf"
    edited = client.post(
        "/api/configs/discover",
        headers={"X-CSRFToken": csrf_token},
        json={"node_id": node_id, "main_conf_path": edited_path},
    )
    assert edited.status_code == 202, edited.text
    assert _wait_for_task(client, edited.json()["task_id"])["status"] == "success"
    assert seen_paths == [main_conf_path, edited_path]
    with session_scope(app.state.database.session_factory) as session:
        assert session.get(NodeSyncSetting, node_id).main_conf_path == edited_path


def test_discovery_lists_included_files_and_syncs_one_or_many_paths(
    config_client,
    monkeypatch,
):
    """发现主配置中的子文件并支持单路径和多路径同步。"""
    client, app, csrf_token, node_id, _user_id = config_client
    main_conf_path = "/srv/nginx/conf/nginx.conf"
    child_paths = [
        "/srv/nginx/conf.d/app.conf",
        "/srv/nginx/conf.d/api.conf",
    ]
    files = [
        {
            "path": main_conf_path,
            "name": "nginx.conf",
            "content": "include /srv/nginx/conf.d/*.conf;",
        },
        {
            "path": child_paths[0],
            "name": "app.conf",
            "content": "server { listen 80; }",
        },
        {
            "path": child_paths[1],
            "name": "api.conf",
            "content": "server { listen 8080; }",
        },
    ]
    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            session.get(Node, node_id).sync_setting = NodeSyncSetting(
                main_conf_path=main_conf_path
            )

    seen_paths = []

    def fake_discovery(
        target, main_path, context, progress_callback=None, max_depth=3, client=None
    ):
        """返回主配置和两条 include 文件路径供页面任务验证。"""
        seen_paths.append(main_path)
        if progress_callback:
            for index, item in enumerate(files, 1):
                progress_callback(index, item["path"])
        return files, []

    class FakeSshClient:
        """提供同步任务需要的轻量 SSH 客户端替身。"""

        def close(self):
            """模拟关闭 SSH 连接。"""
            return None

    monkeypatch.setattr(
        "ngxops.configs.tasks.discover_remote_configs",
        fake_discovery,
    )
    monkeypatch.setattr(
        "ngxops.configs.tasks._connect_ssh",
        lambda *args: (FakeSshClient(), ""),
    )

    discovery = client.post(
        "/api/configs/discover",
        headers={"X-CSRFToken": csrf_token},
        json={"node_id": node_id},
    )
    assert discovery.status_code == 202, discovery.text
    discovery_task = _wait_for_task(client, discovery.json()["task_id"])
    discovered_paths = [
        item["path"] for item in discovery_task["result_tree"]["nodes"][0]["files"]
    ]
    assert discovered_paths == [main_conf_path] + child_paths
    assert seen_paths == [main_conf_path]
    assert any(
        "config-node (192.0.2.80) 发现配置 {}".format(path) in log["message"]
        for path in discovered_paths
        for log in discovery_task["logs"]
    )

    single = client.post(
        "/api/configs/sync",
        headers={"X-CSRFToken": csrf_token},
        json={
            "node_id": node_id,
            "mode": "partial",
            "selected_paths": [child_paths[0]],
        },
    )
    assert single.status_code == 202, single.text
    single_task = _wait_for_task(client, single.json()["task_id"])
    single_result = single_task["result_tree"]["nodes"][0]
    assert [item["path"] for item in single_result["created"]] == [child_paths[0]]

    multiple = client.post(
        "/api/configs/sync",
        headers={"X-CSRFToken": csrf_token},
        json={
            "node_id": node_id,
            "mode": "partial",
            "selected_paths": child_paths,
        },
    )
    assert multiple.status_code == 202, multiple.text
    multiple_task = _wait_for_task(client, multiple.json()["task_id"])
    multiple_result = multiple_task["result_tree"]["nodes"][0]
    processed_paths = {
        item["path"]
        for key in ("created", "updated", "skipped")
        for item in multiple_result[key]
    }
    assert processed_paths == set(child_paths)
    assert seen_paths == [main_conf_path, main_conf_path, main_conf_path]


def test_compact_batch_result_stays_within_task_result_limit():
    """批量结果裁剪明细后保留计数且可写入统一任务结果树上限。"""
    node_results = []
    for node_id in range(1, 4):
        item = {"name": "n" * 255, "path": "/" + ("p" * 499)}
        error = {"path": "/" + ("e" * 499), "message": "x" * 128}
        result = {
            "node_id": node_id,
            "hostname": "host-{}".format(node_id),
            "ip": "192.0.2.{}".format(node_id),
            "mode": "full",
            "created": [dict(item) for _index in range(1000)],
            "updated": [],
            "orphaned": [],
            "deleted": [],
            "skipped": [],
            "errors": [dict(error) for _index in range(100)],
        }
        node_results.append(_compact_node_result(result))

    tree = {
        "summary": {"total": 3, "success": 3, "failed": 0},
        "nodes": node_results,
    }
    serialized = _serialize_result_tree(tree)

    for compact in node_results:
        assert len(compact["created"]) == MAX_RESULT_DETAIL_ITEMS
        assert len(compact["errors"]) == MAX_RESULT_ERROR_ITEMS
        assert compact["counts"]["created"] == 1000
        assert compact["omitted_detail_count"] == 925
        assert compact["omitted_error_count"] == 80
    assert len(serialized.encode("utf-8")) <= MAX_RESULT_LENGTH


def test_sync_wizard_uses_host_ip_search_and_default_nginx_filter(config_client):
    """节点同步向导只按主机/IP 搜索并默认限制为已识别 Nginx。"""
    client, app, _csrf_token, node_id, user_id = config_client
    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            matched = session.get(Node, node_id)
            matched.hostname = "edge-api-01"
            matched.ip = "198.51.100.10"
            prod_group = NodeGroup(name="prod-east", created_by=user_id)
            blue_group = NodeGroup(name="blue", created_by=user_id)
            test_group = NodeGroup(name="test-east", created_by=user_id)
            session.add_all([prod_group, blue_group, test_group])
            matched.groups.extend([prod_group, blue_group])
            session.add_all(
                [
                    Node(
                        hostname="edge-api-02",
                        ip="198.51.100.11",
                        port=22,
                        credential_id=matched.credential_id,
                        environment="test",
                        status="online",
                        nginx_available=True,
                        groups=[test_group],
                        created_by=user_id,
                    ),
                    Node(
                        hostname="edge-api-03",
                        ip="203.0.113.12",
                        port=22,
                        credential_id=matched.credential_id,
                        environment="test",
                        status="online",
                        nginx_available=True,
                        groups=[prod_group, blue_group],
                        created_by=user_id,
                    ),
                    Node(
                        hostname="edge-api-04",
                        ip="198.51.100.13",
                        port=22,
                        credential_id=matched.credential_id,
                        environment="test",
                        status="online",
                        nginx_available=False,
                        groups=[prod_group, blue_group],
                        created_by=user_id,
                    ),
                ]
            )

    response = client.get(
        "/configs/sync/?search=edge-api%2C198.51.100&group_search=prod%2Cblue&nginx_available=all"
    )

    assert response.status_code == 200, response.text
    assert "edge-api-01" in response.text
    assert "edge-api-02" in response.text
    assert "edge-api-03" not in response.text
    assert "edge-api-04" not in response.text
    assert "节点组 / 标签" not in response.text
    assert "Nginx 状态" not in response.text
    assert "批量任务日志" not in response.text
    assert "可同步节点 3 个" not in response.text
    assert "config-sync-toolbar" in response.text
    assert "全选可同步节点" in response.text
    assert "最多选择 3 个节点" in response.text
    assert "批量同步" in response.text
    assert "config-discovery-select-all" in response.text
    assert "config-sync-terminal" not in response.text
    assert "全量同步" in response.text


def test_sync_wizard_displays_last_sync_time_in_beijing_time(config_client):
    """同步向导将 UTC 数据库时间按 UTC+8 展示。"""
    client, app, _csrf_token, node_id, user_id = config_client
    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            binding_id = _create_binding(
                session,
                node_id,
                user_id,
                "nginx.conf",
                "/etc/nginx/nginx.conf",
                "events {}",
            )
            session.get(ConfigBinding, binding_id).last_sync_time = datetime(
                2026, 10, 9, 3, 15
            )

    response = client.get("/configs/sync/")

    assert response.status_code == 200, response.text
    assert "2026-10-09 11:15" in response.text


def test_config_list_search_and_filters_are_visible(config_client):
    """配置列表使用多条件搜索、默认 Nginx 开关和清晰的绑定状态栏。"""
    client, app, _csrf_token, node_id, user_id = config_client
    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            second = Node(
                hostname="other-node",
                ip="192.0.2.81",
                port=22,
                credential_id=session.get(Node, node_id).credential_id,
                environment="test",
                status="online",
                nginx_available=True,
                created_by=user_id,
            )
            session.add(second)
            session.flush()
            second_node_id = second.id
            _create_binding(
                session,
                node_id,
                user_id,
                "edge-frontend.conf",
                "/etc/nginx/conf.d/edge-frontend.conf",
                "server {}",
            )

    response = client.get("/configs/?search=config-node%2Cedge-frontend")
    assert response.status_code == 200, response.text
    assert "config-node" in response.text
    assert 'href="/configs/nodes/{}/?list_query='.format(node_id) in response.text
    assert "edge-frontend.conf" not in response.text
    assert "other-node" not in response.text
    assert "data-query-tags" in response.text
    assert "全部节点组" not in response.text
    assert "绑定状态：" in response.text
    assert 'id="nginxOnlyToggle" checked' in response.text
    assert "/static/js/config-list.js?v=3" in response.text
    assert '/configs/nodes/{}/'.format(node_id) in response.text

    non_matching = client.get("/configs/?search=config-node%2Cmissing-term")
    assert non_matching.status_code == 200, non_matching.text
    assert 'href="/configs/nodes/{}/'.format(node_id) not in non_matching.text
    assert '/configs/nodes/{}/'.format(second_node_id) not in non_matching.text
    detail = client.get("/configs/nodes/{}/?list_query=%2Fconfigs%2F".format(node_id))
    assert detail.status_code == 200, detail.text
    assert "edge-frontend.conf" in detail.text


def test_create_config_can_bind_multiple_or_zero_nodes(config_client):
    """新增配置可一次绑定多个节点，也允许创建未绑定标签。"""
    client, app, csrf_token, node_id, user_id = config_client
    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            first = session.get(Node, node_id)
            group = NodeGroup(name="config-prod", created_by=user_id)
            second = Node(
                hostname="config-node-2",
                ip="192.0.2.82",
                port=22,
                credential_id=first.credential_id,
                environment="prod",
                status="online",
                nginx_available=True,
                groups=[group],
                created_by=user_id,
            )
            session.add(second)
            session.flush()
            second_node_id = second.id

    create_page = client.get("/configs/create/")
    assert create_page.status_code == 200, create_page.text
    assert "data-query-tags" in create_page.text
    assert "configNodePageSize" in create_page.text
    assert "config-prod" in create_page.text
    assert "按 Enter 查询" in create_page.text
    assert 'tabindex="0" aria-label="选择节点 config-node"' in create_page.text
    assert "/static/js/config-create.js?v=2" in create_page.text

    headers = {"X-CSRFToken": csrf_token}
    created = client.post(
        "/configs/create/",
        headers=headers,
        data={
            "name": "multi-node-config",
            "default_remote_path": "/etc/nginx/conf.d/multi.conf",
            "template_content": "server {}",
            "description": "multi node",
            "return_to": "/configs/?per_page=10",
            "node_ids": [str(node_id), str(second_node_id)],
        },
    )
    assert created.status_code == 303, created.text
    assert created.headers["location"] == "/configs/?per_page=10"
    with session_scope(app.state.database.session_factory) as session:
        config = session.scalar(
            select(Config).where(Config.name == "multi-node-config")
        )
        bindings = session.scalars(
            select(ConfigBinding).where(ConfigBinding.config_id == config.id)
        ).all()
        assert {binding.node_id for binding in bindings} == {node_id, second_node_id}
        assert all(binding.sync_status == "not_synced" for binding in bindings)
        for binding in bindings:
            assert (
                session.scalar(
                    select(BindingVersion).where(
                        BindingVersion.binding_id == binding.id,
                        BindingVersion.version == 1,
                    )
                )
                is not None
            )

    unbound = client.post(
        "/configs/create/",
        headers=headers,
        data={"name": "unbound-config", "return_to": "/configs/"},
    )
    assert unbound.status_code == 303, unbound.text
    with session_scope(app.state.database.session_factory) as session:
        config = session.scalar(select(Config).where(Config.name == "unbound-config"))
        assert config is not None
        assert (
            session.scalar(
                select(ConfigBinding.id).where(ConfigBinding.config_id == config.id)
            )
            is None
        )


def test_config_delete_notice_uses_global_toast(config_client):
    """配置标签删除后通过全局右上角提示反馈。"""
    client, app, csrf_token, _node_id, user_id = config_client
    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            config = Config(name="delete-toast-config", created_by=user_id)
            session.add(config)
            session.flush()
            config_id = config.id

    deleted = client.post(
        "/configs/{}/delete/".format(config_id),
        headers={"X-CSRFToken": csrf_token},
        data={"return_to": "/configs/"},
    )
    assert deleted.status_code == 303, deleted.text
    page = client.get(deleted.headers["location"])
    assert page.status_code == 200, page.text
    assert "class=\"config-notice\"" in page.text
    assert "data-message=\"配置标签 delete-toast-config 及其绑定已删除\"" in page.text
    assert "alert-dismissible" not in page.text
    assert "/static/js/config-list.js?v=3" in page.text


def test_full_sync_versions_content_marks_missing_and_cleans_delete(
    config_client,
    monkeypatch,
):
    """全量同步更新版本、标记远程缺失且只在远程删除成功后移除绑定。"""
    client, app, csrf_token, node_id, user_id = config_client
    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            app_binding_id = _create_binding(
                session,
                node_id,
                user_id,
                "app.conf",
                "/etc/nginx/conf.d/app.conf",
                "old app",
            )
            gone_binding_id = _create_binding(
                session,
                node_id,
                user_id,
                "gone.conf",
                "/etc/nginx/conf.d/gone.conf",
                "gone",
            )
            modified_binding_id = _create_binding(
                session,
                node_id,
                user_id,
                "local.conf",
                "/etc/nginx/conf.d/local.conf",
                "local edit",
                status="modified",
            )
            _create_binding(
                session,
                node_id,
                user_id,
                "same.conf",
                "/etc/nginx/conf.d/same.conf",
                "same content",
            )
            deleted_binding_id = _create_binding(
                session,
                node_id,
                user_id,
                "deleted.conf",
                "/etc/nginx/conf.d/deleted.conf",
                "pending delete",
                status="marked_deleted",
            )

    def fake_discovery(
        target, main_path, context, progress_callback=None, max_depth=3, client=None
    ):
        """返回仍存在的 app 配置用于全量同步烟测。"""
        return [
            {
                "path": "/etc/nginx/conf.d/app.conf",
                "name": "app.conf",
                "content": "new app content",
            },
            {
                "path": "/etc/nginx/conf.d/same.conf",
                "name": "same.conf",
                "content": "same content",
            },
        ], []

    class FakeChannel:
        """提供成功的远程命令退出状态。"""

        def recv_exit_status(self):
            """返回远程删除成功状态码。"""
            return 0

    class FakeStream:
        """提供 SSH 命令通道和空输出。"""

        channel = FakeChannel()

        def read(self):
            """返回空命令输出。"""
            return b""

    class FakeSshClient:
        """记录安全删除命令并返回成功状态。"""

        def exec_command(self, command, timeout=None):
            """返回成功退出的伪造远程命令流。"""
            assert command == "rm -f -- /etc/nginx/conf.d/deleted.conf"
            return None, FakeStream(), FakeStream()

        def close(self):
            """模拟关闭远程连接。"""
            return None

    monkeypatch.setattr(
        "ngxops.configs.tasks.discover_remote_configs",
        fake_discovery,
    )
    monkeypatch.setattr(
        "ngxops.configs.tasks._connect_ssh",
        lambda *args: (FakeSshClient(), ""),
    )
    response = client.post(
        "/api/configs/sync",
        headers={"X-CSRFToken": csrf_token},
        json={"node_id": node_id, "mode": "full"},
    )
    assert response.status_code == 202, response.text
    task = _wait_for_task(client, response.json()["task_id"])
    assert task["status"] == "success"
    node_result = task["result_tree"]["nodes"][0]
    assert [item["name"] for item in node_result["updated"]] == ["app.conf"]
    assert [item["name"] for item in node_result["skipped"]] == ["same.conf"]
    assert [item["name"] for item in node_result["orphaned"]] == ["gone.conf"]
    assert [item["name"] for item in node_result["deleted"]] == ["deleted.conf"]
    assert any(
        "config-node (192.0.2.80) 跳过配置 /etc/nginx/conf.d/same.conf"
        in log["message"]
        for log in task["logs"]
    )
    with session_scope(app.state.database.session_factory) as session:
        app_binding = session.get(ConfigBinding, app_binding_id)
        gone_binding = session.get(ConfigBinding, gone_binding_id)
        modified_binding = session.get(ConfigBinding, modified_binding_id)
        assert app_binding.current_version == 2
        assert app_binding.sync_status == "synced"
        assert app_binding.content == "new app content"
        assert gone_binding.sync_status == "orphaned"
        assert modified_binding.sync_status == "modified"
        assert session.get(ConfigBinding, deleted_binding_id) is None
        assert (
            session.scalar(
                select(BindingVersion).where(
                    BindingVersion.binding_id == app_binding_id,
                    BindingVersion.version == 2,
                )
            )
            is not None
        )


def test_partial_sync_only_updates_selected_discovered_paths(
    config_client,
    monkeypatch,
):
    """部分同步忽略未选远程路径且不会把其他已同步绑定标为 orphan。"""
    client, app, csrf_token, node_id, user_id = config_client
    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            selected_id = _create_binding(
                session,
                node_id,
                user_id,
                "selected.conf",
                "/etc/nginx/conf.d/selected.conf",
                "old selected",
            )
            other_id = _create_binding(
                session,
                node_id,
                user_id,
                "other.conf",
                "/etc/nginx/conf.d/other.conf",
                "old other",
            )

    def fake_discovery(
        target, main_path, context, progress_callback=None, max_depth=3, client=None
    ):
        """返回两个文件以核对部分同步路径过滤。"""
        return [
            {
                "path": "/etc/nginx/conf.d/selected.conf",
                "name": "selected.conf",
                "content": "new selected",
            },
            {
                "path": "/etc/nginx/conf.d/unselected.conf",
                "name": "unselected.conf",
                "content": "remote only",
            },
        ], []

    class FakeSshClient:
        """提供同步流程使用的空 SSH 客户端。"""

        def close(self):
            """关闭伪造 SSH 连接。"""
            return None

    monkeypatch.setattr(
        "ngxops.configs.tasks.discover_remote_configs",
        fake_discovery,
    )
    monkeypatch.setattr(
        "ngxops.configs.tasks._connect_ssh",
        lambda *args: (FakeSshClient(), ""),
    )
    response = client.post(
        "/api/configs/sync",
        headers={"X-CSRFToken": csrf_token},
        json={
            "node_id": node_id,
            "mode": "partial",
            "selected_paths": ["/etc/nginx/conf.d/selected.conf"],
        },
    )
    assert response.status_code == 202, response.text
    task = _wait_for_task(client, response.json()["task_id"])
    assert task["status"] == "success"
    with session_scope(app.state.database.session_factory) as session:
        selected = session.get(ConfigBinding, selected_id)
        other = session.get(ConfigBinding, other_id)
        assert selected.content == "new selected"
        assert selected.current_version == 2
        assert other.content == "old other"
        assert other.sync_status == "synced"
        assert (
            session.scalar(select(Config).where(Config.name == "unselected.conf"))
            is None
        )


def test_batch_sync_creates_persistent_task_with_default_parallel_limit(
    config_client,
    monkeypatch,
):
    """批量同步接受合格节点并通过统一任务 API 返回终态。"""
    client, app, csrf_token, node_id, _user_id = config_client

    def fake_discovery(
        target, main_path, context, progress_callback=None, max_depth=3, client=None
    ):
        """返回一个批量节点可成功读取的远程文件。"""
        return [{"path": main_path, "name": "nginx.conf", "content": "events {}"}], []

    class FakeSshClient:
        """提供批量同步使用的空 SSH 客户端。"""

        def close(self):
            """关闭伪造 SSH 连接。"""
            return None

    monkeypatch.setattr(
        "ngxops.configs.tasks.discover_remote_configs",
        fake_discovery,
    )
    monkeypatch.setattr(
        "ngxops.configs.tasks._connect_ssh",
        lambda *args: (FakeSshClient(), ""),
    )
    response = client.post(
        "/api/configs/sync/batch",
        headers={"X-CSRFToken": csrf_token},
        json={"node_ids": [node_id]},
    )
    assert response.status_code == 202, response.text
    task = _wait_for_task(client, response.json()["task_id"])
    assert task["operation_type"] == "config_batch_sync"
    assert task["status"] == "success"
    assert task["result_tree"]["summary"] == {
        "total": 1,
        "success": 1,
        "failed": 0,
    }
    assert any(
        "config-node (192.0.2.80) 新增配置 /etc/nginx/nginx.conf"
        in log["message"]
        for log in task["logs"]
    )
    detail = client.get("/tasks/{}/".format(task["id"]))
    assert detail.status_code == 200, detail.text
    assert "task-log-list-terminal" in detail.text
    assert "config-node (192.0.2.80) · 连接 SSH" in detail.text
    assert 'id="taskLogHostFilter"' not in detail.text

    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            task_row = session.get(Task, task["id"])
            task_row.target_hostnames = "config-node,config-node-2"
            task_row.target_ips = "192.0.2.80,192.0.2.81"
    detail = client.get("/tasks/{}/".format(task["id"]))
    assert detail.status_code == 200, detail.text
    assert 'id="taskLogHostFilter"' in detail.text
    assert '<option value="192.0.2.80">config-node</option>' in detail.text
    assert '<option value="192.0.2.81">config-node-2</option>' in detail.text


def test_batch_sync_persists_active_node_and_config_progress(config_client, monkeypatch):
    """批量任务在节点完成前持续显示当前节点步骤和配置路径。"""
    client, _app, csrf_token, node_id, _user_id = config_client
    started = Event()
    release = Event()

    def fake_sync_node(
        context,
        session_factory,
        encryption_key,
        current_node_id,
        user_id,
        task_id,
        mode,
        selected_paths,
        main_conf_path,
        ssh_client=None,
        progress_callback=None,
    ):
        """暂停节点同步以检查持久化的活动进度。"""
        progress_callback("同步配置 1/2：/etc/nginx/conf.d/site.conf")
        started.set()
        assert release.wait(3), "测试未释放同步工作线程"
        return {
            "node_id": current_node_id,
            "hostname": "config-node",
            "ip": "192.0.2.80",
            "mode": mode,
            "created": [{"name": "site.conf", "path": "/etc/nginx/conf.d/site.conf"}],
            "updated": [],
            "skipped": [],
            "orphaned": [],
            "deleted": [],
            "errors": [],
        }

    monkeypatch.setattr("ngxops.configs.tasks._sync_node", fake_sync_node)
    response = client.post(
        "/api/configs/sync/batch",
        headers={"X-CSRFToken": csrf_token},
        json={"node_ids": [node_id]},
    )
    assert response.status_code == 202, response.text
    try:
        assert started.wait(3), "批量同步线程未启动"
        task_response = client.get(
            "/api/tasks/{}".format(response.json()["task_id"])
        )
        assert task_response.status_code == 200, task_response.text
        task = task_response.json()
        assert task["status"] == "running"
        assert task["progress"] == 0
        assert "0/1 个节点" in task["detail"]
        assert "config-node" in task["detail"]
        assert "/etc/nginx/conf.d/site.conf" in task["detail"]
        task_detail = client.get("/tasks/{}/".format(task["id"]))
        assert task_detail.status_code == 200, task_detail.text
        assert "task-log-list-terminal" in task_detail.text
        assert "config-node (192.0.2.80) · 同步配置 1/2" in task_detail.text
    finally:
        release.set()

    task = _wait_for_task(client, response.json()["task_id"])
    assert task["status"] == "success"
