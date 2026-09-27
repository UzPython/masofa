from django.contrib import admin
from .models import Command, AllowedSite, SiteWarning

admin.site.register(Command)
admin.site.register(AllowedSite)
admin.site.register(SiteWarning)