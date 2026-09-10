"""模板标签 config_filters 测试。"""

from django.test import TestCase, RequestFactory
from django.http import QueryDict
from apps.configs.templatetags.config_filters import (
    dict_get,
    binding_sync_status_badge,
    binding_source_badge,
    pagination_url,
)


class TestDictGetFilter(TestCase):
    def test_existing_key(self):
        self.assertEqual(dict_get({"a": 1, "b": 2}, "a"), 1)

    def test_missing_key(self):
        self.assertEqual(dict_get({"a": 1}, "missing"), "")

    def test_none_dict(self):
        self.assertEqual(dict_get(None, "key"), "")


class TestBindingSyncStatusBadge(TestCase):
    def test_synced_badge(self):
        html = binding_sync_status_badge("synced")
        self.assertIn("已同步", html)
        self.assertIn("bg-success", html)

    def test_not_synced_badge(self):
        html = binding_sync_status_badge("not_synced")
        self.assertIn("未同步", html)
        self.assertIn("bg-secondary", html)

    def test_modified_badge(self):
        html = binding_sync_status_badge("modified")
        self.assertIn("待推送", html)
        self.assertIn("bg-primary", html)

    def test_conflict_badge(self):
        html = binding_sync_status_badge("conflict")
        self.assertIn("冲突", html)
        self.assertIn("bg-warning", html)

    def test_orphaned_badge(self):
        html = binding_sync_status_badge("orphaned")
        self.assertIn("远程已删除", html)
        self.assertIn("bg-danger", html)

    def test_syncing_badge(self):
        html = binding_sync_status_badge("syncing")
        self.assertIn("同步中", html)
        self.assertIn("bg-info", html)

    def test_failed_badge(self):
        html = binding_sync_status_badge("failed")
        self.assertIn("同步失败", html)
        self.assertIn("bg-danger", html)

    def test_unknown_status(self):
        html = binding_sync_status_badge("unknown")
        self.assertIn("unknown", html)


class TestBindingSourceBadge(TestCase):
    def test_discovered_badge(self):
        html = binding_source_badge("discovered")
        self.assertIn("远程发现", html)
        self.assertIn("bg-info", html)

    def test_manual_badge(self):
        html = binding_source_badge("manual")
        self.assertIn("手动绑定", html)


class TestPaginationUrl(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_pagination_url_with_request(self):
        request = self.factory.get("/configs/?q=test")
        context = {"request": request}
        url = pagination_url(context, 3)
        self.assertIn("page=3", url)
        self.assertIn("q=test", url)

    def test_pagination_url_no_request(self):
        context = {}
        url = pagination_url(context, 2)
        self.assertEqual(url, "?page=2")
