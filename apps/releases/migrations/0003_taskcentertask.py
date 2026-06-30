from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("releases", "0002_releasetask_batch_number_alter_releasetask_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="TaskCenterTask",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "operation_type",
                    models.CharField(
                        choices=[
                            ("release_publish", "发布配置"),
                            ("release_rollback", "回滚配置"),
                            ("credential_enable_test", "凭证启用测试"),
                            ("other", "其他任务"),
                        ],
                        default="other",
                        max_length=40,
                        verbose_name="任务类型",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "等待中"),
                            ("running", "执行中"),
                            ("success", "成功"),
                            ("failed", "失败"),
                            ("cancelled", "已取消"),
                        ],
                        default="pending",
                        max_length=20,
                        verbose_name="状态",
                    ),
                ),
                ("title", models.CharField(max_length=200, verbose_name="任务标题")),
                ("detail", models.TextField(blank=True, verbose_name="任务说明")),
                ("result", models.TextField(blank=True, verbose_name="任务结果")),
                ("progress", models.IntegerField(default=0, verbose_name="进度")),
                ("source_batch", models.CharField(blank=True, max_length=64, verbose_name="来源批次")),
                ("started_at", models.DateTimeField(blank=True, null=True, verbose_name="开始时间")),
                ("finished_at", models.DateTimeField(blank=True, null=True, verbose_name="完成时间")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "trigger_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="触发人",
                    ),
                ),
            ],
            options={
                "verbose_name": "任务中心",
                "verbose_name_plural": "任务中心",
                "ordering": ["-created_at"],
            },
        ),
    ]

