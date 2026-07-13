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
            "auth_type": forms.RadioSelect(attrs={"class": "form-check-input"}),
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
        """校验认证方式与对应字段的必填逻辑，并验证私钥格式"""
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
        if auth_type == "key":
            key_value = cleaned_data.get("private_key", "")
            if not key_value:
                raise forms.ValidationError("密钥认证方式必须填写私钥")
            if not self._is_valid_private_key(key_value):
                raise forms.ValidationError("私钥格式无效，请提供合法的 RSA/DSA/ECDSA/Ed25519 格式私钥")

        return cleaned_data

    def _is_valid_private_key(self, key_str):
        """校验私钥是否为合法格式（RSA/DSA/ECDSA/Ed25519）"""
        from io import StringIO
        import paramiko

        key_types = [
            paramiko.RSAKey,
            paramiko.DSSKey,
            paramiko.ECDSAKey,
            paramiko.Ed25519Key,
        ]
        for key_type in key_types:
            try:
                key_type.from_private_key(StringIO(key_str))
                return True
            except Exception:
                continue
        return False
