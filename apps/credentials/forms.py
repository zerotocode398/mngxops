from django import forms
from .models import Credential


class CredentialForm(forms.ModelForm):
    class Meta:
        model = Credential
        fields = [
            "name",
            "username",
            "auth_type",
            "password",
            "private_key",
            "description",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "auth_type": forms.Select(attrs={"class": "form-select"}),
            "password": forms.PasswordInput(
                attrs={"class": "form-control", "placeholder": "请输入密码"}
            ),
            "private_key": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 10,
                    "placeholder": "请输入私钥内容",
                }
            ),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }
        labels = {
            "name": "凭证名称",
            "username": "SSH用户名",
            "auth_type": "认证方式",
            "password": "密码",
            "private_key": "私钥",
            "description": "描述",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password"].required = False
        self.fields["private_key"].required = False

        if self.instance and self.instance.pk:
            self.fields["password"].widget.attrs["placeholder"] = "留空则不修改密码"
            self.fields["private_key"].widget.attrs["placeholder"] = "留空则不修改私钥"

    def clean(self):
        cleaned_data = super().clean()
        auth_type = cleaned_data.get("auth_type")
        password = cleaned_data.get("password")
        private_key = cleaned_data.get("private_key")
        is_edit = self.instance and self.instance.pk is not None

        if is_edit and not password:
            cleaned_data["password"] = self.instance.password
        if is_edit and not private_key:
            cleaned_data["private_key"] = self.instance.private_key

        if auth_type == "password" and not cleaned_data.get("password"):
            raise forms.ValidationError("密码认证方式必须填写密码")
        if auth_type == "key" and not cleaned_data.get("private_key"):
            raise forms.ValidationError("密钥认证方式必须填写私钥")

        return cleaned_data
