"""验证 Nginx 升级包持久化、重复校验与查询分页交互。"""

import gzip
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
import re
import tarfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ngxops.accounts.models import User
from ngxops.accounts.passwords import make_password
from ngxops.app import create_app
from ngxops.config import get_settings
from ngxops.credentials.crypto import encrypt_secret
from ngxops.credentials.models import Credential
from ngxops.database.migration_runner import upgrade_database
from ngxops.database.session import session_scope
from ngxops.nodes.models import Node
from ngxops.settings.models import SystemSetting
from ngxops.settings.service import initialize_defaults
from ngxops.tasks.models import Task
from ngxops.upgrade.models import NginxModulePackage, NginxSourcePackage, NginxUpgradeRun
from ngxops.upgrade.services import effective_removed_modules


def test_module_removals_apply_only_to_nodes_that_have_the_parameter():
    """验证批量节点只移除各自编译基线中已存在的模块参数。"""
    requested = ["--with-http_ssl_module", "--with-http_v2_module"]
    first_node = effective_removed_modules(
        ["--with-http_ssl_module", "--with-threads"], requested
    )
    second_node = effective_removed_modules(
        ["--with-http_v2_module", "--with-threads"], requested
    )

    assert first_node == ["--with-http_ssl_module"]
    assert second_node == ["--with-http_v2_module"]


@pytest.fixture
def upgrade_client(tmp_path):
    """创建隔离升级数据目录和已登录管理员。"""
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
                    username="upgrade-admin",
                    password=make_password("upgrade-admin-password-123"),
                    is_superuser=True,
                    is_active=True,
                )
                session.add(user)
                session.flush()
                user_id = user.id

        login_page = client.get("/login/")
        csrf_token = re.search(
            r'name="csrf_token" value="([^"]+)"', login_page.text
        ).group(1)
        login = client.post(
            "/login/",
            data={
                "csrf_token": csrf_token,
                "username": "upgrade-admin",
                "password": "upgrade-admin-password-123",
            },
        )
        assert login.status_code == 302
        yield client, app, csrf_token, user_id


def _archive_bytes():
    """生成可通过源码包和模块包结构校验的 gzip tar 归档。"""
    output = BytesIO()
    payload = b"nginx package test"
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            item = tarfile.TarInfo("nginx-test/README")
            item.size = len(payload)
            archive.addfile(item, BytesIO(payload))
    return output.getvalue()


def _upload(client, csrf_token, path, filename, data):
    """使用升级页的 XHR 请求方式上传一个归档。"""
    fields = {"csrf_token": csrf_token, **data}
    return client.post(
        path,
        headers={
            "X-CSRFToken": csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        },
        data=fields,
        files={"package_file": (filename, _archive_bytes(), "application/gzip")},
    )


