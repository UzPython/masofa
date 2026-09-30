from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('lamp', '0013_alter_allowedsite_domain_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='region',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='district',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='is_discoverable',
            field=models.BooleanField(default=False),
        ),
    ]