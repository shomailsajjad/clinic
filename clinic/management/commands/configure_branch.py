import json
import ipaddress
import uuid
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from clinic.models import Organization, Clinic, User, Patient, Booking

class Command(BaseCommand):
    help = 'Bind a fresh installation to its central organization and clinic configuration.'
    def add_arguments(self, parser):
        parser.add_argument('configuration')
        parser.add_argument('--lan-ip', help='Static private IPv4 address assigned to this Windows server.')
    def handle(self, *args, **options):
        if User.objects.exists() or Patient.objects.exists() or Booking.objects.exists():
            raise CommandError('Configure a fresh database before creating users or patient records. Existing data requires a migration review.')
        lan_ip = options['lan_ip']
        if lan_ip:
            try:
                address = ipaddress.IPv4Address(lan_ip)
                if not address.is_private or address.is_loopback: raise ValueError
            except ValueError: raise CommandError('Choose the server’s private LAN IPv4 address.') from None
        data = json.loads(Path(options['configuration']).read_text(encoding='utf-8'))
        org, clinic = data['organization'], data['clinic']
        with transaction.atomic():
            organization = Organization.objects.get(pk=1)
            organization.global_id, organization.name = uuid.UUID(org['global_id']), org['name']
            organization.save()
            local = Clinic.objects.get(pk=1)
            local.global_id = uuid.UUID(clinic['global_id'])
            for key in ['name', 'code', 'address', 'phone', 'doctor_name', 'qualifications', 'prescription_footer']:
                setattr(local, key, clinic[key])
            local.save()
        node = settings.RUNTIME_DIR / 'node.json'
        config = {'mode': 'branch', 'clinic_code': local.code}
        if lan_ip: config.update({'bind_host':'0.0.0.0', 'allowed_hosts':['localhost','127.0.0.1',lan_ip]})
        node.write_text(json.dumps(config), encoding='utf-8')
        node.chmod(0o600)
        self.stdout.write('Branch configured. Restart the server, then create its local admin and staff accounts.')
