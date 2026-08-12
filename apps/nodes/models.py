from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.credentials.models import Credential

User = get_user_model()


class NodeGroup(models.Model):
    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    name = models.CharField(max_length=100, unique=True, verbose_name="名称")
    description = models.TextField(blank=True, verbose_name="描述")
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name="创建人"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "节点组"
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name


class ActiveNodeManager(models.Manager):
    """仅返回未逻辑删除的节点"""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class Node(models.Model):
    ENV_CHOICES = (
        ("dev", "开发环境"),
        ("test", "测试环境"),
        ("prod", "生产环境"),
    )

    STATUS_CHOICES = (
        ("online", "在线"),
        ("offline", "离线"),
        ("unknown", "未知"),
    )

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    hostname = models.CharField(max_length=100, verbose_name="主机名")
    ip = models.GenericIPAddressField(unique=True, verbose_name="IP地址")
    port = models.IntegerField(default=22, verbose_name="SSH端口")
    groups = models.ManyToManyField(
        NodeGroup,
        related_name="nodes",
        blank=True,
        verbose_name="节点组",
    )
    credential = models.ForeignKey(
        Credential,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="SSH凭证",
        help_text="节点SSH凭证",
    )
    environment = models.CharField(
        max_length=20, choices=ENV_CHOICES, default="dev", verbose_name="环境"
    )
    nginx_version = models.CharField(
        max_length=50, blank=True, verbose_name="Nginx版本"
    )
    nginx_path = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Nginx路径",
        help_text="自定义编译的nginx路径，例如: /usr/local/nginx/sbin/nginx",
    )
    nginx_available = models.BooleanField(
        null=True,
        blank=True,
        verbose_name="Nginx可用",
        help_text="null=未探测，True=已检测到，False=确认不可用；与 SSH status 独立",
    )
    last_nginx_probe_at = models.DateTimeField(
        null=True, blank=True, verbose_name="上次Nginx探测时间",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="unknown", verbose_name="状态"
    )
    last_probe_at = models.DateTimeField(
        null=True, blank=True, verbose_name="上次探测成功时间",
    )
    is_locked = models.BooleanField(default=False, verbose_name="已锁定")
    description = models.TextField(blank=True, verbose_name="描述")
    is_deleted = models.BooleanField(default=False, db_index=True, verbose_name="已删除")
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name="删除时间")
    deleted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deleted_nodes",
        verbose_name="删除人",
    )
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name="创建人"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    objects = ActiveNodeManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name = "节点"
        verbose_name_plural = verbose_name

    @property
    def group(self):
        return self.groups.first()

    @property
    def group_id(self):
        first = self.groups.first()
        return first.id if first else None

    @property
    def is_online(self):
        return self.status == "online"

    @property
    def nginx_status_label(self):
        """Nginx 可用性展示文案（与 SSH 状态分开展示）。"""
        if self.nginx_available is True:
            from apps.releases.task_result import strip_nginx_version

            return strip_nginx_version(self.nginx_version) or "已安装"
        if self.nginx_available is False:
            return "未检测到"
        return "未探测"

    def allows_nginx_ops(self):
        """是否允许依赖 Nginx 的操作（同步/发布/升级/启停等）。"""
        return self.status == "online" and self.nginx_available is True

    def allows_install(self):
        """是否允许 Nginx 安装（仅需 SSH 在线）。"""
        return self.status == "online"

    def soft_delete(self, user=None):
        """将节点标记为逻辑删除，保留发布/升级历史"""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.deleted_by = user
        self.status = "unknown"
        self.save(
            update_fields=[
                "is_deleted",
                "deleted_at",
                "deleted_by",
                "status",
                "updated_at",
            ]
        )

    def restore(self):
        """恢复逻辑删除的节点，保留原主键与历史关联"""
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None
        self.save(
            update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"]
        )

    def __str__(self):
        return f"{self.hostname} ({self.ip})"
