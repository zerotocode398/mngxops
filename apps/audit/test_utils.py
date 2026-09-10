"""审计日志工具函数测试。"""

import json
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.audit.models import AuditLog
from apps.audit.utils import (
    log_async_task,
    log_task_center_created,
    OPERATION_AUDIT_MAP,
)
from apps.releases.models import TaskCenterTask


class TestLogAsyncTask(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="audit-test",
            email="audit@example.com",
            password="pass1234",
        )

    def test_log_async_task_creates_audit_log(self):
        log = log_async_task(
            user=self.user,
            module="节点管理",
            action="节点SSH测试",
            detail="测试连接 10.0.0.1",
            task_center_id=1,
            source_batch="batch-001",
        )
        self.assertIsNotNone(log)
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.module, "节点管理")
        self.assertEqual(log.action, "节点SSH测试")
        self.assertEqual(log.detail, "测试连接 10.0.0.1")
        self.assertEqual(log.task_center_id, 1)
        self.assertEqual(log.source_batch, "batch-001")
        self.assertEqual(log.result, "success")

    def test_log_async_task_with_none_user(self):
        log = log_async_task(
            user=None,
            module="任务中心",
            action="其他任务",
            detail="匿名操作",
            task_center_id=0,
        )
        self.assertIsNone(log)

    def test_log_async_task_defaults(self):
        log = log_async_task(
            user=self.user,
            module="配置管理",
            action="配置批量同步",
            detail="同步完成",
            task_center_id=5,
        )
        self.assertIsNotNone(log)
        self.assertEqual(log.source_batch, "")
        self.assertEqual(log.result, "success")


class TestOperationAuditMap(TestCase):
    def test_map_contains_expected_keys(self):
        expected_keys = [
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
        for key in expected_keys:
            self.assertIn(key, OPERATION_AUDIT_MAP)


class TestLogTaskCenterCreated(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="tc-audit",
            email="tc-audit@example.com",
            password="pass1234",
        )

    def test_log_task_center_created(self):
        task = TaskCenterTask.objects.create(
            operation_type="release_publish",
            status="pending",
            detail="待执行任务 1 个",
            target_hostnames="web-a",
            target_ips="10.0.0.1",
            source_batch="release-260101-0001",
            trigger_user=self.user,
        )
        log = log_task_center_created(task)
        self.assertIsNotNone(log)
        self.assertEqual(log.module, "发布管理")
        self.assertEqual(log.action, "发布配置")
        self.assertEqual(log.task_center_id, task.id)
        self.assertEqual(log.source_batch, "release-260101-0001")

    def test_log_task_center_created_unknown_type(self):
        task = TaskCenterTask.objects.create(
            operation_type="unknown_op",
            status="pending",
            detail="未知操作",
            target_hostnames="web-b",
            target_ips="10.0.0.2",
            source_batch="batch-002",
            trigger_user=self.user,
        )
        log = log_task_center_created(task)
        self.assertIsNotNone(log)
        self.assertEqual(log.module, "任务中心")
        self.assertEqual(log.action, "unknown_op")
