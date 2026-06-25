from django.db import models
from django.contrib.auth import get_user_model
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
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="unknown", verbose_name="状态"
    )
    description = models.TextField(blank=True, verbose_name="描述")
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name="创建人"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "节点"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.hostname} ({self.ip})"
