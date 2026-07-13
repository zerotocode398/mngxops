from django.db import models, transaction
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.nodes.models import Node
from apps.configs.models import Config, ConfigNodeBinding, BindingVersion

User = get_user_model()


class ReleaseTask(models.Model):
    """发布任务 - 每条记录 = 某条绑定 + 某个版本 发布到远程节点"""
    STATUS_CHOICES = (
        ("pending", "等待发布"),
        ("running", "发布中"),
        ("success", "发布成功"),
        ("failed", "发布失败"),
        ("rollback", "已回滚"),
        ("cancelled", "已取消"),
    )

    id = models.BigAutoField(primary_key=True)
    batch_number = models.CharField(max_length=32, db_index=True, verbose_name="批次号")

    # 核心关联：绑定 + 版本
    binding = models.ForeignKey(
        ConfigNodeBinding,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="release_tasks",
        verbose_name="关联绑定",
        help_text="发布将沿用绑定的 remote_path 和 content",
    )
    config = models.ForeignKey(
        Config, on_delete=models.CASCADE, verbose_name="配置标签",
    )
    node = models.ForeignKey(
        Node, on_delete=models.CASCADE, verbose_name="目标节点",
    )
    # 兼容：保留旧 version FK + 新增 publish_version
    version = models.ForeignKey(
        BindingVersion,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name="发布版本",
    )
    publish_version = models.IntegerField(
        null=True, blank=True,
        verbose_name="发布版本号",
        help_text="绑定的版本号，如 V3 表示绑定第 3 版",
    )
    remote_path = models.CharField(max_length=500, blank=True, verbose_name="远程路径")

    operator = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="操作人")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", verbose_name="状态")
    result = models.TextField(blank=True, verbose_name="执行结果")
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="完成时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "发布任务"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.config.name} v{self.publish_version or ''} → {self.node.hostname}"

    @property
    def content_to_publish(self):
        """发布时使用的配置内容 —— 从绑定的 BindingVersion 中读取"""
        if self.binding and self.publish_version:
            try:
                bv = self.binding.versions.get(version=self.publish_version)
                return bv.content
            except BindingVersion.DoesNotExist:
                pass
        return self.binding.content if self.binding else ""


class ReleaseHistory(models.Model):
    ACTION_CHOICES = (
        ("publish", "发布"),
        ("rollback", "回滚"),
    )

    id = models.BigAutoField(primary_key=True)
    release_task = models.ForeignKey(
        ReleaseTask, on_delete=models.CASCADE, related_name="history", verbose_name="关联任务",
    )
    node = models.ForeignKey(Node, on_delete=models.CASCADE, verbose_name="目标节点")
    config = models.ForeignKey(Config, on_delete=models.CASCADE, verbose_name="目标配置")
    version = models.IntegerField(verbose_name="版本号")
    operator = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="操作人")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, verbose_name="操作类型")
    result = models.TextField(blank=True, verbose_name="执行结果")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "发布历史"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_action_display()} - {self.config.name} v{self.version}"


class TaskCenterTask(models.Model):
    OPERATION_TYPE_CHOICES = (
        ("release_publish", "发布配置"),
        ("release_rollback", "回滚配置"),
        ("credential_enable_test", "凭证启用测试"),
        ("node_batch_test", "节点批量测试"),
        ("node_system_info", "节点系统信息采集"),
        ("node_nginx_version", "Nginx 版本检测"),
        ("config_batch_sync", "配置批量同步"),
        ("config_discover", "配置发现扫描"),
        ("config_drift_check", "配置漂移检测"),
        ("nginx_upgrade", "Nginx 编译升级"),
        ("nginx_rollback", "Nginx 升级回滚"),
        ("other", "其他任务"),
    )

    STATUS_CHOICES = (
        ("pending", "等待中"),
        ("running", "执行中"),
        ("success", "成功"),
        ("failed", "失败"),
        ("cancelled", "已取消"),
    )

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    operation_type = models.CharField(
        max_length=40, choices=OPERATION_TYPE_CHOICES, default="other", verbose_name="任务类型",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending", verbose_name="状态",
    )
    detail = models.TextField(blank=True, verbose_name="任务说明")
    result = models.TextField(blank=True, verbose_name="任务结果")
    progress = models.IntegerField(default=0, verbose_name="进度")
    source_batch = models.CharField(max_length=64, blank=True, verbose_name="来源批次")
    target_hostnames = models.TextField(blank=True, verbose_name="目标主机名")
    target_ips = models.TextField(blank=True, verbose_name="目标IP")
    target_configs = models.TextField(blank=True, verbose_name="目标配置")
    trigger_user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="触发人",
    )
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="完成时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "任务中心"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_operation_type_display()} #{self.id}"


def generate_batch_number():
    today = timezone.now().strftime("%y%m%d")
    prefix = f"release-{today}-"

    with transaction.atomic():
        last = (
            ReleaseTask.objects.select_for_update()
            .filter(batch_number__startswith=prefix)
            .order_by("-batch_number")
            .first()
        )
        if last:
            seq = int(last.batch_number[-4:]) + 1
        else:
            seq = 1
        return f"{prefix}{seq:04d}"