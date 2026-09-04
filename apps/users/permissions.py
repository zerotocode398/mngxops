from urllib.parse import urlparse

from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from .perm_defs import permission_code, all_permission_items
from .models import UserProfile, UserGroup

# 无权访问统一文案
PERM_DENIED_TITLE = "无访问权限"
PERM_DENIED_MESSAGE = "当前账号没有使用该功能的权限。请联系管理员分配相应权限后再试。"
PERM_CONFIG_ERROR_TITLE = "权限配置错误"
PERM_CONFIG_ERROR_MESSAGE = "系统权限配置异常，请联系管理员检查后再试。"
SESSION_PERM_DENIED_KEY = "mngxops_perm_denied"


def is_ajax_request(request):
    """判断是否为 AJAX 请求"""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _normalize_forbidden_alert(message):
    """将无权文案规范为 (标题, 正文)"""
    text = (message or "").strip()
    # 兼容历史调用文案
    if not text or text == "当前账号无权限访问该功能":
        return PERM_DENIED_TITLE, PERM_DENIED_MESSAGE
    if text == "权限配置错误":
        return PERM_CONFIG_ERROR_TITLE, PERM_CONFIG_ERROR_MESSAGE
    return PERM_DENIED_TITLE, text


def _safe_same_origin_referer(request):
    """返回可回跳的同源 Referer；与当前无权 URL 相同则视为无效。"""
    referer = request.META.get("HTTP_REFERER") or ""
    if not referer:
        return ""
    if not url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return ""
    if urlparse(referer).path == request.path:
        return ""
    return referer


def forbidden_response(request, message=None):
    """无权访问：AJAX 返回 JSON；页面请求回跳来源或留在当前 URL 并 showAlert。"""
    title, body = _normalize_forbidden_alert(message)
    if is_ajax_request(request):
        return JsonResponse({"success": False, "message": body}, status=403)

    request.session[SESSION_PERM_DENIED_KEY] = {"title": title, "message": body}
    back_url = _safe_same_origin_referer(request)
    if back_url:
        return redirect(back_url)
    return render(request, "403.html", status=403)


def _get_user_role_ids(user):
    """获取用户的有效角色 ID 集合。

    优先级规则：
    1. 若用户个人有角色，使用个人角色，忽略用户组角色
    2. 若用户个人无角色，使用所属用户组关联的角色
    """
    profile, _ = UserProfile.objects.get_or_create(user=user)

    # 用户个人角色
    personal_role_ids = set(profile.groups.values_list("id", flat=True))
    if personal_role_ids:
        return personal_role_ids

    # 用户组关联的角色
    team_role_ids = set(
        UserGroup.objects.filter(teams__members=user).values_list("id", flat=True)
    )
    if team_role_ids:
        return team_role_ids

    return set()


def user_has_permission(user, resource, action):
    """判断用户是否拥有指定资源动作权限"""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    profile, _ = UserProfile.objects.get_or_create(user=user)
    code = permission_code(resource, action)

    # 直授权限优先
    if profile.direct_permissions.filter(code=code).exists():
        return True

    # 获取有效角色
    role_ids = _get_user_role_ids(user)
    if role_ids:
        return UserGroup.objects.filter(
            id__in=role_ids, permissions__code=code
        ).exists()

    return False


def task_center_limited_ops_for_user(user):
    """无 releases.read 时，按节点/运维权限汇总可见的本人任务类型。"""
    ops = []
    if user_has_permission(user, "nodes", "ssh_test"):
        ops.extend(["node_ssh_test", "node_batch_test"])
    if user_has_permission(user, "credentials", "enable"):
        ops.append("credential_enable_test")
    if user_has_permission(user, "configs", "sync"):
        ops.append("config_batch_sync")
    if user_has_permission(user, "nginx_service", "operate"):
        ops.append("nginx_service_control")
    if user_has_permission(user, "upgrade", "execute"):
        ops.append("nginx_install")
    if user_has_permission(user, "nginx_uninstall", "execute"):
        ops.append("nginx_uninstall")
    return ops


def user_can_access_limited_task_center(user):
    """是否可通过节点/运维权限访问任务中心（非 releases.read 路径）。"""
    if user_has_permission(user, "nodes", "ssh_test"):
        return True
    if user_has_permission(user, "credentials", "enable"):
        return True
    return bool(task_center_limited_ops_for_user(user))


class PermissionRequiredMixin:
    """视图级权限校验 Mixin"""

    permission_resource = None
    permission_action = None

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_superuser:
            return super().dispatch(request, *args, **kwargs)

        resource = getattr(self, "permission_resource", None)
        action = getattr(self, "permission_action", None)
        if not resource or not action:
            return forbidden_response(request, "权限配置错误")

        if not user_has_permission(request.user, resource, action):
            return forbidden_response(request, PERM_DENIED_MESSAGE)

        return super().dispatch(request, *args, **kwargs)
