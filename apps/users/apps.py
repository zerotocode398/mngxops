from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.users"
    verbose_name = "用户管理"

    def ready(self):
        """应用就绪后确保权限项与 perm_defs 同步"""
        try:
            self._ensure_all_permissions()
        except Exception:
            # migrate 未建表或启动早期失败时忽略，下次启动再补
            pass

    def _ensure_all_permissions(self):
        """按 all_permission_items 补齐 PermissionItem，保证权限矩阵可勾选"""
        from django.db.utils import OperationalError, ProgrammingError

        from .models import PermissionItem
        from .perm_defs import all_permission_items

        try:
            for item in all_permission_items():
                obj, created = PermissionItem.objects.get_or_create(
                    code=item["code"],
                    defaults={
                        "name": item["name"],
                        "resource": item["resource"],
                        "action": item["action"],
                    },
                )
                # 已存在时同步展示名，避免文案漂移
                if not created and obj.name != item["name"]:
                    obj.name = item["name"]
                    obj.save(update_fields=["name"])
        except (OperationalError, ProgrammingError):
            raise
