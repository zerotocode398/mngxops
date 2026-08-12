from django.db import migrations, models


class Migration(migrations.Migration):
    """增加卸载状态：包管理器卸载"""

    dependencies = [
        ("nginx_uninstall", "0002_nginxuninstalltask_removing_unit"),
    ]

    operations = [
        migrations.AlterField(
            model_name="nginxuninstalltask",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "等待执行"),
                    ("stopping", "停止服务"),
                    ("removing_package", "包管理器卸载"),
                    ("removing_prefix", "删除安装目录"),
                    ("removing_backup", "清理发布备份"),
                    ("removing_extra", "清理额外目录"),
                    ("removing_unit", "清理 systemd"),
                    ("updating_node", "更新节点状态"),
                    ("success", "卸载成功"),
                    ("failed", "卸载失败"),
                    ("cancelled", "已取消"),
                ],
                default="pending",
                max_length=20,
                verbose_name="状态",
            ),
        ),
    ]
