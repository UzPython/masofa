from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SiteRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('domain', models.CharField(max_length=255)),
                ('is_blocked', models.BooleanField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='site_rules',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=('user', 'domain', 'is_blocked'),
                        name='unique_user_site_rule',
                    ),
                ],
            },
        ),
    ]
