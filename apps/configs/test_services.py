"""配置管理服务层测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.configs.services import (
    default_nginx_conf_path,
    discover_max_depth,
    SKIP_FILES,
    _sync_step_label,
)
from apps.configs.models import Config, ConfigNodeBinding, ConfigSyncSetting
from apps.nodes.models import Node


class TestDefaultNginxConfPath(TestCase):
    def test_returns_string(self):
        path = default_nginx_conf_path()
        self.assertIsInstance(path, str)
        self.assertTrue(len(path) > 0)


class TestDiscoverMaxDepth(TestCase):
    def test_returns_positive_int(self):
        depth = discover_max_depth()
        self.assertIsInstance(depth, int)
        self.assertGreaterEqual(depth, 1)


class TestSkipFiles(TestCase):
    def test_contains_mime_types(self):
        self.assertIn("mime.types", SKIP_FILES)


class TestSyncStepLabel(TestCase):
    def test_created(self):
        self.assertEqual(_sync_step_label("created"), "新建")

    def test_updated(self):
        self.assertEqual(_sync_step_label("updated"), "更新")

    def test_deleted(self):
        self.assertEqual(_sync_step_label("deleted"), "清理删除")

    def test_skipped(self):
        self.assertEqual(_sync_step_label("skipped"), "跳过")

    def test_unknown(self):
        self.assertEqual(_sync_step_label("unknown"), "unknown")


class TestGetOrCreateSyncSetting(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="cfg-sync",
            email="cfg-sync@example.com",
            password="pass1234",
        )
        self.node = Node.objects.create(
            hostname="sync-node",
            ip="10.0.0.20",
            status="online",
            created_by=self.user,
        )

    def test_get_or_create(self):
        from apps.configs.services import get_or_create_sync_setting

        setting = get_or_create_sync_setting(self.node, user=self.user)
        self.assertIsInstance(setting, ConfigSyncSetting)
        self.assertEqual(setting.node, self.node)

    def test_reuse_existing(self):
        from apps.configs.services import get_or_create_sync_setting

        setting1 = get_or_create_sync_setting(self.node, user=self.user)
        setting2 = get_or_create_sync_setting(self.node, user=self.user)
        self.assertEqual(setting1.id, setting2.id)


class TestSaveSyncPath(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="sync-path",
            email="sync-path@example.com",
            password="pass1234",
        )
        self.node = Node.objects.create(
            hostname="path-node",
            ip="10.0.0.21",
            status="online",
            created_by=self.user,
        )

    def test_save_sync_path(self):
        from apps.configs.services import save_sync_path

        setting = save_sync_path(
            self.node,
            "/etc/nginx/nginx.conf",
            user=self.user,
        )
        self.assertEqual(setting.main_conf_path, "/etc/nginx/nginx.conf")
        self.assertEqual(setting.updated_by, self.user)
