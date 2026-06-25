from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class AuditLog(models.Model):
    RESULT_CHOICES = (
        ("success", "成功"),
        ("failed", "失败"),
    )

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        verbose_name="操作用户",
    )
    module = models.CharField(
        max_length=100,
        verbose_name="模块",
    )
    action = models.CharField(
        max_length=255,
        verbose_name="动作",
    )
    ip = models.CharField(
        max_length=50,
        verbose_name="IP地址",
    )
    result = models.CharField(
        max_length=20,
        choices=RESULT_CHOICES,
        verbose_name="结果",
    )
    detail = models.TextField(
        blank=True,
        verbose_name="详情",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="创建时间",
    )

    class Meta:
        verbose_name = "操作日志"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} - {self.module} - {self.action}"


class LoginLog(models.Model):
    STATUS_CHOICES = (
        ("success", "成功"),
        ("failed", "失败"),
    )

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    username = models.CharField(
        max_length=150,
        verbose_name="用户名",
    )
    ip = models.CharField(
        max_length=50,
        verbose_name="IP地址",
    )
    user_agent = models.TextField(
        blank=True,
        verbose_name="浏览器信息",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        verbose_name="结果",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="创建时间",
    )

    class Meta:
        verbose_name = "登录日志"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.username} - {self.get_status_display()} - {self.ip}"
