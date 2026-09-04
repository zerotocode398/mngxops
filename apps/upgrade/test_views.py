"""upgrade 视图层测试（Nginx 升级核心模块）"""

import json
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.upgrade.models import (
    NginxSourcePackage,
    NginxThirdPartyModulePackage,
    NginxUpgradeTask,
)


@pytest.mark.django_db
class TestPackageListView:
    """源码包列表"""

    def test_list_accessible(self, admin_client):
        resp = admin_client.get(reverse("upgrade:package_list"))
        assert resp.status_code == 200

    def test_list_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("upgrade:package_list"))
        assert resp.status_code == 302

    def test_list_contains_packages(self, admin_client):
        resp = admin_client.get(reverse("upgrade:package_list"))
        assert resp.status_code == 200
        assert "packages" in resp.context


@pytest.mark.django_db
class TestPackageUploadView:
    """源码包上传"""

    def test_upload_page_accessible(self, admin_client):
        resp = admin_client.get(reverse("upgrade:package_upload"))
        assert resp.status_code == 200

    def test_upload_page_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("upgrade:package_upload"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestModulePackageListView:
    """第三方模块包列表"""

    def test_list_accessible(self, admin_client):
        resp = admin_client.get(reverse("upgrade:module_package_list"))
        assert resp.status_code == 200

    def test_list_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("upgrade:module_package_list"))
        assert resp.status_code == 302

    def test_list_contains_modules(self, admin_client):
        resp = admin_client.get(reverse("upgrade:module_package_list"))
        assert resp.status_code == 200
        assert "packages" in resp.context


@pytest.mark.django_db
class TestModulePackageUploadView:
    """第三方模块包上传"""

    def test_upload_page_accessible(self, admin_client):
        resp = admin_client.get(reverse("upgrade:module_package_upload"))
        assert resp.status_code == 200

    def test_upload_page_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("upgrade:module_package_upload"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestUpgradeCenterView:
    """升级中心"""

    def test_center_accessible(self, admin_client):
        resp = admin_client.get(reverse("upgrade:center"))
        assert resp.status_code == 200

    def test_center_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("upgrade:center"))
        assert resp.status_code == 302

    def test_center_contains_packages(self, admin_client):
        resp = admin_client.get(reverse("upgrade:center"))
        assert resp.status_code == 200
        assert "packages" in resp.context
        assert "module_packages" in resp.context
        assert "default_work_dir" in resp.context


@pytest.mark.django_db
class TestUpgradeHistoryView:
    """升级历史"""

    def test_history_accessible(self, admin_client):
        resp = admin_client.get(reverse("upgrade:history"))
        assert resp.status_code == 200

    def test_history_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("upgrade:history"))
        assert resp.status_code == 302


@pytest.mark.django_db
class TestUpgradeTaskListView:
    """升级任务主页"""

    def test_list_accessible(self, admin_client):
        resp = admin_client.get(reverse("upgrade:list"))
        assert resp.status_code == 200

    def test_list_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(reverse("upgrade:list"))
        assert resp.status_code == 302

    def test_list_contains_stats(self, admin_client):
        resp = admin_client.get(reverse("upgrade:list"))
        assert resp.status_code == 200
        assert "package_count" in resp.context
        assert "running_count" in resp.context
        assert "failed_7d_count" in resp.context
        assert "recent_tasks" in resp.context


# ==================== 源码包版本检查 ====================


@pytest.mark.django_db
class TestPackageVersionCheckView:
    """源码包版本检查 API"""

    def test_check_nonexistent_version(self, admin_client):
        resp = admin_client.get(
            reverse("upgrade:package_check_version"), {"version": "9.9.9"}
        )
        payload = resp.json()
        assert payload["exists"] is False
        assert payload["version"] == "9.9.9"

    def test_check_empty_version(self, admin_client):
        resp = admin_client.get(
            reverse("upgrade:package_check_version"), {"version": ""}
        )
        payload = resp.json()
        assert payload["exists"] is False

    def test_check_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(
            reverse("upgrade:package_check_version"), {"version": "1.0.0"}
        )
        assert resp.status_code == 302


