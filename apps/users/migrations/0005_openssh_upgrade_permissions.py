"""创建 OpenSSH 升级权限项（空授权，不迁移历史权限，需管理员显式授予）。"""
from django.db import migrations


def _ensure_items(apps, schema_editor):
    """仅创建 PermissionItem 行，不向任何角色/用户迁移授权。"""
    PermissionItem = apps.get_model("users", "PermissionItem")

    from apps.users.perm_defs import all_permission_items

    for item in all_permission_items():
        if item["resource"] != "openssh_upgrade":
            continue
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


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0004_userprofile_login_lock"),
    ]

    operations = [
        migrations.RunPython(_ensure_items, _noop_reverse),
    ]