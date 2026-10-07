import json
import time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from clinic.models import Clinic
from clinic.sync import synchronize_once

class Command(BaseCommand):
    help = 'Run a branch sync once or continuously, retaining local work during connection failures.'
    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true')
        parser.add_argument('--interval', type=int, default=30)
    def handle(self, *args, **options):
        if settings.CLINIC_NODE_MODE != 'branch': raise CommandError('Configure this installation as a branch first.')
        clinic = Clinic.objects.get(code=settings.CLINIC_LOCAL_CODE)
        config_path = settings.RUNTIME_DIR / 'sync-config.json'
        config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
        while True:
            try:
                count = synchronize_once(clinic, config.get('url'), config.get('key'))
                self.stdout.write(f'Synchronized {count} pending records.')
            except Exception as error:
                # No request bodies, credentials, clinical payloads or raw network errors enter logs.
                if not options['loop']: raise CommandError('Synchronization incomplete; local work is retained. Check configuration and synchronization status.') from None
                self.stderr.write('Offline or sync conflict. Local work retained; retrying.')
            if not options['loop']: break
            time.sleep(max(5, min(options['interval'], 60)))
