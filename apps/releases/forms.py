from django import forms
from .models import ReleaseTask


class ReleaseCreateForm(forms.ModelForm):
    class Meta:
        model = ReleaseTask
        fields = ["node", "config", "version"]
        widgets = {
            "node": forms.Select(attrs={"class": "form-select"}),
            "config": forms.Select(attrs={"class": "form-select"}),
            "version": forms.Select(attrs={"class": "form-select"}),
        }
        labels = {
            "node": "目标节点",
            "config": "目标配置",
            "version": "发布版本",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["node"].required = False
        self.fields["config"].required = False
        self.fields["version"].required = False
        self.fields["node"].queryset = (
            self.fields["node"]
            .queryset.filter(is_locked=False)
            .select_related("credential")
            .order_by("hostname")
        )
        self.fields["config"].queryset = (
            self.fields["config"].queryset.prefetch_related("nodes").order_by("name")
        )
        self.fields["version"].queryset = (
            self.fields["version"]
            .queryset.select_related("config")
            .order_by("-version")
        )
