from django import template

from apps.users.permissions import user_has_permission

register = template.Library()


@register.filter(name="has_perm_code")
def has_perm_code(user, value):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True

    if not value or "." not in value:
        return False

    resource, action = value.split(".", 1)
    return user_has_permission(user, resource, action)

