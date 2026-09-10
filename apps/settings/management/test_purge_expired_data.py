"""数据过期清理管理命令测试。"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase


class TestPurgeExpiredDataCommand(TestCase):
    def test_command_output_format(self):
        out = StringIO()
        call_command("purge_expired_data", stdout=out)
        output = out.getvalue()
        self.assertIn("清理完成", output)
        self.assertIn("任务中心", output)
        self.assertIn("发布历史", output)
        self.assertIn("操作日志", output)
        self.assertIn("登录日志", output)

    def test_command_help(self):
        from django.core.management import call_command, CommandError

        try:
            call_command("purge_expired_data", "--help")
        except SystemExit:
            pass
