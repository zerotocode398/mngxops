"""任务进度功能测试。"""

from django.test import TestCase

from apps.releases.task_progress import (
    _truncate_middle,
    _release_step_label,
    _format_current_steps,
    _set_current_step,
    _clear_release_progress_state,
)


class TestTruncateMiddle(TestCase):
    def test_short_text(self):
        self.assertEqual(_truncate_middle("hello", max_len=60), "hello")

    def test_long_text(self):
        long_text = (
            "/very/long/path/that/exceeds/the/maximum/length/for/display/config.conf"
        )
        result = _truncate_middle(long_text, max_len=60)
        self.assertLessEqual(len(result), 60)
        self.assertIn("...", result)

    def test_empty(self):
        self.assertEqual(_truncate_middle("", max_len=60), "")

    def test_none(self):
        self.assertEqual(_truncate_middle(None, max_len=60), "")


class TestReleaseStepLabel(TestCase):
    def test_phase_only(self):
        label = _release_step_label("推送配置")
        self.assertEqual(label, "推送配置")

    def test_with_config(self):
        label = _release_step_label("推送配置", config_name="nginx.conf", version=3)
        self.assertIn("推送配置", label)
        self.assertIn("nginx.conf", label)
        self.assertIn("v3", label)

    def test_with_remote_path(self):
        label = _release_step_label(
            "推送配置",
            config_name="nginx.conf",
            version=1,
            remote_path="/etc/nginx/nginx.conf",
        )
        self.assertIn("nginx.conf", label)
        self.assertIn("→", label)

    def test_with_extra(self):
        label = _release_step_label("校验", extra="diff 对比")
        self.assertIn("校验", label)
        self.assertIn("diff 对比", label)

    def test_latest_version(self):
        label = _release_step_label(
            "推送配置", config_name="app.conf", version="latest"
        )
        self.assertIn("latest", label)


class TestProgressStateManagement(TestCase):
    def test_set_and_format(self):
        _set_current_step(9999, "node01", "推送配置 · nginx.conf v3")
        result = _format_current_steps(9999)
        self.assertIn("node01", result)
        self.assertIn("nginx.conf", result)
        _clear_release_progress_state(9999)

    def test_clear_state(self):
        _set_current_step(9998, "node02", "备份配置")
        _clear_release_progress_state(9998)
        result = _format_current_steps(9998)
        self.assertEqual(result, "")

    def test_clear_none_id(self):
        _clear_release_progress_state(None)
        _clear_release_progress_state("")

    def test_set_step_none(self):
        _set_current_step(9997, "node03", "部署中")
        _set_current_step(9997, "node03", None)
        result = _format_current_steps(9997)
        self.assertEqual(result, "")
        _clear_release_progress_state(9997)

    def test_empty_format(self):
        result = _format_current_steps(99999)
        self.assertEqual(result, "")
