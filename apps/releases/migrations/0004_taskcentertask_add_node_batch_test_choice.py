from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("releases", "0003_taskcentertask"),
    ]

    operations = [
        migrations.AlterField(
            model_name="taskcentertask",
            name="operation_type",
            field=models.CharField(
                max_length=40,
                choices=[
                    ("release_publish", "发布配置"),
                    ("release_rollback", "回滚配置"),
                    ("credential_enable_test", "凭证启用测试"),
                    ("node_batch_test", "节点批量测试"),
                    ("other", "其他任务"),
                ],
                default="other",
                verbose_name="任务类型",
            ),
        ),
    ]

