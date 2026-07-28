"""管理命令：按系统设置清理过期历史数据"""

from django.core.management.base import BaseCommand

from utils.data_retention import purge_expired_data


class Command(BaseCommand):
    """执行数据保留清理（可挂 crontab）"""

    help = "按系统设置保留天数清理任务中心/发布历史/操作日志/登录日志"

    def handle(self, *args, **options):
        """执行清理并输出结果"""
        result = purge_expired_data()
        self.stdout.write(
            self.style.SUCCESS(
                "清理完成: "
                f"任务中心={result['task_center']} "
                f"发布历史={result['release_history']} "
                f"操作日志={result['audit_log']} "
                f"登录日志={result['login_log']}"
            )
        )
