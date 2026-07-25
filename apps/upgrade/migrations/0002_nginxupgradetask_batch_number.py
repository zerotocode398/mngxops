# Generated manually for Q64 multi-node upgrade batch

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("upgrade", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="nginxupgradetask",
            name="batch_number",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="多节点同批升级共用同一批次号",
                max_length=32,
                verbose_name="批次号",
            ),
        ),
    ]
