# Generated manually for Q70

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0002_loginlog_fail_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditlog",
            name="source_batch",
            field=models.CharField(
                blank=True, db_index=True, max_length=64, verbose_name="来源批次",
            ),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="task_center_id",
            field=models.IntegerField(
                blank=True, db_index=True, null=True, verbose_name="任务中心ID",
            ),
        ),
    ]
