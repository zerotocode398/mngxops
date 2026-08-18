"""OpenSSH 升级模块测试。"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.nodes.models import Node, NodeGroup
from apps.openssh_upgrade.models import (
    OpenSSHUpgradeTask,
    generate_openssh_batch_number,
)
from apps.openssh_upgrade.services import (
    openssh_gate_message,
    parse_openssh_version,
    preview_nodes,
)

User = get_user_model()


class BatchNumberTests(TestCase):
    """批次号生成：升级 OSI-YYMMDD-NNNN，回滚 OSR-YYMMDD-NNNN，当日自增。"""

    def test_upgrade_prefix(self):
        bn = generate_openssh_batch_number("OSI")
        self.assertTrue(bn.startswith("OSI-"))
        self.assertEqual(len(bn), len("OSI-YYMMDD-NNNN"))

    def test_rollback_prefix(self):
        bn = generate_openssh_batch_number("OSR")
        self.assertTrue(bn.startswith("OSR-"))

    def test_sequential_increment(self):
        from apps.nodes.models import Node

        node = Node.objects.create(
            hostname="seqhost", ip="10.9.9.9", environment="test",
            status="online",
            created_by=User.objects.first()
            or User.objects.create_user(username="sequser", password="pass1234"),
        )
        first = generate_openssh_batch_number("OSI")
        OpenSSHUpgradeTask.objects.create(
            action="upgrade", batch_number=first, node=node,
        )
        second = generate_openssh_batch_number("OSI")
        self.assertNotEqual(first, second)
        self.assertEqual(int(second[-4:]), int(first[-4:]) + 1)


class ParseVersionTests(TestCase):
    """OpenSSH 版本解析。"""

    def test_ssh_v_output(self):
        out = "OpenSSH_9.8p1, OpenSSL 3.0.13 30 Jan 2024"
        self.assertEqual(parse_openssh_version(out), "9.8p1")

    def test_sshd_v_output(self):
        out = "OpenSSH_8.9p1 Ubuntu-3ubuntu0.10, OpenSSL 3.0.2"
        self.assertEqual(parse_openssh_version(out), "8.9p1")

    def test_no_match(self):
        self.assertEqual(parse_openssh_version("nginx version: nginx/1.25.0"), "")


class GateMessageTests(TestCase):
    """升级门禁：锁定/离线/无凭证/禁用凭证。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="tester", password="pass1234"
        )
        self.node = Node.objects.create(
            hostname="host1",
            ip="10.0.0.1",
            port=22,
            environment="test",
            status="online",
            created_by=self.user,
        )

    def test_locked(self):
        self.node.is_locked = True
        self.assertIn("锁定", openssh_gate_message(self.node))

    def test_offline(self):
        self.node.status = "offline"
        self.assertIn("在线", openssh_gate_message(self.node))

    def test_no_credential(self):
        self.assertIn("凭证", openssh_gate_message(self.node))

    def test_allowed_needs_credential(self):
        # 未配置凭证时不允许
        self.assertIsNotNone(openssh_gate_message(self.node))


class PreviewTests(TestCase):
    """预览入口校验。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="tester2", password="pass1234"
        )
        self.node = Node.objects.create(
            hostname="host1",
            ip="10.0.0.2",
            port=22,
            environment="test",
            status="online",
            created_by=self.user,
        )

    def test_empty(self):
        result = preview_nodes([])
        self.assertFalse(result["success"])

    def test_beyond_batch_limit(self):
        ids = list(range(1, 100))
        result = preview_nodes(ids)
        self.assertFalse(result["success"])
        self.assertIn("最多", result["message"])

    def test_missing_node_returns_failure(self):
        result = preview_nodes([99999])
        self.assertFalse(result["success"])
        self.assertIn("不存在", result["message"])