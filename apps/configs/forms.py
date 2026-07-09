from django import forms
from .models import Config
from apps.nodes.models import Node


class ConfigForm(forms.ModelForm):
    remark = forms.CharField(
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        label="修改备注",
        help_text="简要说明本次修改内容",
    )

    class Meta:
        model = Config
        fields = ["name", "file_path", "content"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "file_path": forms.TextInput(attrs={"class": "form-control"}),
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
            "name": "配置名称",
            "file_path": "配置文件路径",
            "content": "配置内容",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].required = False
        self.fields["file_path"].required = False
        self.fields["content"].required = False
