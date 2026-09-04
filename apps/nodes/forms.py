from django import forms

from .models import Node, NodeGroup
from apps.credentials.models import Credential
from apps.users.permissions import user_has_permission
from utils.setting_service import get_setting


class NodeGroupForm(forms.ModelForm):
    class Meta:
        model = NodeGroup
        fields = ["name", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }
        labels = {
            "name": "节点组名称",
            "description": "描述",
        }


class CredentialChoiceField(forms.ModelChoiceField):
    """自定义凭证下拉字段，仅显示已启用凭证，并按认证方式分组（密码在前，密钥在后）"""

    def __init__(self, **kwargs):
        kwargs.setdefault("queryset", Credential.objects.filter(is_enabled=True))
        kwargs.setdefault("required", False)
        kwargs.setdefault("widget", forms.Select(attrs={"class": "form-select"}))
        super().__init__(**kwargs)

    def _get_choices(self):
        """重写choices属性，按密码认证/密钥认证分组返回选项，包含empty_label"""
        queryset = self._queryset
        if queryset is None:
            return None
        try:
            password_creds = queryset.filter(auth_type="password")
            key_creds = queryset.filter(auth_type="key")
            choices = []
            if self.empty_label is not None:
                choices.append(("", self.empty_label))
            if password_creds.exists():
                choices.append(("🔒 密码认证", list(self._make_items(password_creds))))
            if key_creds.exists():
                choices.append(("🔑 密钥认证", list(self._make_items(key_creds))))
            return choices
        except Exception:
            if self.empty_label is not None:
                return [("", self.empty_label)]
            return []

    def _make_items(self, queryset):
        """生成选项列表，格式为 (value, label)"""
        return [(obj.pk, str(obj)) for obj in queryset]

    @property
    def choices(self):
        return self._get_choices()

    @choices.setter
    def choices(self, value):
        pass


class NodeForm(forms.ModelForm):
    """节点表单，包含凭证分组下拉和环境Radio选择"""

    credential = CredentialChoiceField()

    class Meta:
        model = Node
        fields = [
            "hostname",
            "ip",
            "port",
            "credential",
            "groups",
            "environment",
            "nginx_path",
            "description",
        ]
        widgets = {
            "hostname": forms.TextInput(attrs={"class": "form-control"}),
            "ip": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "例如: 192.168.1.100"}
            ),
            "port": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "max": 65535}
            ),
            "groups": forms.CheckboxSelectMultiple(),
            "environment": forms.RadioSelect(attrs={"class": "form-check-input"}),
            "nginx_path": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "例如: /usr/local/nginx/sbin/nginx",
                }
            ),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }
        labels = {
            "hostname": "主机名",
            "ip": "IP地址",
            "port": "SSH端口",
            "credential": "SSH凭证",
            "groups": "节点组",
            "environment": "环境",
            "nginx_path": "Nginx路径",
            "description": "描述",
        }

    def __init__(self, *args, **kwargs):
        """初始化表单，根据用户权限设置节点组可选范围"""
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["credential"].required = False
        # 凭证字段已在 CredentialChoiceField 中过滤为仅已启用的凭证
        self.fields["credential"].empty_label = "不设置"
        if not self.instance.pk:
            try:
                self.fields["port"].initial = int(
                    get_setting("node.ssh_default_port", "22") or 22
                )
            except (TypeError, ValueError):
                self.fields["port"].initial = 22
            self.fields["nginx_path"].initial = (
                get_setting("config.default_nginx_bin", "/usr/sbin/nginx")
                or "/usr/sbin/nginx"
            )
        if user:
            if user.is_superuser or user_has_permission(user, "credentials", "read"):
                self.fields["credential"].queryset = Credential.objects.filter(
                    is_enabled=True
                )
            else:
                self.fields["credential"].queryset = Credential.objects.none()
            self.fields["groups"].queryset = NodeGroup.objects.all()

    def clean_groups(self):
        groups = self.cleaned_data.get("groups", [])
        if len(groups) > 3:
            raise forms.ValidationError("节点最多只能关联 3 个节点组")
        return groups

    def clean_ip(self):
        """校验 IP：活跃节点不可重复；已逻辑删除的同 IP 仅允许创建时恢复"""
        ip = self.cleaned_data.get("ip")
        if not ip:
            return ip
        active_qs = Node.objects.filter(ip=ip)
        if self.instance and self.instance.pk:
            active_qs = active_qs.exclude(pk=self.instance.pk)
        if active_qs.exists():
            raise forms.ValidationError("该 IP 地址已被其他节点使用")
        # 编辑时不可占用已逻辑删除节点的 IP（需通过新建同 IP 走恢复）
        if self.instance and self.instance.pk:
            if (
                Node.all_objects.filter(ip=ip, is_deleted=True)
                .exclude(pk=self.instance.pk)
                .exists()
            ):
                raise forms.ValidationError(
                    "该 IP 对应已删除节点，请新建同 IP 节点以恢复历史记录"
                )
        return ip
