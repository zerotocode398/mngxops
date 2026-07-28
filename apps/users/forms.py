from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import UserProfile, UserGroup, UserTeam, PermissionItem

import re

# 登录用户名仅允许 ASCII 字母数字与 _-
USERNAME_PATTERN = re.compile(r"^[-a-zA-Z0-9_]+$")
USERNAME_HELP = "仅支持字母、数字、下划线与连字符"


def validate_ascii_username(username):
    """校验用户名是否为 ASCII 合法登录标识"""
    if not username or not USERNAME_PATTERN.fullmatch(username):
        raise forms.ValidationError(
            "用户名仅支持字母、数字、下划线与连字符"
        )
    return username


class UserGroupForm(forms.ModelForm):
    permissions = forms.ModelMultipleChoiceField(
        queryset=PermissionItem.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="角色权限",
    )

    class Meta:
        model = UserGroup
        fields = ["name", "description", "permissions"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }
        labels = {
            "name": "角色名称",
            "description": "描述",
            "permissions": "角色权限",
        }


class UserCreateForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
        label="邮箱",
    )
    remark = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "可选"}),
        label="备注",
    )
    groups = forms.ModelMultipleChoiceField(
        queryset=UserGroup.objects.all(),
        required=True,
        widget=forms.CheckboxSelectMultiple(),
        label="角色",
        help_text="须至少选择 1 个角色（最多 3 个），否则无法使用功能菜单",
        error_messages={"required": "请至少选择一个角色"},
    )
    direct_permissions = forms.ModelMultipleChoiceField(
        queryset=PermissionItem.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="用户直授权限",
        help_text="可选：会叠加在角色权限之上",
    )

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")
        widgets = {
            "username": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "如 ops_admin",
                }
            ),
        }
        labels = {
            "username": "用户名",
        }
        help_texts = {
            "username": USERNAME_HELP,
        }

    def __init__(self, *args, **kwargs):
        """统一密码框样式与中文标签"""
        super().__init__(*args, **kwargs)
        self.fields["password1"].label = "密码"
        self.fields["password2"].label = "确认密码"
        self.fields["password1"].widget.attrs.update({"class": "form-control"})
        self.fields["password2"].widget.attrs.update({"class": "form-control"})
        self.fields["username"].help_text = USERNAME_HELP

    def clean_username(self):
        """限制用户名为 ASCII 登录标识，并保留唯一性校验"""
        username = self.cleaned_data.get("username", "")
        validate_ascii_username(username)
        return super().clean_username()

    def clean_groups(self):
        """创建用户须至少关联一个角色"""
        groups = self.cleaned_data.get("groups") or []
        if not groups:
            raise forms.ValidationError("请至少选择一个角色")
        if len(groups) > 3:
            raise forms.ValidationError("用户最多只能关联 3 个角色")
        return groups

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            profile = UserProfile.objects.create(
                user=user,
                remark=self.cleaned_data.get("remark", ""),
            )
            profile.groups.set(self.cleaned_data.get("groups", []))
            profile.direct_permissions.set(
                self.cleaned_data.get("direct_permissions", [])
            )
        return user


class UserTeamForm(forms.ModelForm):
    roles = forms.ModelMultipleChoiceField(
        queryset=UserGroup.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="关联角色",
        help_text="用户组成员若无个人角色配置，将使用组关联的角色权限",
    )

    class Meta:
        model = UserTeam
        fields = ["name", "description", "roles"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }
        labels = {
            "name": "组名",
            "description": "描述",
            "roles": "关联角色",
        }


class UserUpdateForm(forms.ModelForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
        label="邮箱",
    )
    mobile = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        label="手机号",
    )
    remark = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        label="备注",
    )
    is_superuser = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label="是否超级管理员",
    )
    groups = forms.ModelMultipleChoiceField(
        queryset=UserGroup.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="角色",
        help_text="最多可选 3 个",
    )
    direct_permissions = forms.ModelMultipleChoiceField(
        queryset=PermissionItem.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="用户直授权限",
        help_text="可选：会叠加在角色权限之上",
    )

    class Meta:
        model = User
        fields = ("username", "email", "is_superuser")
        widgets = {
            "username": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "如 ops_admin"}
            ),
        }
        labels = {
            "username": "用户名",
        }
        help_texts = {
            "username": USERNAME_HELP,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = USERNAME_HELP
        if self.instance and hasattr(self.instance, "profile"):
            self.fields["mobile"].initial = self.instance.profile.mobile
            self.fields["remark"].initial = self.instance.profile.remark
            self.fields["email"].initial = self.instance.email
            self.fields["is_superuser"].initial = self.instance.is_superuser
            self.fields["groups"].initial = self.instance.profile.groups.all()
            self.fields["direct_permissions"].initial = (
                self.instance.profile.direct_permissions.all()
            )

    def clean_username(self):
        """限制用户名为 ASCII 登录标识（便于修正历史中文用户名）"""
        username = self.cleaned_data.get("username", "")
        return validate_ascii_username(username)

    def clean_groups(self):
        groups = self.cleaned_data.get("groups", [])
        if len(groups) > 3:
            raise forms.ValidationError("用户最多只能关联 3 个角色")
        return groups

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.is_superuser = self.cleaned_data["is_superuser"]
        if commit:
            user.save()
            profile, created = UserProfile.objects.get_or_create(user=user)
            profile.mobile = self.cleaned_data.get("mobile", "")
            profile.remark = self.cleaned_data.get("remark", "")
            profile.save()
            profile.groups.set(self.cleaned_data.get("groups", []))
            profile.direct_permissions.set(
                self.cleaned_data.get("direct_permissions", [])
            )
        return user
