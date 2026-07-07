from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from apps.nodes.models import Node

User = get_user_model()


class Config(models.Model):
    SYNC_STATUS_CHOICES = [
        ("pending", "等待同步"),
        ("syncing", "同步中"),
        ("success", "同步成功"),
        ("failed", "同步失败"),
        ("orphaned", "远程已删除"),
    ]

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    node = models.ForeignKey(
        Node,
        on_delete=models.CASCADE,
        verbose_name="关联节点",
    )
    name = models.CharField(max_length=255, verbose_name="配置名称")
    file_path = models.CharField(max_length=500, verbose_name="配置文件路径")
    content = models.TextField(verbose_name="配置内容")
    current_version = models.IntegerField(default=1, verbose_name="当前版本号")
    sync_status = models.CharField(
        max_length=20,
        choices=SYNC_STATUS_CHOICES,
        default="pending",
        verbose_name="同步状态",
    )
    last_sync_time = models.DateTimeField(
        null=True, blank=True, verbose_name="最后同步时间"
    )
    last_sync_error = models.TextField(blank=True, verbose_name="最后同步错误信息")
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        verbose_name="创建人",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "配置"
        verbose_name_plural = verbose_name
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.node.hostname} - {self.name}"

    def prune_old_versions(self, retention_days=180):
        cutoff_time = timezone.now() - timedelta(days=retention_days)
        self.versions.filter(created_at__lt=cutoff_time).delete()


class ConfigVersion(models.Model):
    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    config = models.ForeignKey(
        Config,
        on_delete=models.CASCADE,
        related_name="versions",
        verbose_name="关联配置",
    )
    version = models.IntegerField(verbose_name="版本号")
    content = models.TextField(verbose_name="配置内容")
    remark = models.TextField(blank=True, verbose_name="备注")
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        verbose_name="修改人",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "配置版本"
        verbose_name_plural = verbose_name
        ordering = ["-version"]
        unique_together = ("config", "version")

    @property
    def content_bytes(self):
        return len(self.content.encode("utf-8"))

    @property
    def version_label(self):
        return f"{self.created_at.strftime('%Y%m%d')} - V{self.version}"

    def __str__(self):
        return f"{self.config.name} - v{self.version}"


class ConfigSyncSetting(models.Model):
    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    node = models.OneToOneField(
        Node,
        on_delete=models.CASCADE,
        related_name="config_sync_setting",
        verbose_name="关联节点",
    )
    main_conf_path = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="nginx.conf 主路径",
    )
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="最后更新人",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="最后更新时间")

    class Meta:
        verbose_name = "配置同步设置"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.node.hostname}: {self.main_conf_path}"
