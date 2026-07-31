from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .middleware import get_current_request, get_current_user
from .models import AuditLog

TRACKED_MODELS = {
    "apps.configs.models.Config": "配置管理",
    "apps.configs.models.ConfigNodeBinding": "配置绑定",
    "apps.configs.models.BindingVersion": "绑定版本",
    "apps.nodes.models.Node": "节点管理",
    "apps.nodes.models.NodeGroup": "节点分组",
    "apps.releases.models.ReleaseTask": "发布任务",
    "apps.releases.models.ReleaseHistory": "发布历史",
    "apps.users.models.User": "用户管理",
    "apps.users.models.UserGroup": "角色管理",
    "apps.users.models.UserTeam": "用户组管理",
    "apps.credentials.models.Credential": "凭证管理",
    "apps.settings.models.SystemSetting": "系统设置",
    "apps.upgrade.models.NginxSourcePackage": "Nginx 源码包",
    "apps.upgrade.models.NginxUpgradeTask": "Nginx 升级任务",
}


def _get_model_label(instance):
    for label, module_name in TRACKED_MODELS.items():
        if label.endswith(instance.__class__.__name__):
            return module_name
    return instance.__class__.__name__


def _get_client_ip():
    req = get_current_request()
    if req is None:
        return "0.0.0.0"
    xff = req.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return req.META.get("REMOTE_ADDR", "0.0.0.0")


def _truncate_remark(remark, max_len=40):
    """截断备注，避免审计 detail 过长。"""
    text = (remark or "").strip()
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _binding_identity(binding):
    """拼接绑定可读身份：配置名 @ 主机名。"""
    try:
        config_name = getattr(getattr(binding, "config", None), "name", None) or "?"
        hostname = getattr(getattr(binding, "node", None), "hostname", None) or "?"
        return f"{config_name} @ {hostname}"
    except Exception:
        return str(getattr(binding, "pk", "?"))


def _get_instance_label(instance):
    """生成审计 detail 中的对象标签（绑定/版本用业务身份，其余 name/username/pk）。"""
    class_name = instance.__class__.__name__

    if class_name == "ConfigNodeBinding":
        return _binding_identity(instance)

    if class_name == "BindingVersion":
        try:
            binding = getattr(instance, "binding", None)
            identity = _binding_identity(binding) if binding is not None else "?"
            version_no = getattr(instance, "version", None)
            label = f"{identity} · V{version_no}" if version_no is not None else identity
            remark = _truncate_remark(getattr(instance, "remark", ""))
            if remark:
                label = f"{label}（{remark}）"
            return label
        except Exception:
            return str(getattr(instance, "pk", "?"))

    name = getattr(instance, "name", None)
    if name:
        return name
    username = getattr(instance, "username", None)
    if username:
        return username
    return getattr(instance, "pk", str(instance))


@receiver(post_save)
def audit_post_save(sender, instance, created, **kwargs):
    sender_path = f"{sender.__module__}.{sender.__name__}"
    if sender_path not in TRACKED_MODELS:
        return

    module_name = TRACKED_MODELS[sender_path]
    user = get_current_user()
    if user is None:
        return

    ip = _get_client_ip()
    label = _get_instance_label(instance)

    if created:
        action = f"创建{module_name}"
        detail = f"新建 {module_name}「{label}」"
    else:
        action = f"更新{module_name}"
        detail = f"修改 {module_name}「{label}」"

    AuditLog.objects.create(
        user=user, module=module_name, action=action, ip=ip,
        result="success", detail=detail,
    )


@receiver(post_delete)
def audit_post_delete(sender, instance, **kwargs):
    sender_path = f"{sender.__module__}.{sender.__name__}"
    if sender_path not in TRACKED_MODELS:
        return

    module_name = TRACKED_MODELS[sender_path]
    user = get_current_user()
    if user is None:
        return

    ip = _get_client_ip()
    label = _get_instance_label(instance)

    AuditLog.objects.create(
        user=user, module=module_name, action=f"删除{module_name}",
        ip=ip, result="success", detail=f"删除 {module_name}「{label}」",
    )


def _connect_task_center_audit():
    """TaskCenterTask 创建时写入带超链字段的操作日志（不走 TRACKED_MODELS CRUD）。"""
    from apps.releases.models import TaskCenterTask
    from .utils import log_task_center_created

    def _on_task_center_created(sender, instance, created, **kwargs):
        if not created:
            return
        log_task_center_created(instance)

    post_save.connect(
        _on_task_center_created,
        sender=TaskCenterTask,
        dispatch_uid="audit_task_center_created",
    )


_connect_task_center_audit()
