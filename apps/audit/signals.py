from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .middleware import get_current_request, get_current_user
from .models import AuditLog

TRACKED_MODELS = {
    "apps.configs.models.Config": "配置管理",
    "apps.configs.models.ConfigVersion": "配置版本",
    "apps.nodes.models.Node": "节点管理",
    "apps.nodes.models.NodeGroup": "节点分组",
    "apps.releases.models.ReleaseTask": "发布任务",
    "apps.releases.models.ReleaseHistory": "发布历史",
    "apps.users.models.User": "用户管理",
    "apps.credentials.models.Credential": "凭证管理",
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


def _get_instance_label(instance):
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
        user=user,
        module=module_name,
        action=action,
        ip=ip,
        result="success",
        detail=detail,
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
        user=user,
        module=module_name,
        action=f"删除{module_name}",
        ip=ip,
        result="success",
        detail=f"删除 {module_name}「{label}」",
    )