# ==================== 源码包删除 ====================


@pytest.mark.django_db
class TestPackageDeleteView:
    """源码包删除"""

    def test_delete_success(self, admin_client, admin_user):
        pkg = NginxSourcePackage.objects.create(
            name="nginx-official",
            version="1.26.1",
            uploaded_by=admin_user,
            package_file=SimpleUploadedFile(
                "nginx-1.26.1.tar.gz", b"fake content", content_type="application/gzip"
            ),
        )
        with patch("django.db.models.fields.files.FieldFile.delete", return_value=None):
            resp = admin_client.post(reverse("upgrade:package_delete", args=[pkg.id]))
        assert resp.status_code == 302
        assert not NginxSourcePackage.objects.filter(id=pkg.id).exists()

    def test_delete_not_found(self, admin_client):
        resp = admin_client.post(reverse("upgrade:package_delete", args=[99999]))
        assert resp.status_code == 404

    def test_delete_redirects_anonymous(self, anonymous_client, admin_user):
        pkg = NginxSourcePackage.objects.create(
            name="nginx-official",
            version="1.26.1",
            uploaded_by=admin_user,
            package_file=SimpleUploadedFile(
                "nginx-1.26.1.tar.gz", b"fake content", content_type="application/gzip"
            ),
        )
        resp = anonymous_client.post(reverse("upgrade:package_delete", args=[pkg.id]))
        assert resp.status_code == 302


# ==================== 源码包下载 ====================


@pytest.mark.django_db
class TestPackageDownloadView:
    """源码包下载"""

    def test_download_accessible(self, admin_client, admin_user):
        pkg = NginxSourcePackage.objects.create(
            name="nginx-official",
            version="1.26.1",
            uploaded_by=admin_user,
            package_file=SimpleUploadedFile(
                "nginx-1.26.1.tar.gz", b"fake content", content_type="application/gzip"
            ),
        )
        resp = admin_client.get(reverse("upgrade:package_download", args=[pkg.id]))
        assert resp.status_code == 200

    def test_download_not_found(self, admin_client):
        resp = admin_client.get(reverse("upgrade:package_download", args=[99999]))
        assert resp.status_code == 404

    def test_download_redirects_anonymous(self, anonymous_client, admin_user):
        pkg = NginxSourcePackage.objects.create(
            name="nginx-official",
            version="1.26.1",
            uploaded_by=admin_user,
            package_file=SimpleUploadedFile(
                "nginx-1.26.1.tar.gz", b"fake content", content_type="application/gzip"
            ),
        )
        resp = anonymous_client.get(reverse("upgrade:package_download", args=[pkg.id]))
        assert resp.status_code == 302


# ==================== 第三方模块包检查 ====================


@pytest.mark.django_db
class TestModulePackageCheckView:
    """第三方模块包检查 API"""

    def test_check_nonexistent(self, admin_client):
        resp = admin_client.get(
            reverse("upgrade:module_package_check"),
            {"name": "unknown-module", "version": "v1.0"},
        )
        payload = resp.json()
        assert payload["exists"] is False

    def test_check_empty_name(self, admin_client):
        resp = admin_client.get(
            reverse("upgrade:module_package_check"), {"name": "", "version": ""}
        )
        payload = resp.json()
        assert payload["exists"] is False

    def test_check_redirects_anonymous(self, anonymous_client):
        resp = anonymous_client.get(
            reverse("upgrade:module_package_check"),
            {"name": "test-module", "version": "v1.0"},
        )
        assert resp.status_code == 302


# ==================== 第三方模块包删除 ====================


