from django.contrib.auth.models import User
from django.db import models

class Command(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    command_text = models.TextField()
    is_executed = models.BooleanField(default=False)
    output_result = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.command_text} - {'Bajarildi' if self.is_executed else 'Kutilyapti'}"


# 1. Ruxsat etilgan saytlar (Oq ro'yxat)
class AllowedSite(models.Model):
    domain = models.CharField(max_length=255, unique=True, help_text="Masalan: youtube.com")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.domain


# 2. Ruxsatsiz saytlarga kirish urinishlari (Ogohlantirishlar)
class SiteWarning(models.Model):
    computer_name = models.CharField(max_length=255, default="Noma'lum kompyuter", help_text="Kompyuter nomi yoki egasi")
    url = models.TextField(help_text="Foydalanuvchi kirmoqchi bo'lgan to'liq havola")
    domain = models.CharField(max_length=255, help_text="Sayt domeni (masalan: instagram.com)")
    timestamp = models.DateTimeField(auto_now_add=True, help_text="Urinish vaqti")

    def __str__(self):
        return f"{self.computer_name} - {self.domain} ({self.timestamp})"