import secrets
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from clinic.models import Clinic, SyncCredential
from clinic.sync import token_hash

class Command(BaseCommand):
    help = 'Issue or rotate a branch API key. Store its output file securely; never commit it.'
    def add_arguments(self, parser):
        parser.add_argument('clinic_code')
        parser.add_argument('--output', required=True)
    def handle(self, *args, **options):
        if settings.CLINIC_NODE_MODE != 'central': raise CommandError('Run this command on the central server only.')
        clinic = Clinic.objects.get(code=options['clinic_code'])
        output = Path(options['output'])
        if output.exists(): raise CommandError('Choose a new private output file; an existing file will not be overwritten.')
        token = secrets.token_urlsafe(48)
        with output.open('x', encoding='utf-8') as stream: stream.write(token)
        output.chmod(0o600)
        # Rotation preserves the authoritative server identity.
        SyncCredential.objects.update_or_create(clinic=clinic, defaults={'token_hash': token_hash(token), 'is_active': True})
        self.stdout.write('Synchronization key written to the private output file. Transfer securely to this branch only.')