@pytest.mark.django_db
class TestModulePackageDeleteView:
    """第三方模块包删除"""

    def test_delete_success(self, admin_client, admin_user):
        pkg = NginxThirdPartyModulePackage.objects.create(
            name="nginx-module-sts",
            version="v1.2.1",
            uploaded_by=admin_user,
            package_file=SimpleUploadedFile(
                "nginx-module-sts.tar.gz",
                b"fake module",
                content_type="application/gzip",
            ),
        )
        with patch("django.db.models.fields.files.FieldFile.delete", return_value=None):
            resp = admin_client.post(
                reverse("upgrade:module_package_delete", args=[pkg.id])
            )
        assert resp.status_code == 302
        assert not NginxThirdPartyModulePackage.objects.filter(id=pkg.id).exists()

    def test_delete_not_found(self, admin_client):
        resp = admin_client.post(reverse("upgrade:module_package_delete", args=[99999]))
        assert resp.status_code == 404

    def test_delete_redirects_anonymous(self, anonymous_client, admin_user):
        pkg = NginxThirdPartyModulePackage.objects.create(
            name="nginx-module-sts",
            version="v1.2.1",
            uploaded_by=admin_user,
            package_file=SimpleUploadedFile(
                "nginx-module-sts.tar.gz",
                b"fake module",
                content_type="application/gzip",
            ),
        )
        resp = anonymous_client.post(
            reverse("upgrade:module_package_delete", args=[pkg.id])
        )
        assert resp.status_code == 302


# ==================== 第三方模块包下载 ====================


@pytest.mark.django_db
class TestModulePackageDownloadView:
    """第三方模块包下载"""

    def test_download_accessible(self, admin_client, admin_user):
        pkg = NginxThirdPartyModulePackage.objects.create(
            name="nginx-module-sts",
            version="v1.2.1",
            uploaded_by=admin_user,
            package_file=SimpleUploadedFile(
                "nginx-module-sts.tar.gz",
                b"fake module",
                content_type="application/gzip",
            ),
        )
        resp = admin_client.get(
            reverse("upgrade:module_package_download", args=[pkg.id])
        )
        assert resp.status_code == 200

    def test_download_not_found(self, admin_client):
        resp = admin_client.get(
            reverse("upgrade:module_package_download", args=[99999])
        )
        assert resp.status_code == 404

    def test_download_redirects_anonymous(self, anonymous_client, admin_user):
        pkg = NginxThirdPartyModulePackage.objects.create(
            name="nginx-module-sts",
            version="v1.2.1",
            uploaded_by=admin_user,
            package_file=SimpleUploadedFile(
                "nginx-module-sts.tar.gz",
                b"fake module",
                content_type="application/gzip",
            ),
        )
        resp = anonymous_client.get(
            reverse("upgrade:module_package_download", args=[pkg.id])
        )
        assert resp.status_code == 302


# ==================== 升级任务日志页 ====================


@pytest.mark.django_db
class TestUpgradeTaskLogView:
    """升级任务日志详情页"""

    def test_task_log_accessible(self, admin_client, admin_user, online_node):
        task = NginxUpgradeTask.objects.create(
            node=online_node,
            target_version="1.26.1",
            operator=admin_user,
        )
        resp = admin_client.get(reverse("upgrade:task_log", args=[task.id]))
        assert resp.status_code == 200
        assert resp.context["task"] == task

    def test_task_log_redirects_anonymous(
        self, anonymous_client, admin_user, online_node
    ):
        task = NginxUpgradeTask.objects.create(
            node=online_node,
            target_version="1.26.1",
            operator=admin_user,
        )
        resp = anonymous_client.get(reverse("upgrade:task_log", args=[task.id]))
        assert resp.status_code == 302

    def test_task_log_not_found(self, admin_client):
        resp = admin_client.get(reverse("upgrade:task_log", args=[99999]))
        assert resp.status_code == 404

    def test_task_log_contains_params(self, admin_client, admin_user, online_node):
        task = NginxUpgradeTask.objects.create(
            node=online_node,
            target_version="1.26.1",
            current_configure_opts="--prefix=/usr/local/nginx --with-http_ssl_module",
            target_configure_opts="--prefix=/usr/local/nginx --with-http_ssl_module --with-http_v3_module",
            operator=admin_user,
        )
        resp = admin_client.get(reverse("upgrade:task_log", args=[task.id]))
        assert resp.status_code == 200
        assert "current_params" in resp.context
        assert "target_params" in resp.context
        assert "param_removed" in resp.context
        assert "param_added" in resp.context
        assert resp.context["has_param_diff"] is True


