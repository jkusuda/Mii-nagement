from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mii', '0010_island_map'),
    ]

    operations = [
        migrations.AddField(
            model_name='resident',
            name='activity',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='resident',
            name='appearance',
            field=models.JSONField(default=dict),
        ),
    ]
