from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("releases", "0004_taskcentertask_add_node_batch_test_choice"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="taskcentertask",
            name="title",
        ),
        migrations.AddField(
            model_name="taskcentertask",
            name="target_hostnames",
            field=models.TextField(blank=True, verbose_name="目标主机名"),
        ),
        migrations.AddField(
            model_name="taskcentertask",
            name="target_ips",
            field=models.TextField(blank=True, verbose_name="目标IP"),
        ),
    ]

