"""任务结果格式化测试。"""

from django.test import TestCase

from apps.releases.task_result import (
    strip_nginx_version,
    upgrade_detail_short,
    node_header,
    item_success,
    item_failed,
    build_tree_result,
    short_error_tail,
    split_failed_item,
    split_error_reason_lines,
    _shorten_hostnames,
    _extract_success_fail,
    _shorten_nginx_secondary,
    format_task_center_summary,
)


class TestStripNginxVersion(TestCase):
    def test_nginx_prefix(self):
        self.assertEqual(strip_nginx_version("nginx/1.24.0"), "1.24.0")

    def test_nginx_dash(self):
        self.assertEqual(strip_nginx_version("nginx-1.26.1"), "1.26.1")

    def test_plain(self):
        self.assertEqual(strip_nginx_version("1.24.0"), "1.24.0")

    def test_none(self):
        self.assertEqual(strip_nginx_version(None), "")

    def test_empty(self):
        self.assertEqual(strip_nginx_version(""), "")


class TestUpgradeDetailShort(TestCase):
    def test_versions(self):
        result = upgrade_detail_short("1.24.0", "1.26.1")
        self.assertIn("1.24.0", result)
        self.assertIn("1.26.1", result)
        self.assertIn("→", result)

    def test_unknown_current(self):
        result = upgrade_detail_short("", "1.26.1")
        self.assertIn("未知", result)
        self.assertIn("1.26.1", result)


class TestNodeHeader(TestCase):
    def test_both(self):
        result = node_header("10.0.0.1", "web01")
        self.assertIn("10.0.0.1", result)
        self.assertIn("web01", result)

    def test_hostname_only(self):
        result = node_header("", "web01")
        self.assertIn("web01", result)
        self.assertIn("[节点]", result)

    def test_ip_only(self):
        result = node_header("10.0.0.1", "")
        self.assertIn("10.0.0.1", result)


class TestItemSuccess(TestCase):
    def test_basic(self):
        result = item_success("nginx.conf")
        self.assertIn("[成功]", result)
        self.assertIn("nginx.conf", result)


class TestItemFailed(TestCase):
    def test_with_reason(self):
        result = item_failed("nginx.conf", reason="连接超时")
        self.assertIn("[失败]", result)
        self.assertIn("失败原因", result)
        self.assertIn("连接超时", result)

    def test_no_reason(self):
        result = item_failed("nginx.conf")
        self.assertIn("[失败]", result)
        self.assertNotIn("失败原因", result)


class TestBuildTreeResult(TestCase):
    def test_basic(self):
        result = build_tree_result(
            success_count=2,
            fail_count=1,
            total=3,
            node_blocks=["[节点] web01", "  [成功] app.conf"],
        )
        self.assertIn("成功 2", result)
        self.assertIn("失败 1", result)
        self.assertIn("共 3", result)


class TestShortErrorTail(TestCase):
    def test_short(self):
        self.assertEqual(short_error_tail("error", max_lines=5), "error")

    def test_long(self):
        lines = [f"error line {i}" for i in range(10)]
        text = "\n".join(lines)
        result = short_error_tail(text, max_lines=5)
        self.assertLessEqual(result.count("|"), 4)

    def test_empty(self):
        self.assertEqual(short_error_tail(""), "")
        self.assertEqual(short_error_tail(None), "")


class TestSplitFailedItem(TestCase):
    def test_with_reason(self):
        label, reason = split_failed_item("操作 - 失败原因: 连接超时")
        self.assertEqual(label, "操作")
        self.assertEqual(reason, "连接超时")

    def test_no_reason(self):
        label, reason = split_failed_item("操作")
        self.assertEqual(label, "操作")
        self.assertEqual(reason, "")

    def test_empty(self):
        label, reason = split_failed_item("")
        self.assertEqual(label, "")
        self.assertEqual(reason, "")


class TestSplitErrorReasonLines(TestCase):
    def test_single(self):
        lines = split_error_reason_lines("连接超时")
        self.assertEqual(lines, ["连接超时"])

    def test_pipe_separated(self):
        lines = split_error_reason_lines("连接超时 | 权限不足")
        self.assertEqual(len(lines), 2)

    def test_empty(self):
        self.assertEqual(split_error_reason_lines(""), [])
        self.assertEqual(split_error_reason_lines(None), [])


class TestShortenHostnames(TestCase):
    def test_few(self):
        result = _shorten_hostnames("web01,web02", limit=3)
        self.assertEqual(result, "web01,web02")

    def test_many(self):
        result = _shorten_hostnames("a,b,c,d,e", limit=3)
        self.assertIn("等5台", result)

    def test_empty(self):
        self.assertEqual(_shorten_hostnames(""), "")
        self.assertEqual(_shorten_hostnames("  "), "")


class TestExtractSuccessFail(TestCase):
    def test_both(self):
        result = _extract_success_fail("成功 2，失败 1")
        self.assertIn("成功 2", result)
        self.assertIn("失败 1", result)

    def test_none(self):
        self.assertEqual(_extract_success_fail(""), "")
        self.assertEqual(_extract_success_fail(None), "")


class TestShortenNginxSecondary(TestCase):
    def test_version_arrow(self):
        result = _shorten_nginx_secondary("1.24.0 → 1.26.1")
        self.assertIn("1.24.0", result)
        self.assertIn("1.26.1", result)

    def test_running_prefix(self):
        result = _shorten_nginx_secondary("正在编译...")
        self.assertEqual(result, "编译")

    def test_empty(self):
        self.assertEqual(_shorten_nginx_secondary(""), "")
        self.assertEqual(_shorten_nginx_secondary(None), "")
