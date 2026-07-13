from django.db import models
from django.contrib.auth import get_user_model

from utils.crypto import encrypt_value, decrypt_value

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
    is_enabled = models.BooleanField(default=True, verbose_name="启用")
    description = models.TextField(blank=True, verbose_name="描述")
    last_test_time = models.DateTimeField(null=True, blank=True, verbose_name="最后测试时间")
    last_test_result = models.CharField(
        max_length=20,
        choices=(
            ("success", "全部成功"),
            ("partial", "部分失败"),
            ("failed", "全部失败"),
            ("unknown", "未测试"),
        ),
        default="unknown",
        verbose_name="最后测试结果",
    )
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

    def save(self, *args, **kwargs):
        if self.password and not self._is_encrypted(self.password):
            self.password = encrypt_value(self.password)
        if self.private_key and not self._is_encrypted(self.private_key):
            self.private_key = encrypt_value(self.private_key)
        super().save(*args, **kwargs)

    def get_password(self):
        if not self.password:
            return ""
        if self._is_encrypted(self.password):
            return decrypt_value(self.password)
        return self.password

    def get_private_key(self):
        if not self.private_key:
            return ""
        if self._is_encrypted(self.private_key):
            return decrypt_value(self.private_key)
        return self.private_key

    @staticmethod
    def _is_encrypted(value):
        return value.startswith("gAAAAA")


class CredentialEnableTask(models.Model):
    STATUS_CHOICES = (
        ("pending", "待执行"),
        ("running", "执行中"),
        ("completed", "已完成"),
        ("failed", "失败"),
    )

    credential = models.ForeignKey(
        Credential, on_delete=models.CASCADE, related_name="enable_tasks", verbose_name="凭证"
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending", verbose_name="状态"
    )
    total_count = models.IntegerField(default=0, verbose_name="总节点数")
    completed_count = models.IntegerField(default=0, verbose_name="已完成数")
    success_count = models.IntegerField(default=0, verbose_name="成功数")
    failed_count = models.IntegerField(default=0, verbose_name="失败数")
    skipped_count = models.IntegerField(default=0, verbose_name="跳过数")
    task_center_id = models.BigIntegerField(null=True, blank=True, verbose_name="任务中心ID")
    message = models.TextField(blank=True, verbose_name="结果说明")
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="完成时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "凭证启用测试任务"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

