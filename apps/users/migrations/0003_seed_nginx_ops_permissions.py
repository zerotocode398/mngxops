"""种子 Nginx 安装/启停/卸载权限项，并从 upgrade/nodes 拷贝既有授权。"""
from django.db import migrations


def _ensure_and_migrate(apps, schema_editor):
    """创建新 PermissionItem，并将旧权限授予拷贝到新码。"""
    PermissionItem = apps.get_model("users", "PermissionItem")
    UserGroup = apps.get_model("users", "UserGroup")
    UserProfile = apps.get_model("users", "UserProfile")

    from apps.users.perm_defs import all_permission_items

    code_to_item = {}
    for item in all_permission_items():
        obj, _ = PermissionItem.objects.get_or_create(
            code=item["code"],
            defaults={
                "name": item["name"],
                "resource": item["resource"],
                "action": item["action"],
            },
        )
        if obj.name != item["name"]:
            obj.name = item["name"]
            obj.save(update_fields=["name"])
        code_to_item[item["code"]] = obj

    # upgrade.read/create → nginx_install.read/create
    # nodes.read → nginx_service/uninstall.read
    # nodes.update → nginx_service/uninstall.create
    copy_map = {
        "upgrade.read": ["nginx_install.read"],
        "upgrade.create": ["nginx_install.create"],
        "nodes.read": ["nginx_service.read", "nginx_uninstall.read"],
        "nodes.update": ["nginx_service.create", "nginx_uninstall.create"],
    }

    for role in UserGroup.objects.prefetch_related("permissions").all():
        existing = set(role.permissions.values_list("code", flat=True))
        to_add = []
        for src, targets in copy_map.items():
            if src not in existing:
                continue
            for tgt in targets:
                if tgt not in existing and tgt in code_to_item:
                    to_add.append(code_to_item[tgt])
        if to_add:
            role.permissions.add(*to_add)

    through = UserProfile.direct_permissions.through
    for profile in UserProfile.objects.prefetch_related("direct_permissions").all():
        existing = set(profile.direct_permissions.values_list("code", flat=True))
        rows = []
        for src, targets in copy_map.items():
            if src not in existing:
                continue
            for tgt in targets:
                if tgt not in existing and tgt in code_to_item:
                    rows.append(
                        through(
                            userprofile_id=profile.id,
                            permissionitem_id=code_to_item[tgt].id,
                        )
                    )
        if rows:
            through.objects.bulk_create(rows, ignore_conflicts=True)


def _noop_reverse(apps, schema_editor):
    """回滚不删除权限项与授权，避免误伤手工勾选。"""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_remove_userprofile_mobile"),
    ]

    operations = [
        migrations.RunPython(_ensure_and_migrate, _noop_reverse),
    ]
