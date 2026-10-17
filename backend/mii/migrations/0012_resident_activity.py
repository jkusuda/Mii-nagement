from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mii', '0011_resident_appearance'),
    ]

    operations = [
        migrations.AddField(
            model_name='resident',
            name='activity',
            field=models.TextField(blank=True, default=''),
        ),
    ]