# ==================== 升级任务进度 API ====================


@pytest.mark.django_db
class TestUpgradeTaskProgressView:
    """升级任务进度轮询 API"""

    def test_progress_accessible(self, admin_client, admin_user, online_node):
        task = NginxUpgradeTask.objects.create(
            node=online_node,
            target_version="1.26.1",
            status="compiling",
            progress=50,
            current_step="正在编译...",
            log_output="make[1]: Entering directory",
            operator=admin_user,
        )
        resp = admin_client.get(reverse("upgrade:task_progress", args=[task.id]))
        payload = resp.json()
        assert payload["success"] is True
        assert payload["status"] == "compiling"
        assert payload["progress"] == 50
        assert payload["current_step"] == "正在编译..."
        assert "log_url" in payload

    def test_progress_not_found(self, admin_client):
        resp = admin_client.get(reverse("upgrade:task_progress", args=[99999]))
        assert resp.status_code == 404


# ==================== 升级任务取消 ====================


@pytest.mark.django_db
class TestUpgradeTaskCancelView:
    """升级任务取消"""

    def test_cancel_pending_task(self, admin_client, admin_user, online_node):
        task = NginxUpgradeTask.objects.create(
            node=online_node,
            target_version="1.26.1",
            status="pending",
            operator=admin_user,
        )
        resp = admin_client.post(reverse("upgrade:task_cancel", args=[task.id]))
        payload = resp.json()
        assert payload["success"] is True
        task.refresh_from_db()
        assert task.status == "cancelled"

    def test_cancel_running_task(self, admin_client, admin_user, online_node):
        task = NginxUpgradeTask.objects.create(
            node=online_node,
            target_version="1.26.1",
            status="uploading_package",
            operator=admin_user,
        )
        resp = admin_client.post(reverse("upgrade:task_cancel", args=[task.id]))
        payload = resp.json()
        assert payload["success"] is True
        task.refresh_from_db()
        assert task.status == "cancelled"

    def test_cancel_terminal_task_rejected(self, admin_client, admin_user, online_node):
        task = NginxUpgradeTask.objects.create(
            node=online_node,
            target_version="1.26.1",
            status="success",
            operator=admin_user,
        )
        resp = admin_client.post(reverse("upgrade:task_cancel", args=[task.id]))
        payload = resp.json()
        assert payload["success"] is False

    def test_cancel_not_found(self, admin_client):
        resp = admin_client.post(reverse("upgrade:task_cancel", args=[99999]))
        assert resp.status_code == 404


# ==================== 升级任务创建 ====================


@pytest.mark.django_db
class TestUpgradeTaskCreateView:
    """升级任务创建 API"""

    def test_create_requires_permission(self, anonymous_client):
        resp = anonymous_client.post(
            reverse("upgrade:task_create"),
            data="{}",
            content_type="application/json",
        )
        assert resp.status_code == 302

    def test_create_invalid_json(self, admin_client):
        resp = admin_client.post(
            reverse("upgrade:task_create"),
            data="not json",
            content_type="application/json",
        )
        payload = resp.json()
        assert payload["success"] is False

    def test_create_single_form_validation_error(self, admin_client):
        resp = admin_client.post(
            reverse("upgrade:task_create"),
            {},
        )
        payload = resp.json()
        assert payload["success"] is False


# ==================== 批量进度 API ====================


