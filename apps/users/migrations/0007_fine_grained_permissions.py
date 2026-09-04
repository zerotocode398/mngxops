"""创建细粒度权限项，并从旧权限自动迁移授权。"""

from django.db import migrations


def _ensure_and_migrate(apps, schema_editor):
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

    # 旧权限 → 新细粒度权限 映射表
    # 拥有旧权限的角色/用户，自动获得对应的新权限
    copy_map = {
        "nodes.update": ["nodes.ssh_test", "nodes.lock", "nodes.unlock"],
        "credentials.update": ["credentials.enable"],
        "configs.update": ["configs.sync"],
        "upgrade.create": ["upgrade.execute"],
        "nginx_install.create": ["nginx_install.execute"],
        "nginx_service.create": ["nginx_service.operate"],
        "nginx_uninstall.create": ["nginx_uninstall.execute"],
        "users.update": ["users.lock", "users.unlock"],
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
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0004_userprofile_login_lock"),
    ]

    operations = [
        migrations.RunPython(_ensure_and_migrate, _noop_reverse),
    ]
