from django.http import HttpResponseForbidden, JsonResponse

from .perm_defs import permission_code, all_permission_items
from .models import UserProfile, UserGroup


def is_ajax_request(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def forbidden_response(request, message):
    if is_ajax_request(request):
        return JsonResponse({"success": False, "message": message}, status=403)
    return HttpResponseForbidden(message)


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
            return forbidden_response(request, "当前账号无权限访问该功能")

        return super().dispatch(request, *args, **kwargs)
