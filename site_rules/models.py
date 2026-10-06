from django.conf import settings
from django.db import models


class SiteRule(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='site_rules')
    domain = models.CharField(max_length=255)
    is_blocked = models.BooleanField()
    is_silent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'domain', 'is_blocked'),
                name='unique_user_site_rule',
            ),
        ]

    def __str__(self):
        return self.domain


class AgentCommandRoute(models.Model):
    command_id = models.PositiveBigIntegerField(unique=True)
    target_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='routed_agent_commands',
    )
    computer_name = models.CharField(max_length=100)

    class Meta:
        indexes = [
            models.Index(
                fields=('target_owner', 'computer_name'),
                name='site_rules__target__408eb5_idx',
            ),
        ]
