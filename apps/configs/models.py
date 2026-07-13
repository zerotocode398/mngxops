from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from apps.nodes.models import Node

User = get_user_model()


class Config(models.Model):
    """配置标签 - 定义"这是什么类型的配置"，不保存实际内容
    实际内容和版本历史存放在 ConfigNodeBinding 中
    """
    SOURCE_CHOICES = (
        ("manual", "手动创建"),
        ("discovered", "远程发现导入"),
    )

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    name = models.CharField(max_length=255, verbose_name="配置名称")
    default_remote_path = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="默认远程路径",
        help_text="如 /etc/nginx/conf.d/app.conf，创建绑定时自动填入，可修改",
    )
    template_content = models.TextField(
        blank=True,
        verbose_name="内容模板",
        help_text="创建绑定时若远程无此文件，可基于此模板生成初始内容",
    )
    source = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, default="manual", verbose_name="来源",
    )
    description = models.TextField(blank=True, verbose_name="描述")
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name="创建人",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "配置"
        verbose_name_plural = verbose_name
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name

    @property
    def binding_count(self):
        return self.bindings.count()

    @property
    def node_names(self):
        return ", ".join(b.node.hostname for b in self.bindings.select_related("node").all())


class ConfigNodeBinding(models.Model):
    """配置与节点的绑定关系
    每条绑定独立存储内容、版本、路径、同步状态
    """
    BINDING_SOURCE_CHOICES = (
        ("manual", "手动绑定"),
        ("discovered", "远程发现"),
    )

    SYNC_STATUS_CHOICES = (
        ("not_synced", "未同步"),
        ("synced", "已同步"),
        ("modified", "本地已修改"),
        ("conflict", "冲突"),
        ("orphaned", "远程已删除"),
        ("syncing", "同步中"),
        ("failed", "同步失败"),
    )

    id = models.BigAutoField(primary_key=True)
    config = models.ForeignKey(
        Config, on_delete=models.CASCADE, related_name="bindings", verbose_name="配置标签",
    )
    node = models.ForeignKey(
        Node, on_delete=models.CASCADE, related_name="config_bindings", verbose_name="节点",
    )
    remote_path = models.CharField(
        max_length=500,
        verbose_name="远程文件路径",
        help_text="此配置在该节点上的绝对路径",
    )
    content = models.TextField(verbose_name="当前内容")
    current_version = models.IntegerField(default=1, verbose_name="当前版本号")
    sync_status = models.CharField(
        max_length=20, choices=SYNC_STATUS_CHOICES, default="not_synced",
        verbose_name="同步状态",
    )
    synced_version = models.IntegerField(
        null=True, blank=True,
        verbose_name="已同步版本",
        help_text="最后成功推送的版本号",
    )
    last_sync_time = models.DateTimeField(null=True, blank=True, verbose_name="最后同步时间")
    last_sync_error = models.TextField(blank=True, verbose_name="最后同步错误")
    remote_content_hash = models.CharField(
        max_length=64, blank=True,
        verbose_name="远程内容 Hash(MD5)",
        help_text="最后同步时记录的远程文件 MD5，用于检测漂移",
    )
    drift_detected_at = models.DateTimeField(null=True, blank=True, verbose_name="漂移检测时间")
    source = models.CharField(max_length=20, choices=BINDING_SOURCE_CHOICES, default="manual")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="创建人")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "配置节点绑定"
        verbose_name_plural = verbose_name
        unique_together = ("config", "node")
        ordering = ["config__name", "node__hostname"]

    def __str__(self):
        return f"{self.config.name} @ {self.node.hostname} (v{self.current_version})"

    @property
    def is_synced(self):
        return self.sync_status == "synced" and self.synced_version == self.current_version

    @property
    def is_modified(self):
        return self.sync_status == "modified"


class BindingVersion(models.Model):
    """每条绑定的独立版本历史"""
    id = models.BigAutoField(primary_key=True)
    binding = models.ForeignKey(
        ConfigNodeBinding, on_delete=models.CASCADE, related_name="versions",
        verbose_name="绑定",
    )
    version = models.IntegerField(verbose_name="版本号")
    content = models.TextField(verbose_name="版本内容")
    remark = models.TextField(blank=True, verbose_name="备注")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="修改人")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "绑定版本"
        verbose_name_plural = verbose_name
        ordering = ["-version"]
        unique_together = ("binding", "version")

    def __str__(self):
        return f"{self.binding.config.name}@v{self.version}"

    @property
    def content_bytes(self):
        return len(self.content.encode("utf-8"))

    @property
    def version_label(self):
        return f"{self.created_at.strftime('%Y%m%d')} - V{self.version}"


# === 保留旧模型以支持平滑迁移 ===
# 迁移完成后可废弃
class ConfigVersion(models.Model):
    """【待废弃】旧版配置版本 - 迁移到 BindingVersion 后可删除"""
    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    config = models.ForeignKey(
        Config,
        on_delete=models.CASCADE,
        related_name="legacy_versions",
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
        verbose_name = "配置版本(旧)"
        verbose_name_plural = verbose_name
        ordering = ["-version"]
        unique_together = ("config", "version")

    def __str__(self):
        return f"{self.config.name} - v{self.version}"

    @property
    def content_bytes(self):
        return len(self.content.encode("utf-8"))


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