"""audit 模块单元测试（审计日志工具函数）"""

import pytest
from unittest.mock import MagicMock, patch

from apps.audit.utils import (
    OPERATION_AUDIT_MAP,
    _resolve_client_ip,
    log_async_task,
    log_task_center_created,
)


class TestOperationAuditMap:
    """OPERATION_AUDIT_MAP 完整性校验"""

    def test_all_known_types_have_entry(self):
        known_types = [
            "release_publish",
            "release_rollback",
            "credential_enable_test",
            "node_ssh_test",
            "node_batch_test",
            "node_system_info",
            "node_nginx_version",
            "config_batch_sync",
            "config_discover",
            "config_drift_check",
            "config_glob_preview",
            "nginx_upgrade",
            "nginx_rollback",
            "nginx_service_control",
            "nginx_install",
            "nginx_uninstall",
            "other",
        ]
        for op_type in known_types:
            assert op_type in OPERATION_AUDIT_MAP, f"Missing: {op_type}"

    def test_each_entry_returns_module_and_action(self):
        for op_type, (module, action) in OPERATION_AUDIT_MAP.items():
            assert isinstance(module, str) and len(module) > 0
            assert isinstance(action, str) and len(action) > 0


class TestResolveClientIp:
    """_resolve_client_ip 函数"""

    def test_explicit_ip_passed_through(self):
        assert _resolve_client_ip("1.2.3.4") == "1.2.3.4"

    def test_no_request_returns_default(self):
        with patch("apps.audit.utils.get_current_request", return_value=None):
            assert _resolve_client_ip() == "0.0.0.0"

    def test_x_forwarded_for_used_first(self):
        mock_req = MagicMock()
        mock_req.META = {
            "HTTP_X_FORWARDED_FOR": "10.0.0.1, 10.0.0.2",
            "REMOTE_ADDR": "192.168.0.1",
        }
        with patch("apps.audit.utils.get_current_request", return_value=mock_req):
            assert _resolve_client_ip() == "10.0.0.1"

    def test_remote_addr_fallback(self):
        mock_req = MagicMock()
        mock_req.META = {"REMOTE_ADDR": "192.168.0.1"}
        with patch("apps.audit.utils.get_current_request", return_value=mock_req):
            assert _resolve_client_ip() == "192.168.0.1"


@pytest.mark.django_db
class TestLogAsyncTask:
    """log_async_task 函数"""

    def test_creates_audit_log(self, admin_user):
        log = log_async_task(
            user=admin_user,
            module="配置管理",
            action="创建配置",
            detail="test",
            task_center_id=1,
            source_batch="BATCH-001",
        )
        assert log is not None
        assert log.user == admin_user
        assert log.module == "配置管理"
        assert log.action == "创建配置"
        assert log.detail == "test"
        assert log.task_center_id == 1
        assert log.source_batch == "BATCH-001"

    def test_no_user_returns_none(self):
        log = log_async_task(
            user=None,
            module="配置管理",
            action="创建配置",
            detail="test",
            task_center_id=1,
        )
        assert log is None


@pytest.mark.django_db
class TestLogTaskCenterCreated:
    """log_task_center_created 函数"""

    def test_creates_log_from_task(self, admin_user):
        from apps.releases.models import TaskCenterTask

        task = TaskCenterTask.objects.create(
            operation_type="node_ssh_test",
            status="pending",
            detail="连接测试",
            target_hostnames="node-01",
            trigger_user=admin_user,
        )
        log = log_task_center_created(task)
        assert log is not None
        assert log.module == "节点管理"
        assert log.action == "节点SSH测试"
        assert log.task_center_id == task.id

    def test_unknown_type_uses_default(self, admin_user):
        from apps.releases.models import TaskCenterTask

        task = TaskCenterTask.objects.create(
            operation_type="unknown_op",
            status="pending",
            detail="未知任务",
            trigger_user=admin_user,
        )
        log = log_task_center_created(task)
        assert log is not None
        assert log.module == "任务中心"
        assert log.action == "unknown_op"
