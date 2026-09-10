"""导航上下文测试。"""

from django.test import TestCase, RequestFactory

from utils.nav_context import (
    get_sidebar_nav,
    append_nav_query,
    nav_context,
    ALLOWED_SIDEBAR_NAV,
)


class TestGetSidebarNav(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_get_from_query(self):
        request = self.factory.get("/?nav=upgrade")
        self.assertEqual(get_sidebar_nav(request), "upgrade")

    def test_get_from_post(self):
        request = self.factory.post("/", {"nav": "nginx_service"})
        self.assertEqual(get_sidebar_nav(request), "nginx_service")

    def test_post_overrides_get(self):
        request = self.factory.post("/?nav=upgrade", {"nav": "nginx_install"})
        self.assertEqual(get_sidebar_nav(request), "nginx_install")

    def test_invalid_nav(self):
        request = self.factory.get("/?nav=invalid")
        self.assertEqual(get_sidebar_nav(request), "")

    def test_empty_nav(self):
        request = self.factory.get("/?nav=")
        self.assertEqual(get_sidebar_nav(request), "")

    def test_no_nav(self):
        request = self.factory.get("/")
        self.assertEqual(get_sidebar_nav(request), "")

    def test_allowed_values(self):
        self.assertIn("nginx_install", ALLOWED_SIDEBAR_NAV)
        self.assertIn("upgrade", ALLOWED_SIDEBAR_NAV)
        self.assertIn("nginx_service", ALLOWED_SIDEBAR_NAV)


class TestAppendNavQuery(TestCase):
    def test_append_to_clean_url(self):
        result = append_nav_query("/configs/", "upgrade")
        self.assertIn("nav=upgrade", result)

    def test_override_existing(self):
        result = append_nav_query("/configs/?nav=nginx_install", "upgrade")
        self.assertIn("nav=upgrade", result)
        self.assertNotIn("nav=nginx_install", result)

    def test_invalid_nav(self):
        result = append_nav_query("/configs/", "invalid")
        self.assertEqual(result, "/configs/")

    def test_empty_nav(self):
        result = append_nav_query("/configs/", "")
        self.assertEqual(result, "/configs/")

    def test_preserve_other_params(self):
        result = append_nav_query("/configs/?foo=bar", "upgrade")
        self.assertIn("foo=bar", result)
        self.assertIn("nav=upgrade", result)


class TestNavContext(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_with_nav(self):
        request = self.factory.get("/?nav=upgrade")
        ctx = nav_context(request)
        self.assertEqual(ctx["sidebar_nav"], "upgrade")
        self.assertEqual(ctx["nav_qs"], "?nav=upgrade")
        self.assertIn("&nav=upgrade", ctx["nav_query_suffix"])

    def test_without_nav(self):
        request = self.factory.get("/")
        ctx = nav_context(request)
        self.assertEqual(ctx["sidebar_nav"], "")
        self.assertEqual(ctx["nav_qs"], "")
        self.assertEqual(ctx["nav_query_suffix"], "")
