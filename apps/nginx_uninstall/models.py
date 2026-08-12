"""Nginx 卸载任务模型"""
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.nodes.models import Node


def generate_uninstall_batch_number():
    """生成卸载批次号，格式 UN-YYMMDD-NNNN（当日自增）"""
    today = timezone.now().strftime("%y%m%d")
    prefix = f"UN-{today}-"
    with transaction.atomic():
        last = (
            NginxUninstallTask.objects.select_for_update()
            .filter(batch_number__startswith=prefix)
            .order_by("-batch_number")
            .first()
        )
        if last and last.batch_number:
            seq = int(last.batch_number[-4:]) + 1
        else:
            seq = 1
        return f"{prefix}{seq:04d}"


class NginxUninstallTask(models.Model):
    """单节点 Nginx 卸载任务"""

    STATUS_CHOICES = (
        ("pending", "等待执行"),
        ("stopping", "停止服务"),
        ("removing_prefix", "删除安装目录"),
        ("removing_backup", "清理发布备份"),
        ("removing_extra", "清理额外目录"),
        ("updating_node", "更新节点状态"),
        ("success", "卸载成功"),
        ("failed", "卸载失败"),
        ("cancelled", "已取消"),
    )

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    batch_number = models.CharField(
        max_length=32, blank=True, db_index=True, verbose_name="批次号",
    )
    node = models.ForeignKey(Node, on_delete=models.CASCADE, verbose_name="目标节点")
    resolved_prefix = models.CharField(max_length=500, verbose_name="卸载 --prefix")
    backup_path = models.CharField(
        max_length=500, blank=True, verbose_name="拟删发布备份路径",
    )
    work_dir = models.CharField(
        max_length=500, blank=True, verbose_name="编译工作目录",
    )
    options_json = models.TextField(
        default="{}",
        verbose_name="删除选项 JSON",
        help_text="按节点：remove_* / stop_if_running / work_dir / modules_dir / extra_paths",
    )

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending", verbose_name="状态",
    )
    progress = models.IntegerField(default=0, verbose_name="进度百分比")
    current_step = models.CharField(max_length=255, blank=True, verbose_name="当前步骤")
    log_output = models.TextField(blank=True, verbose_name="完整输出日志")
    error_message = models.TextField(blank=True, verbose_name="错误信息")

    task_center = models.ForeignKey(
        "releases.TaskCenterTask",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nginx_uninstall_tasks",
        verbose_name="关联任务中心",
    )
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="操作人",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="完成时间")

    class Meta:
        verbose_name = "Nginx 卸载任务"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return f"卸载 {self.resolved_prefix} @ {self.node_id} ({self.status})"
