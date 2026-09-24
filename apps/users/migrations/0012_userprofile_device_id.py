from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0011_remove_userprofile_last_activity'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='device_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='浏览器 Cookie 生成的唯一设备 ID，用于精确检测多点登录',
                max_length=64,
                verbose_name='设备标识',
            ),
        ),
    ]
