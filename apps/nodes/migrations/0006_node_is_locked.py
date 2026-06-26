from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0005_remove_nodegroup_credential_alter_node_credential"),
    ]

    operations = [
        migrations.AddField(
            model_name="node",
            name="is_locked",
            field=models.BooleanField(default=False, verbose_name="已锁定"),
        ),
    ]
