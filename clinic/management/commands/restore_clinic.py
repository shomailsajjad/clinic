from django.core.management.base import BaseCommand, CommandError
from clinic.backup import restore_backup


class Command(BaseCommand):
    help = 'Restore a verified backup into a NEW separate SQLite file or PostgreSQL database.'

    def add_arguments(self, parser):
        parser.add_argument('backup')
        parser.add_argument('--target', required=True, help='New SQLite filename or pre-created separate PostgreSQL database name.')

    def handle(self, *args, **options):
        try:
            restore_backup(options['backup'], options['target'])
        except (OSError, RuntimeError) as error:
            raise CommandError(str(error)) from None
        self.stdout.write('Backup restored to the separate target. Verify records before switching the application database.')
