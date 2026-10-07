import os

from django.conf import settings
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from clinic.models import User


class Command(BaseCommand):
    help = 'Initialize a testing-only admin using secure hosting environment settings.'

    def handle(self, *args, **options):
        if not settings.CLINIC_DEMO_MODE:
            raise CommandError('Demo initialization requires CLINIC_DEMO_MODE=1.')
        username = 'admin'
        if User.objects.filter(username=username, role=User.Role.ADMIN, is_active=True).exists():
            self.stdout.write('Existing demo admin retained.')
            return
        if User.objects.filter(username=username).exists():
            raise CommandError('The demo admin username is already used by another account.')
        password = os.environ.get('CLINIC_DEMO_ADMIN_PASSWORD', '')
        if not password:
            raise CommandError('Set CLINIC_DEMO_ADMIN_PASSWORD securely in the hosting dashboard.')
        try:
            password_validation.validate_password(password, User(username=username))
        except ValidationError:
            raise CommandError('Choose a stronger demo password: at least 12 characters with mixed letters, numbers and symbols.') from None
        if len(password) < 12:
            raise CommandError('Demo password must have at least 12 characters.')
        User.objects.create_superuser(username, password=password)
        self.stdout.write('Demo admin initialized. No patient records or service prices were seeded.')
