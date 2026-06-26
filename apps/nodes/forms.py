from django import forms
from .models import Node, NodeGroup
from apps.credentials.models import Credential


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


class NodeForm(forms.ModelForm):
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
            "credential": forms.Select(attrs={"class": "form-select"}),
            "groups": forms.CheckboxSelectMultiple(),
            "environment": forms.Select(attrs={"class": "form-select"}),
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
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["credential"].required = False
        self.fields["credential"].empty_label = "不设置"
        if user and user.is_superuser:
            self.fields["credential"].queryset = Credential.objects.all()
            self.fields["groups"].queryset = NodeGroup.objects.all()
        else:
            self.fields["credential"].queryset = Credential.objects.none()
            self.fields["groups"].queryset = NodeGroup.objects.none()

    def clean_groups(self):
        groups = self.cleaned_data.get("groups", [])
        if len(groups) > 3:
            raise forms.ValidationError("节点最多只能关联 3 个节点组")
        return groups
