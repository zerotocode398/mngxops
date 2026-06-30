from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("credentials", "0003_credential_is_enabled"),
    ]

    operations = [
        migrations.CreateModel(
            name="CredentialEnableTask",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "待执行"),
                            ("running", "执行中"),
                            ("completed", "已完成"),
                            ("failed", "失败"),
                        ],
                        default="pending",
                        max_length=20,
                        verbose_name="状态",
                    ),
                ),
                ("total_count", models.IntegerField(default=0, verbose_name="总节点数")),
                ("completed_count", models.IntegerField(default=0, verbose_name="已完成数")),
                ("success_count", models.IntegerField(default=0, verbose_name="成功数")),
                ("failed_count", models.IntegerField(default=0, verbose_name="失败数")),
                ("skipped_count", models.IntegerField(default=0, verbose_name="跳过数")),
                ("task_center_id", models.BigIntegerField(blank=True, null=True, verbose_name="任务中心ID")),
                ("message", models.TextField(blank=True, verbose_name="结果说明")),
                (
                    "started_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="开始时间"),
                ),
                (
                    "finished_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="完成时间"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "credential",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="enable_tasks",
                        to="credentials.credential",
                        verbose_name="凭证",
                    ),
                ),
            ],
            options={
                "verbose_name": "凭证启用测试任务",
                "verbose_name_plural": "凭证启用测试任务",
                "ordering": ["-created_at"],
            },
        ),
    ]

