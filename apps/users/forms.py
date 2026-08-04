from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
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
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="角色",
        help_text="最多可选 3 个；不选则无功能权限",
    )
    teams = forms.ModelMultipleChoiceField(
        queryset=UserTeam.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="用户组",
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
        """校验角色数量上限"""
        groups = self.cleaned_data.get("groups") or []
        if len(groups) > 3:
            raise forms.ValidationError("用户最多只能关联 3 个角色")
        return groups

    def save(self, commit=True):
        """创建用户并写入角色、用户组与直授权限"""
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
            user.user_teams.set(self.cleaned_data.get("teams") or [])
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
    remark = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        label="备注",
    )
    password1 = forms.CharField(
        required=False,
        label="新密码",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "留空则不修改",
                "autocomplete": "new-password",
            }
        ),
        help_text="无法查看原密码；留空表示不修改",
    )
    password2 = forms.CharField(
        required=False,
        label="确认新密码",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "留空则不修改",
                "autocomplete": "new-password",
            }
        ),
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
    teams = forms.ModelMultipleChoiceField(
        queryset=UserTeam.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="用户组",
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
        """预填资料、角色、用户组与直授权限"""
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = USERNAME_HELP
        if self.instance and self.instance.pk:
            self.fields["teams"].initial = list(self.instance.user_teams.all())
        if self.instance and hasattr(self.instance, "profile"):
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
        """校验角色数量上限"""
        groups = self.cleaned_data.get("groups") or []
        if len(groups) > 3:
            raise forms.ValidationError("用户最多只能关联 3 个角色")
        return groups

    def clean(self):
        """可选重置密码：填任一则两者必填且一致，并校验强度"""
        cleaned = super().clean()
        p1 = (cleaned.get("password1") or "").strip()
        p2 = (cleaned.get("password2") or "").strip()
        cleaned["password1"] = p1
        cleaned["password2"] = p2
        if not p1 and not p2:
            return cleaned
        if not p1 or not p2:
            self.add_error("password1", "修改密码时请同时填写新密码与确认密码")
            self.add_error("password2", "修改密码时请同时填写新密码与确认密码")
            return cleaned
        if p1 != p2:
            self.add_error("password2", "两次输入的密码不一致")
            return cleaned
        try:
            validate_password(p1, self.instance)
        except DjangoValidationError as exc:
            self.add_error("password1", exc)
        return cleaned

    def save(self, commit=True):
        """更新用户并同步角色、用户组与直授权限"""
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.is_superuser = self.cleaned_data["is_superuser"]
        new_password = self.cleaned_data.get("password1") or ""
        if new_password:
            user.set_password(new_password)
        if commit:
            user.save()
            profile, created = UserProfile.objects.get_or_create(user=user)
            profile.remark = self.cleaned_data.get("remark", "")
            profile.save()
            profile.groups.set(self.cleaned_data.get("groups", []))
            profile.direct_permissions.set(
                self.cleaned_data.get("direct_permissions", [])
            )
            user.user_teams.set(self.cleaned_data.get("teams") or [])
        return user
