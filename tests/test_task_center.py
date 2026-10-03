"""验证统一任务中心页面、搜索、访问边界和协作取消。"""

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
from ngxops.database.migration_runner import upgrade_database
from ngxops.database.session import session_scope
from ngxops.tasks.models import Task, TaskLog


@pytest.fixture
def task_center_client(tmp_path):
    """创建隔离数据库、管理员与受限操作用户。"""
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
                    username="task-admin",
                    password=make_password("task-admin-password-123"),
                    is_superuser=True,
                    is_active=True,
                )
                operator = User(
                    username="task-operator",
                    password=make_password("task-operator-password-123"),
                    is_superuser=False,
                    is_active=True,
                )
                session.add_all([admin, operator])
                session.flush()
                admin_id = admin.id
                operator_id = operator.id
                release_task = _create_task(
                    session,
                    "release_publish",
                    admin_id,
                    batch="release-261002-0001",
                    hosts="worker-a, worker-b",
                    ips="192.0.2.10, 192.0.2.11",
                    detail="发布完成：成功 2，失败 0，共 2",
                    tree={
                        "summary": {"total": 2, "success": 2, "failed": 0},
                        "nodes": [{"hostname": "worker-a", "bindings": []}],
                    },
                    with_log=True,
                )
                publisher_task = _create_task(
                    session,
                    "release_publish",
                    operator_id,
                    batch="release-owned-0001",
                    hosts="publisher-node",
                    status="pending",
                )
                own_sync = _create_task(
                    session,
                    "config_batch_sync",
                    operator_id,
                    batch="sync-own",
                    hosts="worker-c",
                    status="pending",
                )
                admin_sync = _create_task(
                    session,
                    "config_batch_sync",
                    admin_id,
                    batch="sync-admin",
                    hosts="worker-d",
                    status="pending",
                )
        login_token = _login(client, "task-admin", "task-admin-password-123")
        yield (
            client,
            app,
            login_token,
            admin_id,
            operator_id,
            release_task,
            publisher_task,
            own_sync,
            admin_sync,
        )


def _create_task(
    session,
    operation_type,
    user_id,
    *,
    batch="",
    hosts="",
    ips="",
    detail="",
    status="success",
    tree=None,
    with_log=False,
):
    """保存一条测试任务和可选日志。"""
    task = Task(
        operation_type=operation_type,
        status=status,
        detail=detail,
        result_tree_json=json.dumps(tree) if tree is not None else None,
        progress=100 if status == "success" else 0,
        source_batch=batch,
        target_hostnames=hosts,
        target_ips=ips,
        trigger_user_id=user_id,
    )
    session.add(task)
    session.flush()
    if with_log:
        session.add(TaskLog(task_id=task.id, message="发布步骤完成"))
    return task.id


def _login(client, username, password):
    """读取登录页 CSRF 令牌并建立用户会话。"""
    response = client.get("/login/")
    token = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
    response = client.post(
        "/login/",
        data={"csrf_token": token, "username": username, "password": password},
    )
    assert response.status_code == 302
    return token


def test_task_center_search_paging_and_task_detail(task_center_client):
    """列表按逗号词 AND 搜索并渲染任务结果、批次和日志。"""
    client, _app, _token, _admin, _operator, release_id, _publisher, _own, _other = task_center_client
    response = client.get("/tasks/?search=worker-a%2Crelease-261002-0001")
    assert response.status_code == 200
    assert "发布配置" in response.text
    assert "release-261002-0001" in response.text
    assert "#{}".format(release_id) in response.text
    assert "发布步骤完成" not in response.text

    unmatched = client.get("/tasks/?search=worker-a%2Crelease-missing")
    assert "#{}".format(release_id) not in unmatched.text

    detail = client.get("/tasks/{}/".format(release_id))
    assert detail.status_code == 200
    assert "执行结果" in detail.text
    assert "worker-a" in detail.text
    assert "发布步骤完成" in detail.text
    assert "taskLoadMoreLogs" in detail.text


def test_task_api_search_and_cancel(task_center_client):
    """JSON 列表沿用 AND 搜索，取消请求写入协作终态。"""
    client, _app, token, _admin, _operator, release_id, _publisher, own_sync, _other = task_center_client
    response = client.get("/api/tasks?search=worker-a%2Crelease-261002-0001")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [release_id]

    cancelled = client.post(
        "/api/tasks/{}/cancel".format(own_sync),
        headers={"X-CSRFToken": token},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    terminal = client.post(
        "/api/tasks/{}/cancel".format(own_sync),
        headers={"X-CSRFToken": token},
    )
    assert terminal.status_code == 400


def test_limited_user_only_sees_owned_allowed_task_types(task_center_client):
    """受限用户只能读取自己有业务权限的任务和详情。"""
    client, app, _token, _admin, operator_id, _release, _publisher, own_sync, admin_sync = task_center_client
    app.state.permission_checker = lambda _session, user, resource, action: (
        user.id == operator_id and resource == "configs" and action == "sync"
    )
    client.cookies.clear()
    _login(client, "task-operator", "task-operator-password-123")

    response = client.get("/tasks/")
    assert response.status_code == 200
    assert "#{}".format(own_sync) in response.text
    assert "#{}".format(admin_sync) not in response.text

    own_detail = client.get("/tasks/{}/".format(own_sync))
    assert own_detail.status_code == 200
    hidden_detail = client.get("/tasks/{}/".format(admin_sync))
    assert hidden_detail.status_code == 404

    own_tasks = client.get("/api/tasks")
    assert own_tasks.status_code == 200
    assert [item["id"] for item in own_tasks.json()["items"]] == [own_sync]


def test_publisher_can_track_owned_release_without_release_history_read(task_center_client):
    """发布权限可追踪本人任务但不能打开全局发布历史。"""
    client, app, _token, _admin, operator_id, _release, publisher_task, _sync, _other = task_center_client
    app.state.permission_checker = lambda _session, user, resource, action: (
        user.id == operator_id and resource == "releases" and action == "publish"
    )
    client.cookies.clear()
    _login(client, "task-operator", "task-operator-password-123")

    center = client.get("/tasks/")
    assert center.status_code == 200
    assert "#{}".format(publisher_task) in center.text
    assert "/releases/center/" in center.text
    assert "/releases/?search=release-owned-0001" not in center.text

    detail = client.get("/tasks/{}/".format(publisher_task))
    assert detail.status_code == 200
    assert "/tasks/?search=release-owned-0001" in detail.text
    assert "/releases/?search=release-owned-0001" not in detail.text


def test_task_routes_are_not_published_as_json_openapi_routes(task_center_client):
    """OpenAPI 保留 JSON 任务 API，并公开关键词筛选参数。"""
    client = task_center_client[0]
    schema = client.get("/openapi.json").json()
    assert "/tasks/" not in schema["paths"]
    parameters = schema["paths"]["/api/tasks"]["get"]["parameters"]
    assert "search" in {parameter["name"] for parameter in parameters}
