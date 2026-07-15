from django import forms
from .models import ReleaseTask
from apps.configs.models import ConfigNodeBinding


class ReleaseCreateForm(forms.ModelForm):
    class Meta:
        model = ReleaseTask
        fields = ["node", "config", "binding", "version"]
        widgets = {
            "node": forms.Select(attrs={"class": "form-select"}),
            "config": forms.Select(attrs={"class": "form-select"}),
            "binding": forms.Select(attrs={"class": "form-select"}),
            "version": forms.Select(attrs={"class": "form-select"}),
        }
        labels = {
            "node": "目标节点",
            "config": "目标配置",
            "binding": "配置绑定",
            "version": "发布版本",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.nodes.models import Node
        from apps.configs.models import Config, BindingVersion

        self.fields["node"].required = False
        self.fields["config"].required = False
        self.fields["binding"].required = False
        self.fields["version"].required = False
        self.fields["node"].queryset = (
            Node.objects.filter(is_locked=False)
            .select_related("credential")
            .order_by("hostname")
        )
        self.fields["config"].queryset = Config.objects.order_by("name")
        self.fields["binding"].queryset = ConfigNodeBinding.objects.select_related(
            "config", "node"
        ).exclude(sync_status="marked_deleted").order_by("config__name", "node__hostname")
        self.fields["version"].queryset = BindingVersion.objects.select_related(
            "binding__config"
        ).order_by("-version")