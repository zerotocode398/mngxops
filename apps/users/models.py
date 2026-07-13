from django.db import models
from django.contrib.auth.models import User
from .perm_defs import RESOURCE_CHOICES, ACTION_CHOICES


class PermissionItem(models.Model):
    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    code = models.CharField(max_length=100, unique=True, verbose_name="权限编码")
    name = models.CharField(max_length=100, verbose_name="权限名称")
    resource = models.CharField(max_length=50, choices=RESOURCE_CHOICES, verbose_name="资源")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, verbose_name="动作")

    class Meta:
        verbose_name = "权限项"
        verbose_name_plural = verbose_name
        ordering = ["resource", "action", "id"]

    def __str__(self):
        return self.name


class UserGroup(models.Model):
    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    name = models.CharField(max_length=100, unique=True, verbose_name="名称")
    description = models.TextField(blank=True, verbose_name="描述")
    permissions = models.ManyToManyField(
        PermissionItem,
        blank=True,
        verbose_name="角色权限",
        related_name="roles",
    )
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name="创建人"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "角色"
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name


class UserTeam(models.Model):
    """用户组 — 独立于角色的分组方式，可绑定一个或多个用户，也可关联角色"""
    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    name = models.CharField(max_length=100, unique=True, verbose_name="组名")
    description = models.TextField(blank=True, verbose_name="描述")
    members = models.ManyToManyField(
        User,
        blank=True,
        verbose_name="组成员",
        related_name="user_teams",
    )
    roles = models.ManyToManyField(
        UserGroup,
        blank=True,
        verbose_name="关联角色",
        related_name="teams",
    )
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name="创建人", related_name="created_teams"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "用户组"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def member_count(self):
        return self.members.count()


class UserProfile(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, verbose_name="用户", related_name="profile"
    )
    mobile = models.CharField(max_length=20, blank=True, verbose_name="手机号")
    avatar = models.ImageField(
        upload_to="avatar/", blank=True, null=True, verbose_name="头像"
    )
    groups = models.ManyToManyField(
        UserGroup, blank=True, verbose_name="角色", related_name="members"
    )
    direct_permissions = models.ManyToManyField(
        PermissionItem,
        blank=True,
        verbose_name="用户直授权限",
        related_name="users",
    )
    remark = models.TextField(blank=True, verbose_name="备注")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "用户资料"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.user.username}的资料"
