"""Nginx 升级模块 - 表单"""
import json
from django import forms
from .models import NginxSourcePackage, NginxUpgradeTask


class NginxSourcePackageForm(forms.ModelForm):
    """源码包上传表单"""

    overwrite = forms.BooleanField(required=False, initial=False)

    class Meta:
        model = NginxSourcePackage
        fields = ["name", "version", "package_file", "description", "is_official"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "如：官方标准包 1.26.1"}),
            "version": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "如：1.26.1（自动从文件名提取）"}
            ),
            "package_file": forms.FileInput(attrs={
                "class": "form-control",
                "accept": ".tar.gz,.tgz",
            }),
            "description": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": "如：从 nginx.org 下载的官方稳定版"}
            ),
        }
        labels = {
            "name": "包名称",
            "version": "版本号",
            "package_file": "源码包文件",
            "description": "描述",
            "is_official": "标记为官方包",
        }
        help_texts = {
            "package_file": "支持 .tar.gz / .tgz 格式，大小限制见系统设置",
        }

    def __init__(self, *args, user=None, **kwargs):
        """接收当前用户，用于版本唯一性校验"""
        self.user = user
        super().__init__(*args, **kwargs)
        from utils.setting_service import get_setting
        try:
            max_mb = max(1, int(get_setting("upgrade.package_max_size_mb", "500") or 500))
        except (TypeError, ValueError):
            max_mb = 500
        self.fields["package_file"].help_text = f"支持 .tar.gz / .tgz 格式，最大 {max_mb}MB"

    def clean_package_file(self):
        """校验源码包格式与大小"""
        package_file = self.cleaned_data.get("package_file")
        if package_file:
            if not package_file.name.endswith((".tar.gz", ".tgz")):
                raise forms.ValidationError("仅支持 .tar.gz / .tgz 格式的文件")
            # 按系统设置限制源码包大小
            from utils.setting_service import get_setting
            try:
                max_mb = max(1, int(get_setting("upgrade.package_max_size_mb", "500") or 500))
            except (TypeError, ValueError):
                max_mb = 500
            if package_file.size > max_mb * 1024 * 1024:
                raise forms.ValidationError(f"文件大小不能超过 {max_mb}MB")
        return package_file

    def clean_version(self):
        """规范化版本号，必要时从文件名提取"""
        version = self.cleaned_data.get("version", "").strip()
        if not version:
            # 尝试从文件名提取版本号
            package_file = self.cleaned_data.get("package_file")
            if package_file:
                import re
                match = re.search(r"nginx-(\d+\.\d+\.\d+)", package_file.name)
                if match:
                    return match.group(1)
            raise forms.ValidationError("请输入版本号或选择具有标准命名的源码包文件")
        return version

    def clean(self):
        """同用户同版本未覆盖时拒绝，供前端弹出确认"""
        cleaned = super().clean()
        version = cleaned.get("version")
        overwrite = cleaned.get("overwrite")
        if version and self.user and not overwrite:
            exists = NginxSourcePackage.objects.filter(
                version=version,
                uploaded_by=self.user,
            ).exists()
            if exists:
                raise forms.ValidationError(
                    f"版本 {version} 已存在，是否覆盖？",
                    code="version_exists",
                )
        return cleaned


class NginxUpgradeTaskForm(forms.ModelForm):
    """升级任务创建表单 - 继承编译参数 + 增量调整"""

    added_modules_json = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"style": "display:none;", "id": "id_added_modules_json"}),
    )
    removed_modules_json = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"style": "display:none;", "id": "id_removed_modules_json"}),
    )
    added_third_party_json = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"style": "display:none;", "id": "id_added_third_party_json"}),
    )

    class Meta:
        model = NginxUpgradeTask
        fields = [
            "node", "source_package", "upgrade_mode",
            "current_version", "target_version", "target_prefix", "target_configure_opts",
            "remote_work_dir", "make_jobs",
        ]
        widgets = {
            "node": forms.Select(attrs={"class": "form-select"}),
            "source_package": forms.Select(attrs={"class": "form-select"}),
            "upgrade_mode": forms.Select(attrs={"class": "form-select"}),
            "current_version": forms.HiddenInput(),
            "target_version": forms.TextInput(attrs={"class": "form-control", "readonly": "readonly"}),
            "target_prefix": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "如 /usr/local/nginx"}
            ),
            "target_configure_opts": forms.Textarea(
                attrs={
                    "class": "form-control font-monospace",
                    "rows": 10,
                    "readonly": "readonly",
                    "spellcheck": "false",
                }
            ),
            "remote_work_dir": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "/tmp/nginx-upgrade"}
            ),
            "make_jobs": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 32}),
        }
        labels = {
            "node": "目标节点",
            "source_package": "源码包",
            "upgrade_mode": "升级模式",
            "current_version": "当前版本",
            "target_version": "目标版本",
            "target_prefix": "目标 --prefix",
            "target_configure_opts": "调整后的编译参数",
            "remote_work_dir": "远程编译工作目录",
            "make_jobs": "并行编译数 (-j)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.nodes.models import Node
        self.fields["node"].queryset = (
            Node.objects.filter(is_locked=False, status="online")
            .order_by("hostname")
        )
        self.fields["source_package"].queryset = (
            NginxSourcePackage.objects.order_by("-created_at")
        )
        # 当前版本由前端 nginx -V 提交，允许为空（后台再拉取覆盖）
        self.fields["current_version"].required = False

    def clean(self):
        cleaned_data = super().clean()
        # 将隐藏字段解析为 JSON
        added_json = cleaned_data.get("added_modules_json", "[]")
        removed_json = cleaned_data.get("removed_modules_json", "[]")
        third_party_json = cleaned_data.get("added_third_party_json", "[]")
        try:
            cleaned_data["added_modules"] = json.dumps(json.loads(added_json or "[]"))
            cleaned_data["removed_modules"] = json.dumps(json.loads(removed_json or "[]"))
            cleaned_data["added_third_party"] = json.dumps(json.loads(third_party_json or "[]"))
        except json.JSONDecodeError:
            raise forms.ValidationError("模块参数 JSON 格式不正确")
        return cleaned_data