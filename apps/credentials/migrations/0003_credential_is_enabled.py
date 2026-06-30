from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("credentials", "0002_alter_credential_name_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="credential",
            name="is_enabled",
            field=models.BooleanField(default=True, verbose_name="启用"),
        ),
    ]

