from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("releases", "0002_releasetask_binding_releasetask_publish_version_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="taskcentertask",
            name="log_output",
            field=models.TextField(blank=True, verbose_name="实时执行日志"),
        ),
    ]
