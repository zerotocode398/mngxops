from django import forms
from .models import Config, ConfigNodeBinding


class ConfigForm(forms.ModelForm):
    """配置标签表单 - Config 现在只是标签"""

    class Meta:
        model = Config
        fields = ["name", "default_remote_path", "template_content", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "default_remote_path": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "如 /etc/nginx/conf.d/app.conf"}
            ),
            "template_content": forms.Textarea(
                attrs={
                    "class": "form-control font-monospace",
                    "rows": 8,
                    "spellcheck": "false",
                    "wrap": "off",
                    "placeholder": "可选：创建绑定时若远程无此文件，基于此模板生成初始内容",
                }
            ),
            "description": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": "可选描述"}
            ),
        }
        labels = {
            "name": "配置名称",
            "default_remote_path": "默认远程路径",
            "template_content": "内容模板（可选）",
            "description": "描述",
        }


class BindingForm(forms.ModelForm):
    """配置节点绑定表单"""

    remark = forms.CharField(
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        label="修改备注",
        help_text="简要说明本次修改内容",
    )

    class Meta:
        model = ConfigNodeBinding
        fields = ["config", "node", "remote_path", "content"]
        widgets = {
            "config": forms.Select(attrs={"class": "form-select"}),
            "node": forms.Select(attrs={"class": "form-select"}),
            "remote_path": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "如 /etc/nginx/nginx.conf"}
            ),
            "content": forms.Textarea(
                attrs={
                    "class": "form-control font-monospace",
                    "rows": 20,
                    "spellcheck": "false",
                    "wrap": "off",
                }
            ),
        }
        labels = {
            "config": "配置标签",
            "node": "目标节点",
            "remote_path": "远程文件路径",
            "content": "配置内容",
        }