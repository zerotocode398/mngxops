from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("releases", "0005_taskcentertask_remove_title_add_targets"),
    ]

    operations = [
        migrations.AddField(
            model_name="taskcentertask",
            name="target_configs",
            field=models.TextField(blank=True, verbose_name="目标配置"),
        ),
    ]

