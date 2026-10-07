from getpass import getpass
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from clinic.models import User

class Command(BaseCommand):
    help = 'Create an organization administrator with no branch clinical login.'
    def add_arguments(self, parser): parser.add_argument('username')
    def handle(self, *args, **options):
        user = User(username=options['username'], role='owner', clinic=None)
        password = getpass('Password: ')
        if password != getpass('Confirm password: '): raise CommandError('Passwords differ.')
        try: validate_password(password, user)
        except ValidationError as error: raise CommandError('; '.join(error.messages)) from None
        user.set_password(password)
        user.full_clean()
        user.save()
        self.stdout.write('Organization administrator created.')
