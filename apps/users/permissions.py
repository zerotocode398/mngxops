from django.http import HttpResponseForbidden

from .perm_defs import permission_code, all_permission_items
from .models import UserProfile



def user_has_permission(user, resource, action):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    profile, _ = UserProfile.objects.get_or_create(user=user)
    code = permission_code(resource, action)

    if profile.direct_permissions.filter(code=code).exists():
        return True

    return profile.groups.filter(permissions__code=code).exists()


class PermissionRequiredMixin:
    permission_resource = None
    permission_action = None

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_superuser:
            return super().dispatch(request, *args, **kwargs)

        resource = getattr(self, "permission_resource", None)
        action = getattr(self, "permission_action", None)
        if not resource or not action:
            return HttpResponseForbidden("权限配置错误")

        if not user_has_permission(request.user, resource, action):
            return HttpResponseForbidden("当前账号无权限访问该功能")

        return super().dispatch(request, *args, **kwargs)