def test_compute_config_accepts_third_party_module_models(upgrade_client):
    """验证在线和离线第三方模块均可通过预览接口转换为 configure 参数。"""
    client, _app, csrf_token, _user_id = upgrade_client
    response = client.post(
        "/api/upgrade/compute-config",
        headers={"X-CSRFToken": csrf_token},
        json={
            "current_params": ["--with-http_ssl_module"],
            "added_modules": [],
            "removed_modules": [],
            "added_third_party": [
                {"name": "ngx-http-echo", "source": "package", "package_id": 12},
                {
                    "name": "ngx-http-headers",
                    "source": "git",
                    "git_url": "https://example.com/ngx-http-headers.git",
                    "branch": "main",
                },
            ],
            "remote_work_dir": "/tmp/nginx-upgrade",
            "upgrade_mode": "upgrade",
            "target_prefix": "",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["target_params"] == [
        "--with-http_ssl_module",
        "--add-module=/tmp/nginx-upgrade/nginx-modules/ngx-http-echo",
        "--add-module=/tmp/nginx-upgrade/nginx-modules/ngx-http-headers",
    ]
    assert (
        "--add-module=/tmp/nginx-upgrade/nginx-modules/ngx-http-echo"
        in body["target_opts"]
    )
    response_schema = client.get("/openapi.json").json()["components"]["schemas"][
        "ComputeConfigResponse"
    ]
    assert {"target_opts", "target_params"}.issubset(response_schema["properties"])


def test_uploads_use_home_storage_and_md5_prevents_duplicate_rows(upgrade_client):
    """验证源码和模块包落在 NGXOPS_HOME 且相同 MD5 不新增记录。"""
    client, app, csrf_token, user_id = upgrade_client
    archive = _archive_bytes()
    expected_md5 = hashlib.md5(archive).hexdigest()

    source = _upload(
        client,
        csrf_token,
        "/upgrade/packages/upload/",
        "nginx-1.26.1.tar.gz",
        {"name": "Nginx stable", "version": "1.26.1", "description": "stable"},
    )
    assert source.status_code == 200, source.text
    assert source.json()["success"] is True
    assert source.json()["redirect"] == "/upgrade/packages/?uploaded=1"
    source_conflict = client.get("/api/upgrade/packages/check?version=1.26.1")
    assert source_conflict.status_code == 200
    assert source_conflict.json()["exists"] is True
    upload_notice = client.get(source.json()["redirect"])
    assert upload_notice.status_code == 200
    assert 'id="packageNotice"' in upload_notice.text

    source_duplicate = _upload(
        client,
        csrf_token,
        "/upgrade/packages/upload/",
        "nginx-1.26.2.tar.gz",
        {"name": "Nginx duplicate", "version": "1.26.2"},
    )
    assert source_duplicate.status_code == 409
    assert source_duplicate.json()["md5_duplicate"] is True

    module = _upload(
        client,
        csrf_token,
        "/upgrade/modules/upload/",
        "ngx-module-test.tar.gz",
        {"name": "ngx-module-test", "version": "1.0"},
    )
    assert module.status_code == 200, module.text
    module_conflict = client.get(
        "/api/upgrade/modules/check?name=ngx-module-test&version=1.0"
    )
    assert module_conflict.status_code == 200
    assert module_conflict.json()["exists"] is True
    module_duplicate = _upload(
        client,
        csrf_token,
        "/upgrade/modules/upload/",
        "ngx-module-copy.tar.gz",
        {"name": "ngx-module-copy", "version": "2.0"},
    )
    assert module_duplicate.status_code == 409
    assert module_duplicate.json()["md5_duplicate"] is True

    package_dir = app.state.settings.upgrade_package_dir
    assert package_dir == app.state.settings.data_dir / "nginx_packages"
    assert package_dir.is_dir()
    with session_scope(app.state.database.session_factory) as session:
        source_rows = session.scalars(select(NginxSourcePackage)).all()
        module_rows = session.scalars(select(NginxModulePackage)).all()
        assert len(source_rows) == 1
        assert len(module_rows) == 1
        assert source_rows[0].file_md5 == expected_md5
        assert module_rows[0].file_md5 == expected_md5
        assert source_rows[0].created_by == user_id
        assert source_rows[0].is_official is False
        assert (package_dir / source_rows[0].file_name).read_bytes() == archive
        assert (package_dir / module_rows[0].file_name).read_bytes() == archive


def test_uploaded_source_package_can_start_upgrade_batch(upgrade_client, monkeypatch):
    """验证刚上传的源码包可通过完整提交校验并入队而不连接 SSH。"""
    client, app, csrf_token, user_id = upgrade_client
    uploaded = _upload(
        client,
        csrf_token,
        "/upgrade/packages/upload/",
        "nginx-1.28.1.tar.gz",
        {"name": "Nginx stable", "version": "1.28.1"},
    )
    assert uploaded.status_code == 200, uploaded.text

    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            package = session.scalar(
                select(NginxSourcePackage).where(
                    NginxSourcePackage.version == "1.28.1"
                )
            )
            package_id = package.id
            package_path = app.state.settings.upgrade_package_dir / package.file_name
            assert package_path.is_file()
            credential = Credential(
                name="upgrade-start-credential",
                username="deploy",
                password=encrypt_secret(
                    app.state.credential_encryption_key, "not-used-in-test"
                ),
                created_by=user_id,
            )
            session.add(credential)
            session.flush()
            node = Node(
                hostname="upgrade-start-node",
                ip="192.0.2.250",
                port=22,
                credential_id=credential.id,
                environment="test",
                nginx_version="1.26.1",
                nginx_available=True,
                status="online",
                created_by=user_id,
            )
            session.add(node)
            session.flush()
            node_id = node.id

    submitted = []
    monkeypatch.setattr(
        app.state.task_executor,
        "submit",
        lambda task_id, function: submitted.append((task_id, function)),
    )

    response = client.post(
        "/api/upgrade/tasks",
        headers={"X-CSRFToken": csrf_token},
        json={
            "node_ids": [node_id],
            "source_package": package_id,
            "upgrade_mode": "upgrade",
            "remote_work_dir": "/tmp/nginx-upgrade",
            "make_jobs": 4,
            "target_version": "1.28.1",
            "added_modules": [],
            "removed_modules": [],
            "added_third_party": [],
            "nodes_payload": [
                {
                    "node_id": node_id,
                    "params": ["--prefix=/usr/local/nginx"],
                    "current_configure_opts": "--prefix=/usr/local/nginx",
                    "prefix": "/usr/local/nginx",
                    "binary_path": "/usr/local/nginx/sbin/nginx",
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["task_ids"]) == 1
    assert [task_id for task_id, _function in submitted] == response.json()["task_ids"]


def test_upgrade_center_summary_uses_nonbold_hostname_and_confirm_label(upgrade_client):
    """验证升级向导使用轻字重主机名和调整后编译参数标题。"""
    client, _app, _csrf_token, _user_id = upgrade_client
    response = client.get("/upgrade/center/")
    script = (Path.cwd() / "static/js/upgrade-center.js").read_text(encoding="utf-8")
    template = (Path.cwd() / "templates/upgrade/center.html").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert '<span class="upgrade-node-hostname">{{ node.hostname }}</span>' in template
    assert "<strong>{{ node.hostname }}</strong>" not in template
    assert "调整后编译参数" in response.text
    assert "统一目标 configure 参数" not in response.text
    assert 'summaryGrid.className = "upgrade-summary-grid"' in script
    assert 'row.querySelector(".upgrade-node-hostname").textContent' in script


def test_module_search_filters_on_enter_and_query_tag_removal():
    """验证模块搜索只按 Enter 筛选，并在删除查询标签后重新筛选。"""
    script = (Path.cwd() / "static/js/upgrade-center.js").read_text(encoding="utf-8")
    start = script.index('["addModuleSearch", "removeModuleSearch"]')
    end = script.index('document.getElementById("addThirdParty")', start)
    handlers = script[start:end]

    assert '$(input).on("querytags:change", filterModuleOptions);' in handlers
    assert 'if (event.key !== "Enter" || event.isComposing) return;' in handlers
    assert 'input.addEventListener("input", filterModuleOptions)' not in handlers


def test_upgrade_summary_shows_install_path_without_prefix_flag():
    """验证升级摘要仅显示安装路径值，不显示 configure 参数名。"""
    script = (Path.cwd() / "static/js/upgrade-center.js").read_text(encoding="utf-8")

    assert 'appendSummaryItem(summaryGrid, "安装路径", prefix.value' in script
    assert "prefix.textContent = group.prefix;" in script
    assert 'groups.length + " 种路径"' in script
    assert '"--prefix=" + groups[0].prefix' not in script


def test_upload_dropzones_use_configured_limit_for_both_package_types(upgrade_client):
    """验证两个上传页显示系统设置上限且服务端拒绝超限归档。"""
    client, app, csrf_token, _user_id = upgrade_client
    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            initialize_defaults(session)
            setting = session.scalar(
                select(SystemSetting).where(
                    SystemSetting.key == "upgrade.package_max_size_mb"
                )
            )
            setting.value = "1"

    for page_path in ("/upgrade/packages/upload/", "/upgrade/modules/upload/"):
        page = client.get(page_path)
        assert page.status_code == 200
        assert 'id="packageDropzone"' in page.text
        assert 'role="button"' in page.text
        assert "点击或拖拽文件到此处上传" in page.text
        assert 'data-package-limit-mb="1"' in page.text
        assert "最大 1 MB" in page.text
        assert "标记为官方包" not in page.text
        assert page.text.count('id="fileSummary"') == 1

    source_upload_page = client.get("/upgrade/packages/upload/")
    module_upload_page = client.get("/upgrade/modules/upload/")
    assert 'id="packageVersion" name="version" maxlength="50" required' in source_upload_page.text
    assert 'id="packageVersion" name="version" maxlength="50" required' not in module_upload_page.text
    package_js = (Path.cwd() / "static/js/upgrade-packages.js").read_text(encoding="utf-8")
    assert package_js.index("await validateArchiveSignature(file)") < package_js.index("await checkVersionConflict()")
    assert package_js.index("await checkVersionConflict()") < package_js.index("beginTransfer(false)")
    assert "请输入版本号或选择具有 nginx-版本号 命名的文件" in package_js

    too_large = b"x" * (1024 * 1024 + 1)
    for upload_path, filename, name in (
        ("/upgrade/packages/upload/", "nginx-1.2.3.tar.gz", "Nginx"),
        ("/upgrade/modules/upload/", "ngx-module.tar.gz", "ngx-module"),
    ):
        response = client.post(
            upload_path,
            headers={
                "X-CSRFToken": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
            data={"csrf_token": csrf_token, "name": name, "version": "1.2.3"},
            files={"package_file": (filename, too_large, "application/gzip")},
        )
        assert response.status_code == 413
        assert "不能超过 1MB" in response.json()["message"]


def test_package_and_history_filters_use_shared_query_and_page_sizes(upgrade_client):
    """验证升级包和历史列表按 AND 关键词及公共页数筛选。"""
    client, app, _csrf_token, user_id = upgrade_client
    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            packages = [
                NginxSourcePackage(
                    name="nginx-stable-prod" if index == 0 else "archive-{}".format(index),
                    version="1.28.0" if index == 0 else "1.{}".format(index),
                    file_name="test-{}.tar.gz".format(index),
                    file_size=10,
                    file_md5="{:032x}".format(index + 1),
                    description="production web" if index == 0 else "archive",
                    created_by=user_id,
                    created_at=datetime(2026, 10, 12, 3, 4, 0),
                )
                for index in range(11)
            ]
            session.add_all(packages)
            task = Task(
                operation_type="nginx_upgrade",
                status="success",
                progress=100,
                source_batch="UG-261012-0001",
                target_hostnames="edge-prod",
                target_ips="192.0.2.40",
                trigger_user_id=user_id,
            )
            session.add(task)
            session.flush()
            task_id = task.id
            session.add(
                NginxUpgradeRun(
                    task_id=task.id,
                    batch_number="UG-261012-0001",
                    node_hostname="edge-prod",
                    node_ip="192.0.2.40",
                    source_package_name="nginx-stable-prod",
                    target_version="1.28.0",
                    upgrade_mode="upgrade",
                    remote_work_dir="/tmp/nginx-upgrade",
                    make_jobs=2,
                    current_version="1.26.1",
                    backup_binary_path="/usr/sbin/nginx.bak",
                )
            )

    matches = client.get("/upgrade/packages/?search=nginx-stable%2C1.28&per_page=10")
    assert matches.status_code == 200
    assert "nginx-stable-prod" in matches.text
    assert "archive-1" not in matches.text
    assert "2026-10-12 11:04" in matches.text
    description_search = client.get("/upgrade/packages/?search=production%2Cweb")
    assert "nginx-stable-prod" not in description_search.text
    md5_search = client.get("/upgrade/packages/?search=000000000000")
    assert "nginx-stable-prod" not in md5_search.text
    assert 'data-query-tags' in matches.text
    assert 'placeholder="搜索名称或版本"' in matches.text
    assert "描述或 MD5" not in matches.text
    assert 'value="100">100 条/页' in matches.text

    first_page = client.get("/upgrade/packages/?per_page=10")
    second_page = client.get("/upgrade/packages/?page=2&per_page=10")
    assert "共 11 条，第 1 / 2 页" in first_page.text
    assert "共 11 条，第 2 / 2 页" in second_page.text
    assert 'class="ms-auto d-flex flex-wrap justify-content-end gap-2"' in first_page.text
    assert 'href="/upgrade/center/"' in first_page.text
    assert 'href="/upgrade/modules/"' in first_page.text

    history = client.get(
        "/upgrade/history/?search=edge-prod%2CUG-261012&status=success&per_page=10"
    )
    assert history.status_code == 200
    assert "edge-prod" in history.text
    assert 'data-query-tags' in history.text
    assert 'id="upgradeStatus"' in history.text
    assert "筛选</button>" not in history.text

    center = client.get("/upgrade/center/")
    assert center.status_code == 200
    assert 'id="upgradeNodePickerModal"' in center.text
    assert 'id="nodePageSize"' in center.text
    assert 'id="nodePageFirst"' in center.text
    assert 'id="nodePageLast"' in center.text
    assert 'id="packagePageSize"' in center.text
    assert 'id="packagePageFirst"' in center.text
    assert 'id="packagePageLast"' in center.text
    assert 'id="selectedPackageSummary"' in center.text
    assert 'data-step-target="2" disabled aria-disabled="true"' in center.text
    assert 'data-step-target="3" disabled aria-disabled="true"' in center.text
    assert 'data-step-target="4" disabled aria-disabled="true"' in center.text
    assert 'id="moduleAdjustModal"' in center.text
    assert 'id="referenceNode"' in center.text
    assert 'id="paramsMismatchDetails"' in center.text
    assert 'id="finalConfigPreviewLabel"' in center.text
    assert "2026-10-12 11:04" in center.text
    assert 'data-query-tags' in center.text
    assert "回车添加条件" not in center.text
    assert "上传新包" not in center.text
    assert 'class="form-label upgrade-field-label mb-0"' in center.text
    assert 'value="10" selected' in center.text
    node_table_end = center.text.index(
        "</table>", center.text.index('id="upgradeNodeTable"')
    )
    package_table_end = center.text.index(
        "</table>", center.text.index('id="upgradePackageTable"')
    )
    assert node_table_end < center.text.index('id="nodeResultCount"')
    assert package_table_end < center.text.index('id="packageResultCount"')
    assert "选择目标" in center.text
    assert "编译环境" in center.text
    assert "编译参数" in center.text
    assert "确认执行" in center.text
    assert "升级执行进度" in center.text
    assert 'id="currentConfigOutput"' in center.text
    assert 'id="sharedConfigPreview"' in center.text
    assert 'id="perNodeConfigNote"' in center.text
    assert 'class="nginx-v-output nginx-v-output-alt upgrade-config-output upgrade-confirm-terminal"' in center.text

    upgrade_detail = client.get("/upgrade/tasks/{}/".format(task_id))
    task_center_detail = client.get("/tasks/{}/".format(task_id))
    assert upgrade_detail.status_code == 200
    assert 'id="rollbackUpgrade"' in upgrade_detail.text
    assert 'data-has-backup="1"' in upgrade_detail.text
    assert task_center_detail.status_code == 200
    assert 'data-task-active="false"' in task_center_detail.text

    with session_scope(app.state.database.session_factory) as session:
        assert session.scalar(select(func.count()).select_from(NginxSourcePackage)) == 11


def test_upgrade_home_shows_four_stats_and_recent_task_table(upgrade_client):
    """验证升级概览卡片、最近任务表和近七天失败统计。"""
    client, app, _csrf_token, user_id = upgrade_client
    now = datetime.utcnow()
    task_rows = (
        ("pending", now, 0, "UG-HOME-PENDING"),
        ("running", now - timedelta(minutes=1), 45, "UG-HOME-RUNNING"),
        ("failed", now - timedelta(days=1), 60, "UG-HOME-FAILED-RECENT"),
        ("failed", now - timedelta(days=8), 60, "UG-HOME-FAILED-OLD"),
        ("success", now - timedelta(days=2), 100, "UG-HOME-SUCCESS"),
    )
    with session_scope(app.state.database.session_factory) as session:
        with session.begin():
            source = NginxSourcePackage(
                name="Nginx stable",
                version="1.28.0",
                file_name="nginx-overview.tar.gz",
                file_size=100,
                file_md5="1" * 32,
                created_by=user_id,
            )
            module = NginxModulePackage(
                name="ngx-test",
                version="1.0",
                file_name="ngx-test-overview.tar.gz",
                file_size=100,
                file_md5="2" * 32,
                created_by=user_id,
            )
            session.add_all((source, module))
            for status, created_at, progress, batch_number in task_rows:
                task = Task(
                    operation_type="nginx_upgrade",
                    status=status,
                    progress=progress,
                    source_batch=batch_number,
                    target_hostnames="edge-overview",
                    target_ips="192.0.2.41",
                    trigger_user_id=user_id,
                    created_at=created_at,
                )
                session.add(task)
                session.flush()
                session.add(
                    NginxUpgradeRun(
                        task_id=task.id,
                        batch_number=batch_number,
                        node_hostname="edge-overview",
                        node_ip="192.0.2.41",
                        source_package_name="Nginx stable",
                        target_version="1.28.0",
                        upgrade_mode="upgrade",
                        remote_work_dir="/tmp/nginx-upgrade",
                        make_jobs=2,
                        current_version="1.26.1",
                    )
                )

    response = client.get("/upgrade/")
    assert response.status_code == 200
    assert "location" not in response.headers
    assert response.text.count('class="dashboard-stat-card') == 4
    assert 'href="/upgrade/center/"' in response.text
    assert 'href="/upgrade/packages/"' in response.text
    assert 'href="/upgrade/modules/"' in response.text
    assert 'href="/upgrade/history/"' in response.text
    assert "最近升级任务" in response.text
    assert "UG-HOME-PENDING" in response.text
    stats = dict(
        re.findall(
            r'data-upgrade-stat="([^"]+)">([^<]+)</span>', response.text
        )
    )
    assert stats == {"source": "1", "module": "1", "running": "2", "failed-7d": "1"}
