# Generated manually for nginx_available dual-dimension status

from django.db import migrations, models


def backfill_nginx_available(apps, schema_editor):
    """有版本号的历史节点视为已检测到 Nginx；无版本保持未探测。"""
    Node = apps.get_model("nodes", "Node")
    Node.objects.exclude(nginx_version="").filter(nginx_version__isnull=False).update(
        nginx_available=True
    )


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0002_add_last_probe_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="node",
            name="nginx_available",
            field=models.BooleanField(
                blank=True,
                help_text="null=未探测，True=已检测到，False=确认不可用；与 SSH status 独立",
                null=True,
                verbose_name="Nginx可用",
            ),
        ),
        migrations.AddField(
            model_name="node",
            name="last_nginx_probe_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="上次Nginx探测时间"
            ),
        ),
        migrations.RunPython(backfill_nginx_available, migrations.RunPython.noop),
    ]
