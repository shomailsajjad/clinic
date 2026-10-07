"""Exercise two independent branch databases and a real central HTTP server.
Run with the app's Python. Uses invented records and temporary installations.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keep-temporary', action='store_true', help='Retain invented test databases for browser review.')
    args = parser.parse_args()
    folder = Path(tempfile.mkdtemp(prefix='clinic-branches-'))
    nodes = {name: folder / name for name in ['central', 'branch-a', 'branch-b']}
    processes = []
    ports = {}
    def port():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); return sock.getsockname()[1]
    for name, node in nodes.items():
        shutil.copytree(ROOT, node, ignore=shutil.ignore_patterns('.git','.venv','.runtime','staticfiles','__pycache__','docs'))
        ports[name] = port()
    def env(name):
        return {**os.environ, 'CLINIC_NODE_MODE': 'central' if name == 'central' else 'branch',
            'CLINIC_LOCAL_CODE': 'SPC' if name == 'branch-a' else 'SC', 'CLINIC_PORT':str(ports[name]),
            'CLINIC_DB_ENGINE':'sqlite', 'CLINIC_BIND_HOST':'127.0.0.1', 'CLINIC_DEBUG':'0', 'CLINIC_ALLOWED_HOSTS':'127.0.0.1,localhost,testserver'}
    def command(name, *parts, expected=0):
        result = subprocess.run([PYTHON, *parts], cwd=nodes[name], env=env(name), capture_output=True, text=True)
        if result.returncode != expected:
            raise RuntimeError(f'{name} operation failed:\n{result.stdout}\n{result.stderr}')
        return result
    def code(name, body):
        return command(name, '-c', "import os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');import django;django.setup();" + body)
    def start(name):
        log = (folder / (name + '.log')).open('a')
        proc = subprocess.Popen([PYTHON,'serve.py'],cwd=nodes[name],env=env(name),stdout=log,stderr=log)
        log.close();processes.append(proc)
        for attempt in range(100):
            if proc.poll() is not None: raise RuntimeError(f'{name} server stopped: ' + (folder / (name + '.log')).read_text())
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{ports[name]}/login/',timeout=1) as response:
                    if response.status == 200: return proc
            except OSError: time.sleep(.1)
        raise RuntimeError('Server readiness timed out.')
    try:
        for name in nodes: command(name,'manage.py','migrate','--noinput')
        code('central', """
from clinic.models import Clinic,User
from django.conf import settings
import json,secrets
org=Clinic.objects.get(pk=1).organization
b=Clinic.objects.create(organization=org,name='Sajjad Family Clinic — Test Branch',code='SC',doctor_name='Dr. Test Doctor',qualifications='MBBS',address='Example branch address',phone='03001234567')
owner=User.objects.create_user('organization-admin',password=secrets.token_urlsafe(24),role='owner',clinic=None)
(settings.RUNTIME_DIR/'browser-test.json').write_text(json.dumps({'username':owner.username,'password':secrets.token_urlsafe(24)}))
credentials=json.loads((settings.RUNTIME_DIR/'browser-test.json').read_text());owner.set_password(credentials['password']);owner.save()
for clinic in Clinic.objects.all():
 data={'organization':{'global_id':str(org.global_id),'name':org.name},'clinic':{'global_id':str(clinic.global_id),**clinic.letterhead()}}
 (settings.BASE_DIR.parent/(clinic.code+'-configuration.json')).write_text(json.dumps(data))
""")
        for name, clinic_code in [('branch-a','SPC'),('branch-b','SC')]:
            command(name,'manage.py','configure_branch',str(folder/(clinic_code+'-configuration.json')))
            key=folder/(clinic_code+'-key.private')
            command('central','manage.py','issue_sync_key',clinic_code,'--output',str(key))
            config=nodes[name]/'.runtime/sync-config.json'
            config.write_text(json.dumps({'url':f'http://127.0.0.1:{ports["central"]}','key':key.read_text()}));config.chmod(0o600)
            code(name,"""
from clinic.models import User,Clinic,Service,Diagnosis
from django.conf import settings
import secrets,json
clinic=Clinic.objects.get(code=settings.CLINIC_LOCAL_CODE)
admin=User.objects.create_user('branch-admin',password=secrets.token_urlsafe(24),role='admin',clinic=clinic)
password=secrets.token_urlsafe(24)
doctor=User.objects.create_user('branch-doctor',password=password,role='doctor',clinic=clinic)
(settings.RUNTIME_DIR/'browser-test.json').write_text(json.dumps({'username':doctor.username,'password':password}))
Service.objects.create(clinic=clinic,name='Doctor Consultation',category='opd',token_prefix='OPD',price=1000)
Diagnosis.objects.create(name='Example structured diagnosis')
""")
        code('branch-a',"""
