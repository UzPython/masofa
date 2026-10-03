from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = 'Create default admin account with predictable credentials for the dashboard.'

    def handle(self, *args, **options):
        User = get_user_model()
        username = 'admin'
        password = 'Admin123!'

        user, created = User.objects.get_or_create(username=username)
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f'Admin account created: {username} / {password}'))
        else:
            self.stdout.write(self.style.WARNING(f'Admin account already exists: {username}'))

# +998 91 153 33 34