from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0010_userprofile_last_activity'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='userprofile',
            name='last_activity',
        ),
    ]
