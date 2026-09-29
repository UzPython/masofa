from django.contrib import admin
from .models import Command, AllowedSite, SiteWarning, Computer

admin.site.register(Command)
admin.site.register(AllowedSite)
admin.site.register(SiteWarning)
admin.site.register(Computer)