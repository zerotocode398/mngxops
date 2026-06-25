from django.db import models, transaction
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.nodes.models import Node
from apps.configs.models import Config, ConfigVersion

User = get_user_model()


class ReleaseTask(models.Model):
    STATUS_CHOICES = (
        ("pending", "等待发布"),
        ("running", "发布中"),
        ("success", "发布成功"),
        ("failed", "发布失败"),
        ("rollback", "已回滚"),
        ("cancelled", "已取消"),
    )

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    batch_number = models.CharField(
        max_length=32,
        blank=True,
        db_index=True,
        verbose_name="批次号",
    )
    node = models.ForeignKey(
        Node,
        on_delete=models.CASCADE,
        verbose_name="目标节点",
    )
    config = models.ForeignKey(
        Config,
        on_delete=models.CASCADE,
        verbose_name="目标配置",
    )
    version = models.ForeignKey(
        ConfigVersion,
        on_delete=models.CASCADE,
        verbose_name="发布版本",
    )
    operator = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        verbose_name="操作人",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        verbose_name="状态",
    )
    result = models.TextField(
        blank=True,
        verbose_name="执行结果",
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="开始时间",
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="完成时间",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="创建时间",
    )

    class Meta:
        verbose_name = "发布任务"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.config.name} v{self.version.version} → {self.node.hostname}"


class ReleaseHistory(models.Model):
    ACTION_CHOICES = (
        ("publish", "发布"),
        ("rollback", "回滚"),
    )

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    release_task = models.ForeignKey(
        ReleaseTask,
        on_delete=models.CASCADE,
        related_name="history",
        verbose_name="关联任务",
    )
    node = models.ForeignKey(
        Node,
        on_delete=models.CASCADE,
        verbose_name="目标节点",
    )
    config = models.ForeignKey(
        Config,
        on_delete=models.CASCADE,
        verbose_name="目标配置",
    )
    version = models.IntegerField(verbose_name="版本号")
    operator = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        verbose_name="操作人",
    )
    action = models.CharField(
        max_length=20,
        choices=ACTION_CHOICES,
        verbose_name="操作类型",
    )
    result = models.TextField(
        blank=True,
        verbose_name="执行结果",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="创建时间",
    )

    class Meta:
        verbose_name = "发布历史"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_action_display()} - {self.config.name} v{self.version}"


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
