"""Nginx 全新安装任务模型（与升级任务隔离）"""
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.nodes.models import Node
from apps.upgrade.models import NginxSourcePackage


def generate_install_batch_number():
    """生成安装批次号，格式 IN-YYMMDD-NNNN（当日自增）"""
    today = timezone.now().strftime("%y%m%d")
    prefix = f"IN-{today}-"
    with transaction.atomic():
        last = (
            NginxInstallTask.objects.select_for_update()
            .filter(batch_number__startswith=prefix)
            .order_by("-batch_number")
            .first()
        )
        if last and last.batch_number:
            seq = int(last.batch_number[-4:]) + 1
        else:
            seq = 1
        return f"{prefix}{seq:04d}"


class NginxInstallTask(models.Model):
    """单节点 Nginx 源码编译安装任务"""

    STATUS_CHOICES = (
        ("pending", "等待执行"),
        ("uploading_package", "上传源码包"),
        ("downloading_modules", "准备第三方模块"),
        ("configuring", "执行 configure"),
        ("compiling", "执行 make"),
        ("installing", "make install"),
        ("starting", "启动 Nginx"),
        ("syncing_config", "同步配置"),
        ("verifying", "验证中"),
        ("success", "安装成功"),
        ("failed", "安装失败"),
        ("cancelled", "已取消"),
    )

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    batch_number = models.CharField(
        max_length=32, blank=True, db_index=True, verbose_name="批次号",
    )
    node = models.ForeignKey(Node, on_delete=models.CASCADE, verbose_name="目标节点")
    source_package = models.ForeignKey(
        NginxSourcePackage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="源码包",
    )
    remote_work_dir = models.CharField(
        max_length=500,
        default="/tmp/nginx-install",
        verbose_name="远程编译工作目录",
    )
    target_version = models.CharField(max_length=20, verbose_name="目标版本")
    target_prefix = models.CharField(
        max_length=500,
        default="/usr/local/nginx",
        verbose_name="安装 --prefix",
    )
    target_configure_opts = models.TextField(
        blank=True,
        verbose_name="configure 参数",
    )
    added_modules = models.TextField(default="[]", verbose_name="新增内置模块 JSON")
    added_third_party = models.TextField(default="[]", verbose_name="第三方模块 JSON")
    make_jobs = models.IntegerField(default=4, verbose_name="并行编译数 (-j)")
    listen_port = models.PositiveIntegerField(
        default=80,
        verbose_name="监听端口",
        help_text="安装后写入主配置 listen 的端口",
    )

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending", verbose_name="状态",
    )
    progress = models.IntegerField(default=0, verbose_name="进度百分比")
    current_step = models.CharField(max_length=255, blank=True, verbose_name="当前步骤")
    log_output = models.TextField(blank=True, verbose_name="完整输出日志")
    error_message = models.TextField(blank=True, verbose_name="错误信息")
    sync_ok = models.BooleanField(null=True, blank=True, verbose_name="配置同步是否成功")
    sync_detail = models.CharField(max_length=255, blank=True, verbose_name="配置同步摘要")

    task_center = models.ForeignKey(
        "releases.TaskCenterTask",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nginx_install_tasks",
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
        verbose_name = "Nginx 安装任务"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return f"安装 {self.target_version} @ {self.node_id} ({self.status})"
