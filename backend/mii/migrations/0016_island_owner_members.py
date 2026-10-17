from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mii", "0015_metrics"),
    ]

    operations = [
        migrations.AddField(
            model_name="island",
            name="owner",
            field=models.CharField(blank=True, db_index=True, max_length=200, null=True),
        ),
        migrations.AddField(
            model_name="island",
            name="members",
            field=models.JSONField(default=list),
        ),
    ]
