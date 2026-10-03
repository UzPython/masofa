from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('site_rules', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AgentCommandRoute',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('command_id', models.PositiveBigIntegerField(unique=True)),
                ('computer_name', models.CharField(max_length=100)),
                ('target_owner', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='routed_agent_commands',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'indexes': [
                    models.Index(
                        fields=['target_owner', 'computer_name'],
                        name='site_rules__target__408eb5_idx',
                    ),
                ],
            },
        ),
    ]
