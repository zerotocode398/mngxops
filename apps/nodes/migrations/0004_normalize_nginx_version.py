# 存量 Nginx 版本去掉 nginx/ 前缀，统一为纯数字

import re

from django.db import migrations


def _strip_nginx_prefix(value):
    """去掉 nginx/ 或 nginx- 前缀，返回纯版本号。"""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return re.sub(r"(?i)^nginx[/\\-]", "", text)


def normalize_nginx_versions(apps, schema_editor):
    """将节点与升级任务中带前缀的版本字段规范为纯数字。"""
    Node = apps.get_model("nodes", "Node")
    for node in Node.objects.exclude(nginx_version="").iterator():
        cleaned = _strip_nginx_prefix(node.nginx_version)
        if cleaned != node.nginx_version:
            node.nginx_version = cleaned
            node.save(update_fields=["nginx_version"])

    NginxUpgradeTask = apps.get_model("upgrade", "NginxUpgradeTask")
    for task in NginxUpgradeTask.objects.exclude(current_version="").iterator():
        cleaned = _strip_nginx_prefix(task.current_version)
        if cleaned != task.current_version:
            task.current_version = cleaned
            task.save(update_fields=["current_version"])


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0003_nginx_available"),
        ("upgrade", "0002_add_third_party_module_package"),
    ]

    operations = [
        migrations.RunPython(normalize_nginx_versions, migrations.RunPython.noop),
    ]
