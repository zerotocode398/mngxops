from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0009_userprofile_session_tracking'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='last_activity',
            field=models.DateTimeField(
                blank=True,
                help_text='最近一次请求时间，用于判断旧会话是否仍在使用',
                null=True,
                verbose_name='最近活动时间',
            ),
        ),
    ]