@pytest.mark.django_db
class TestUpgradeBatchProgressView:
    """批量升级进度 API"""

    def test_api_missing_ids(self, admin_client):
        resp = admin_client.get(reverse("upgrade:api_batch_progress"))
        payload = resp.json()
        assert payload["success"] is False

    def test_api_invalid_ids(self, admin_client):
        resp = admin_client.get(reverse("upgrade:api_batch_progress"), {"ids": "abc"})
        payload = resp.json()
        assert payload["success"] is False

    def test_api_empty_ids(self, admin_client):
        resp = admin_client.get(reverse("upgrade:api_batch_progress"), {"ids": ""})
        payload = resp.json()
        assert payload["success"] is False

    def test_api_nonexistent_ids(self, admin_client):
        resp = admin_client.get(reverse("upgrade:api_batch_progress"), {"ids": "99999"})
        payload = resp.json()
        assert payload["success"] is True
        assert payload["tasks"] == []

    def test_api_returns_progress(self, admin_client, admin_user, online_node):
        task1 = NginxUpgradeTask.objects.create(
            node=online_node,
            target_version="1.26.1",
            status="compiling",
            progress=60,
            operator=admin_user,
        )
        task2 = NginxUpgradeTask.objects.create(
            node=online_node,
            target_version="1.26.1",
            status="success",
            progress=100,
            operator=admin_user,
        )
        resp = admin_client.get(
            reverse("upgrade:api_batch_progress"),
            {"ids": f"{task1.id},{task2.id}"},
        )
        payload = resp.json()
        assert payload["success"] is True
        assert len(payload["tasks"]) == 2
        assert payload["progress"] == 80
        assert payload["all_done"] is False
        assert payload["any_failed"] is False


# ==================== Nginx -V API ====================


@pytest.mark.django_db
class TestNginxVApiView:
    """获取 nginx -V 输出 API"""

    def test_api_requires_permission(self, anonymous_client, online_node):
        resp = anonymous_client.post(
            reverse("upgrade:api_nginx_v", args=[online_node.id])
        )
        assert resp.status_code == 302

    def test_api_node_not_found(self, admin_client):
        resp = admin_client.post(reverse("upgrade:api_nginx_v", args=[99999]))
        assert resp.status_code == 404

    def test_api_locked_node_rejected(self, admin_client, admin_user, credential):
        from apps.nodes.models import Node

        locked = Node.objects.create(
            hostname="locked-node",
            ip="10.0.0.250",
            status="online",
            is_locked=True,
            credential=credential,
            created_by=admin_user,
        )
        resp = admin_client.post(reverse("upgrade:api_nginx_v", args=[locked.id]))
        payload = resp.json()
        assert payload["success"] is False
        assert "锁定" in payload["message"]


# ==================== 解析编译参数 API ====================


@pytest.mark.django_db
class TestParseConfigApiView:
    """解析 nginx -V 输出 API"""

    def test_parse_valid_output(self, admin_client):
        raw = (
            "nginx version: nginx/1.24.0\n"
            "configure arguments: --prefix=/usr/local/nginx "
            "--with-http_ssl_module --with-http_v2_module"
        )
        resp = admin_client.post(
            reverse("upgrade:api_parse_config"), {"raw_output": raw}
        )
        payload = resp.json()
        assert payload["success"] is True
        assert payload["data"]["version"] == "1.24.0"
        assert payload["data"]["prefix"] == "/usr/local/nginx"
        assert len(payload["data"]["params"]) >= 2

    def test_parse_empty_output(self, admin_client):
        resp = admin_client.post(
            reverse("upgrade:api_parse_config"), {"raw_output": ""}
        )
        payload = resp.json()
        assert payload["success"] is False

    def test_parse_missing_raw_output(self, admin_client):
        resp = admin_client.post(reverse("upgrade:api_parse_config"), {})
        payload = resp.json()
        assert payload["success"] is False

    def test_parse_output_with_third_party(self, admin_client):
        raw = (
            "nginx version: nginx/1.26.1\n"
            "configure arguments: --prefix=/usr/local/nginx "
            "--add-module=/path/to/headers-more"
        )
        resp = admin_client.post(
            reverse("upgrade:api_parse_config"), {"raw_output": raw}
        )
        payload = resp.json()
        assert payload["success"] is True
        assert len(payload["data"]["third_party_modules"]) >= 1


