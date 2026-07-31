from django.http import JsonResponse
from django.shortcuts import redirect

from .perm_defs import permission_code, all_permission_items
from .models import UserProfile, UserGroup

# 无权访问统一文案
PERM_DENIED_TITLE = "无访问权限"
PERM_DENIED_MESSAGE = (
    "当前账号没有使用该功能的权限。请联系管理员分配相应权限后再试。"
)
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


def forbidden_response(request, message=None):
    """无权访问：AJAX 返回 JSON；页面请求回首页并由前端 showAlert 提示"""
    title, body = _normalize_forbidden_alert(message)
    if is_ajax_request(request):
        return JsonResponse({"success": False, "message": body}, status=403)

    request.session[SESSION_PERM_DENIED_KEY] = {"title": title, "message": body}
    return redirect("dashboard:index")


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
