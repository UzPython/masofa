import random

from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    account_id = models.CharField(max_length=20, unique=True, db_index=True)
    region = models.CharField(max_length=100, blank=True, default='')
    district = models.CharField(max_length=100, blank=True, default='')
    is_discoverable = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} ({self.account_id})"

    @classmethod
    def create_for_user(cls, user):
        try:
            return user.profile
        except UserProfile.DoesNotExist:
            pass

        while True:
            account_id = str(random.randint(10**19, 10**20 - 1))
            if not cls.objects.filter(account_id=account_id).exists():
                return cls.objects.create(user=user, account_id=account_id)


class SharedAccount(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='outgoing_shares')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='incoming_shares')
    display_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('owner', 'recipient'), name='unique_shared_account_pair'),
        ]

    def __str__(self):
        return self.display_name


@receiver(post_save, sender=User)
def ensure_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.create_for_user(instance)


class Command(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    command_text = models.TextField()
    computer_name = models.CharField(max_length=100, blank=True, null=True)
    is_executed = models.BooleanField(default=False)
    output_result = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.command_text}"


class AllowedSite(models.Model):
    domain = models.CharField(max_length=255, unique=True, help_text="Masalan: youtube.com")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.domain


class BlockedSite(models.Model):
    domain = models.CharField(max_length=255, unique=True, help_text="Masalan: facebook.com")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.domain


class SiteWarning(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='site_warnings')
    computer_name = models.CharField(max_length=255, default="Noma'lum kompyuter", help_text="Foydalanuvchi nomi")
    url = models.TextField(help_text="Foydalanuvchi kirmoqchi bo'lgan to'liq havola")
    domain = models.CharField(max_length=255, help_text="Sayt domeni (masalan: instagram.com)")
    timestamp = models.DateTimeField(auto_now_add=True, help_text="Urinish vaqti")

    def __str__(self):
        return f"{self.computer_name} - {self.domain} ({self.timestamp})"


class Computer(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='owned_computers')
    shared_with = models.ManyToManyField(User, blank=True, related_name='shared_computers')
    name = models.CharField(max_length=100)
    ip_address = models.GenericIPAddressField(default="127.0.0.1")
    username = models.CharField(max_length=100, default='agent')
    password = models.CharField(max_length=128, default='agent123')
    is_online = models.BooleanField(default=True)
    last_seen = models.DateTimeField(auto_now=True)
    status_text = models.CharField(max_length=150, default="Online")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({'Online' if self.is_online else 'Offline'})"


class yangi_akaunt_ochis(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    password = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user}"