from clinic.models import *
from clinic import workflows
from django.utils import timezone
import uuid
admin=User.objects.get(username='branch-admin');doctor=User.objects.get(username='branch-doctor')
p=Patient.objects.create(name='Ayesha — Demo Patient',age=35,gender='female',phone='03001234567',cnic='1234512345671',clinical_history='Origin branch clinical history',created_by=admin)
b=workflows.create_booking(admin,p,list(Service.objects.all()),'walk_in',timezone.localdate(),None,uuid.uuid4())
workflows.collect_payment(admin,b.pk,1000,'cash','',uuid.uuid4())
data={'base_version':0,'chief_complaint':'Example complaint','history':'Example medical history','examination':'Initial examination — demo only','blood_pressure':'120/80','pulse':70,'temperature':None,'weight':None,'diagnoses':list(Diagnosis.objects.all()),'prescription':[{'medicine':'Example prescribed medicine','dose':'Doctor supplied dose','route':'oral','frequency':'Doctor supplied frequency','duration':'Doctor supplied duration','instructions':'Demo only'}],'advice':'Follow the doctor’s written advice.','follow_up_date':None,'finalized':True,'revision_reason':''}
workflows.save_opd(doctor,b.pk,data)
""")
        central=start('central')
        command('branch-a','manage.py','sync_clinic')
        command('branch-b','manage.py','sync_clinic')
        code('branch-b',"""
from clinic.models import *
from clinic import workflows
from django.test import Client
from django.urls import reverse
from django.utils import timezone
import uuid
remote=RemoteRecord.objects.get(entity_type='patient');admin=User.objects.get(username='branch-admin');doctor=User.objects.get(username='branch-doctor')
c=Client();c.force_login(admin)
assert c.post(reverse('activate_patient',args=[remote.global_id])).status_code==302
p=Patient.objects.get(global_id=remote.global_id)
assert not SyncOutbox.objects.filter(entity_type='patient',entity_id=p.pk).exists()
assert b'Origin branch clinical history' not in c.get(reverse('patient_detail',args=[p.pk])).content
assert c.get(reverse('shared_history',args=[p.pk])).status_code==403
c.force_login(doctor)
assert b'Initial examination' in c.get(reverse('shared_history',args=[p.pk])).content
b=workflows.create_booking(admin,p,list(Service.objects.all()),'walk_in',timezone.localdate(),None,uuid.uuid4())
workflows.collect_payment(admin,b.pk,1000,'bank','TEST-ONLINE-001',uuid.uuid4())
""")
        command('branch-b','manage.py','sync_clinic')
        central.terminate();central.wait(timeout=10)
        code('branch-a',"""
from clinic.models import *
from clinic import workflows
from django.utils import timezone
import uuid
admin=User.objects.get(username='branch-admin');doctor=User.objects.get(username='branch-doctor');b=Booking.objects.first()
workflows.refund_payment(admin,b.transactions.get(kind='collection').pk,300,'cash','','Example partial refund',uuid.uuid4())
previous=b.opd_versions.first()
data={'base_version':1,'chief_complaint':previous.chief_complaint,'history':previous.history,'examination':'Revised examination after outage — demo only','blood_pressure':'120/80','pulse':70,'temperature':None,'weight':None,'diagnoses':list(Diagnosis.objects.all()),'prescription':previous.medicines,'advice':previous.advice,'follow_up_date':None,'finalized':True,'revision_reason':'Example corrected finding'}
workflows.save_opd(doctor,b.pk,data)
new=workflows.create_booking(admin,b.patient,list(Service.objects.all()),'walk_in',timezone.localdate(),None,uuid.uuid4())
assert new.items.first().tokens.first().number==2
""")
        command('branch-a','manage.py','sync_clinic',expected=1)
        code('branch-a',"""
from clinic.models import SyncOutbox
from django.db.models import F
assert SyncOutbox.objects.filter(version__gt=F('acknowledged_version')).exists()
""")
        central=start('central')
        command('branch-a','manage.py','sync_clinic');command('branch-b','manage.py','sync_clinic')
        code('central',"""
from clinic.models import RemoteRecord
from decimal import Decimal
visits=list(RemoteRecord.objects.filter(entity_type='visit'))
assert len(visits)==3
entries=[entry for visit in visits for entry in visit.payload['transactions']]
assert sum(Decimal(e['amount']) if e['kind']=='collection' else -Decimal(e['amount']) for e in entries)==1700
assert len([e for e in entries if e['kind']=='refund'])==1
assert any(len(v.payload['examinations'])==2 for v in visits)
""")
        code('branch-b',"""
from clinic.models import *
from django.test import Client
from django.urls import reverse
c=Client();c.force_login(User.objects.get(username='branch-doctor'))
response=c.get(reverse('shared_history',args=[Patient.objects.first().pk]))
assert b'Revised examination after outage' in response.content
assert b'Initial examination' in response.content
assert Booking.objects.count()==1
""")
        print('PASS: separate databases, authenticated HTTP sync, shared patient reuse, doctor-only history, offline booking/refund/revision, reconnection, preserved versions, PKR 1700 consolidated net fees.')
        if args.keep_temporary:
            for name in ['branch-a','branch-b']:
                command(name,'manage.py','collectstatic','--noinput');start(name)
            print(json.dumps({'temporary_folder':str(folder),'ports':ports}))
    finally:
        if not args.keep_temporary or sys.exc_info()[0]:
            for proc in processes:
                if proc.poll() is None: proc.terminate();proc.wait(timeout=10)
            if not args.keep_temporary: shutil.rmtree(folder)

if __name__ == '__main__': main()
