"""权限定义测试。"""

from django.test import TestCase

from apps.users.perm_defs import (
    RESOURCE_CHOICES,
    ACTION_CHOICES,
    PERM_DISPLAY_NAMES,
    permission_code,
    all_permission_items,
)


class TestResourceChoices(TestCase):
    def test_contains_expected_resources(self):
        resources = dict(RESOURCE_CHOICES)
        self.assertIn("nodes", resources)
        self.assertIn("credentials", resources)
        self.assertIn("configs", resources)
        self.assertIn("releases", resources)
        self.assertIn("upgrade", resources)
        self.assertIn("nginx_service", resources)
        self.assertIn("nginx_uninstall", resources)
        self.assertIn("audit", resources)
        self.assertIn("settings", resources)

    def test_no_duplicate_keys(self):
        keys = [k for k, _ in RESOURCE_CHOICES]
        self.assertEqual(len(keys), len(set(keys)))


class TestActionChoices(TestCase):
    def test_contains_expected_actions(self):
        actions = dict(ACTION_CHOICES)
        self.assertIn("read", actions)
        self.assertIn("create", actions)
        self.assertIn("update", actions)
        self.assertIn("delete", actions)
        self.assertIn("execute", actions)

    def test_no_duplicate_keys(self):
        keys = [k for k, _ in ACTION_CHOICES]
        self.assertEqual(len(keys), len(set(keys)))


class TestPermDisplayNames(TestCase):
    def test_all_resources_have_display_names(self):
        for resource, _ in RESOURCE_CHOICES:
            self.assertIn(resource, PERM_DISPLAY_NAMES)

    def test_display_names_are_non_empty(self):
        for resource, actions in PERM_DISPLAY_NAMES.items():
            for action, name in actions.items():
                self.assertTrue(name, f"{resource}.{action} display name is empty")


class TestPermissionCode(TestCase):
    def test_format(self):
        self.assertEqual(permission_code("nodes", "read"), "nodes.read")
        self.assertEqual(permission_code("configs", "sync"), "configs.sync")

    def test_empty(self):
        self.assertEqual(permission_code("", ""), ".")
        self.assertEqual(permission_code("nodes", ""), "nodes.")


class TestAllPermissionItems(TestCase):
    def test_returns_list(self):
        items = all_permission_items()
        self.assertIsInstance(items, list)
        self.assertGreater(len(items), 0)

    def test_each_item_has_required_keys(self):
        for item in all_permission_items():
            self.assertIn("code", item)
            self.assertIn("name", item)
            self.assertIn("resource", item)
            self.assertIn("action", item)

    def test_codes_are_unique(self):
        items = all_permission_items()
        codes = [item["code"] for item in items]
        self.assertEqual(len(codes), len(set(codes)))

    def test_all_resources_represented(self):
        items = all_permission_items()
        resources = set(item["resource"] for item in items)
        for resource, _ in RESOURCE_CHOICES:
            self.assertIn(resource, resources)
