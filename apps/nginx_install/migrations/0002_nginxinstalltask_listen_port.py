# Generated manually for Q147 listen_port

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nginx_install", "0001_initial_nginx_install_task"),
    ]

    operations = [
        migrations.AddField(
            model_name="nginxinstalltask",
            name="listen_port",
            field=models.PositiveIntegerField(
                default=80,
                help_text="安装后写入主配置 listen 的端口",
                verbose_name="监听端口",
            ),
        ),
    ]
