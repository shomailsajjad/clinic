from django.core.management.base import BaseCommand, CommandError
from clinic.backup import create_backup


class Command(BaseCommand):
    help = 'Create and verify a clinic database backup, optionally on an external drive.'

    def add_arguments(self, parser):
        parser.add_argument('--output', help='New output ZIP filename, including destination directory.')

    def handle(self, *args, **options):
        try:
            path = create_backup(options['output'])
        except (OSError, RuntimeError) as error:
            raise CommandError(str(error)) from None
        self.stdout.write(f'Verified backup saved: {path}')