# ==================== 计算编译参数 API ====================


@pytest.mark.django_db
class TestComputeConfigApiView:
    """计算调整后的编译参数 API"""

    def test_compute_basic(self, admin_client):
        resp = admin_client.post(
            reverse("upgrade:api_compute_config"),
            {
                "current_params": json.dumps(
                    [
                        "--prefix=/usr/local/nginx",
                        "--with-http_ssl_module",
                    ]
                ),
                "added_modules": json.dumps(["--with-http_v3_module"]),
                "removed_modules": json.dumps([]),
                "added_third_party": json.dumps([]),
            },
        )
        payload = resp.json()
        assert payload["success"] is True
        assert "--with-http_v3_module" in payload["target_opts"]

    def test_compute_remove_module(self, admin_client):
        resp = admin_client.post(
            reverse("upgrade:api_compute_config"),
            {
                "current_params": json.dumps(
                    [
                        "--prefix=/usr/local/nginx",
                        "--with-http_ssl_module",
                        "--with-mail",
                    ]
                ),
                "added_modules": json.dumps([]),
                "removed_modules": json.dumps(["--with-mail"]),
                "added_third_party": json.dumps([]),
            },
        )
        payload = resp.json()
        assert payload["success"] is True
        assert "--with-mail" not in payload["target_opts"]

    def test_compute_invalid_json(self, admin_client):
        resp = admin_client.post(
            reverse("upgrade:api_compute_config"),
            {
                "current_params": "not json",
                "added_modules": "[]",
                "removed_modules": "[]",
                "added_third_party": "[]",
            },
        )
        payload = resp.json()
        assert payload["success"] is False

    def test_compute_with_third_party(self, admin_client):
        resp = admin_client.post(
            reverse("upgrade:api_compute_config"),
            {
                "current_params": json.dumps(
                    [
                        "--prefix=/usr/local/nginx",
                    ]
                ),
                "added_modules": json.dumps([]),
                "removed_modules": json.dumps([]),
                "added_third_party": json.dumps(
                    [
                        {
                            "name": "headers-more",
                            "source": "git",
                            "git_url": "https://...",
                            "branch": "master",
                        }
                    ]
                ),
                "remote_work_dir": "/tmp/nginx-upgrade",
            },
        )
        payload = resp.json()
        assert payload["success"] is True
        assert "headers-more" in payload["target_opts"]


# ==================== 升级任务回滚 ====================


@pytest.mark.django_db
class TestUpgradeTaskRollbackView:
    """升级任务回滚"""

    def test_rollback_requires_permission(self, anonymous_client):
        resp = anonymous_client.post(reverse("upgrade:task_rollback", args=[1]))
        assert resp.status_code == 302

    def test_rollback_not_found(self, admin_client):
        resp = admin_client.post(reverse("upgrade:task_rollback", args=[99999]))
        assert resp.status_code == 404

    def test_rollback_pending_task_rejected(
        self, admin_client, admin_user, online_node
    ):
        task = NginxUpgradeTask.objects.create(
            node=online_node,
            target_version="1.26.1",
            status="pending",
            operator=admin_user,
        )
        resp = admin_client.post(reverse("upgrade:task_rollback", args=[task.id]))
        payload = resp.json()
        assert payload["success"] is False

    def test_rollback_no_backup_file(self, admin_client, admin_user, online_node):
        task = NginxUpgradeTask.objects.create(
            node=online_node,
            target_version="1.26.1",
            status="success",
            operator=admin_user,
        )
        resp = admin_client.post(reverse("upgrade:task_rollback", args=[task.id]))
        payload = resp.json()
        assert payload["success"] is False
        assert "备份" in payload["message"]
