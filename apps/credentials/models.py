from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Credential(models.Model):
    AUTH_TYPE_CHOICES = (
        ("password", "密码认证"),
        ("key", "密钥认证"),
    )

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    name = models.CharField(max_length=100, verbose_name="名称")
    username = models.CharField(max_length=100, verbose_name="SSH用户")
    auth_type = models.CharField(
        max_length=20,
        choices=AUTH_TYPE_CHOICES,
        default="password",
        verbose_name="认证方式",
    )
    password = models.TextField(blank=True, verbose_name="密码")
    private_key = models.TextField(blank=True, verbose_name="私钥")
    description = models.TextField(blank=True, verbose_name="描述")
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name="创建人"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "SSH凭证"
        verbose_name_plural = verbose_name
        unique_together = [["name", "created_by"]]

    def __str__(self):
        return self.name
