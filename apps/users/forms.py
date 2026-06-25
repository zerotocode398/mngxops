from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import UserProfile, UserGroup, PermissionItem


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
    first_name = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        label="姓名",
    )
    remark = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        label="备注",
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
        fields = ("username", "email", "first_name", "password1", "password2")
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "password1": forms.PasswordInput(attrs={"class": "form-control"}),
            "password2": forms.PasswordInput(attrs={"class": "form-control"}),
        }
        labels = {
            "username": "用户名",
            "password1": "密码",
            "password2": "确认密码",
        }

    def clean_groups(self):
        groups = self.cleaned_data.get("groups", [])
        if len(groups) > 3:
            raise forms.ValidationError("用户最多只能关联 3 个角色")
        return groups

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data["first_name"]
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


class UserUpdateForm(forms.ModelForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
        label="邮箱",
    )
    first_name = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        label="姓名",
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
    is_active = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label="是否激活",
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
        fields = ("username", "email", "first_name", "is_active", "is_superuser")
        widgets = {
            "username": forms.TextInput(
                attrs={"class": "form-control", "readonly": True}
            ),
        }
        labels = {
            "username": "用户名",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and hasattr(self.instance, "profile"):
            self.fields["mobile"].initial = self.instance.profile.mobile
            self.fields["remark"].initial = self.instance.profile.remark
            self.fields["email"].initial = self.instance.email
            self.fields["first_name"].initial = self.instance.first_name
            self.fields["is_active"].initial = self.instance.is_active
            self.fields["is_superuser"].initial = self.instance.is_superuser
            self.fields["groups"].initial = self.instance.profile.groups.all()
            self.fields["direct_permissions"].initial = (
                self.instance.profile.direct_permissions.all()
            )

    def clean_groups(self):
        groups = self.cleaned_data.get("groups", [])
        if len(groups) > 3:
            raise forms.ValidationError("用户最多只能关联 3 个角色")
        return groups

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data["first_name"]
        user.is_active = self.cleaned_data["is_active"]
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
