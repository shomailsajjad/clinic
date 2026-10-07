import copy
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import (Organization, Clinic, User, Patient, Service, Diagnosis, Booking, OPDVersion,
                     SyncCredential, SyncOutbox, SyncState, RemoteRecord, PatientAlias)
from .forms import OPDForm, StaffForm
from . import workflows, sync

class BranchTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(pk=1)
        self.a = Clinic.objects.get(pk=1)
        self.b = Clinic.objects.create(organization=self.org, name='Second Clinic', code='SC', doctor_name='Dr. Second')
        self.other_org = Organization.objects.create(name='Other organization')
        self.c = Clinic.objects.create(organization=self.other_org, name='Private Clinic', code='PC')
        self.admin = User.objects.create_user('admin1', role='admin', clinic=self.a)
        self.operator = User.objects.create_user('operator1', role='operator', clinic=self.a)
        self.doctor = User.objects.create_user('doctor1', role='doctor', clinic=self.a)
        self.doctor_b = User.objects.create_user('doctor2', role='doctor', clinic=self.b)
        self.admin_b = User.objects.create_user('admin2', role='admin', clinic=self.b)
        self.owner = User.objects.create_user('owner', role='owner', clinic=None)
        self.patient = Patient.objects.create(name='Test patient', age=35, gender='female', created_by=self.operator, clinical_history='Sensitive baseline')
        self.foreign = Patient.objects.create(name='Other branch patient', age=40, gender='male', created_by=self.admin_b, owner_clinic=self.b, clinical_history='Foreign clinical secret')
        self.hidden = Patient.objects.create(name='Other organization patient', age=55, gender='male', organization=self.other_org, owner_clinic=self.c, created_by=self.admin_b)
        self.service = Service.objects.create(name='Consultation', category='opd', token_prefix='OPD', price=1000, clinic=self.a)
        self.service_b = Service.objects.create(name='Consultation', category='opd', token_prefix='OPD', price=800, clinic=self.b)
        self.diagnosis = Diagnosis.objects.create(name='Structured diagnosis')
        self.booking = self.book(self.operator, self.patient, self.service)
        self.booking_b = self.book(self.admin_b, self.foreign, self.service_b)
    def book(self, user, patient, service):
        return workflows.create_booking(user, patient, [service], 'walk_in', timezone.localdate(), None, uuid.uuid4(), '', '')
    def exam_data(self, version=0, reason=''):
        return {'base_version':version, 'chief_complaint':'Test complaint', 'history':'Clinical examination history',
            'examination':'Test examination', 'blood_pressure':'120/80', 'pulse':70, 'temperature':Decimal('37.0'), 'weight':None,
            'diagnoses':[self.diagnosis], 'prescription':[{'medicine':'Example medicine','dose':'Doctor entered dose','route':'oral','frequency':'Doctor entered frequency','duration':'Doctor entered duration','instructions':''}],
            'advice':'Doctor advice', 'follow_up_date':None, 'finalized':True, 'revision_reason':reason}
    def envelope(self, kind, obj):
        row = SyncOutbox.objects.get(entity_type=kind, entity_id=obj.pk)
        return sync.outgoing_envelope(row)
    def credential(self):
        return SyncCredential.objects.create(clinic=self.a, token_hash=sync.token_hash('private-test-key'))
    def test_own_clinic_services_and_bookings(self):
        self.assertEqual(list(Service.objects.for_user(self.operator)), [self.service])
        self.assertEqual(list(Booking.objects.for_user(self.doctor)), [self.booking])
        self.assertEqual(Booking.objects.for_user(self.owner).count(), 2)
        self.assertNotIn(self.hidden, Patient.objects.for_user(self.doctor))
    def test_foreign_service_and_mutation_rejected(self):
        with self.assertRaises(PermissionDenied):
            workflows.save_opd(self.doctor, self.booking_b.pk, self.exam_data())
        with self.assertRaises(ValidationError):
            self.book(self.operator, self.patient, self.service_b)
    def test_one_active_doctor_database_constraint(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user('another', role='doctor', clinic=self.a)
        self.doctor.is_active=False; self.doctor.save()
        User.objects.create_user('replacement', role='doctor', clinic=self.a)
    def test_clinic_tokens_independent(self):
        self.assertEqual(self.booking.items.first().tokens.first().number, 1)
        self.assertEqual(self.booking_b.items.first().tokens.first().number, 1)
    def test_shared_history_doctor_only(self):
        report=workflows.save_opd(self.doctor_b,self.booking_b.pk,self.exam_data())
        for user in [self.operator, self.admin, self.owner]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('shared_history', args=[self.foreign.pk])).status_code,403)
            response=self.client.get(reverse('patient_detail',args=[self.foreign.pk]))
            self.assertNotContains(response,'Foreign clinical secret')
        self.client.force_login(self.doctor)
        response=self.client.get(reverse('shared_history',args=[self.foreign.pk]))
        self.assertContains(response,'Test examination')
        self.assertEqual(self.client.get(reverse('opd_print',args=[report.pk])).status_code,404)
    def test_cross_organization_history_hidden(self):
        self.client.force_login(self.doctor)
        self.assertEqual(self.client.get(reverse('shared_history',args=[self.hidden.pk])).status_code,404)
    def test_prescriptions_immutable_and_letterhead_snapshotted(self):
        first=workflows.save_opd(self.doctor,self.booking.pk,self.exam_data())
        data=self.exam_data(1,'Corrected examination');data['examination']='Revised examination'
        second=workflows.save_opd(self.doctor,self.booking.pk,data)
        self.a.name='Changed name'; self.a.save()
        first.refresh_from_db()
        self.assertEqual(first.examination,'Test examination')
        self.assertTrue(second.is_revision)
        self.client.force_login(self.doctor)
        response=self.client.get(reverse('opd_print',args=[first.pk]))
        self.assertContains(response,'SUPERSEDED VERSION')
        self.assertContains(response,'Sajjad Poly Clinic')
        self.assertContains(response,'Example medicine')
        self.assertNotContains(response,'Changed name')
    def test_stale_and_reasonless_revisions_rejected(self):
        workflows.save_opd(self.doctor,self.booking.pk,self.exam_data())
        for data in [self.exam_data(),self.exam_data(1)]:
            with self.assertRaises(ValidationError): workflows.save_opd(self.doctor,self.booking.pk,data)
        self.assertEqual(OPDVersion.objects.count(),1)
    def test_opd_form_parses_medicines_and_filters_diagnoses(self):
        data=self.exam_data();data['diagnoses']=[self.diagnosis.pk];data['prescription']='Example | Dose | oral | daily | 5 days |'
        form=OPDForm(data,user=self.doctor)
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data['prescription'][0]['medicine'],'Example')
    def test_backup_shared_clinical_history_denied(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('backup_download')).status_code,403)
    def test_owner_report_no_clinical_text(self):
        workflows.save_opd(self.doctor,self.booking.pk,self.exam_data())
        workflows.collect_payment(self.operator,self.booking.pk,Decimal('1000'),'cash','',uuid.uuid4())
        self.client.force_login(self.owner)
        today=str(timezone.localdate())
        response=self.client.get(reverse('organization_report'),{'start':today,'end':today})
        self.assertContains(response,'Structured diagnosis')
        self.assertContains(response,'1000')
        self.assertNotContains(response,'Test examination')
    def test_owner_clinic_configuration_and_local_edit_restriction(self):
        self.client.force_login(self.owner)
        response=self.client.get(reverse('branch_configuration',args=[self.b.pk]))
        self.assertEqual(response.json()['clinic']['code'],'SC')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('clinic_edit',args=[self.b.pk])).status_code,403)
    @override_settings(CLINIC_NODE_MODE='branch',CLINIC_LOCAL_CODE='SPC')
    def test_branch_binding_rejects_other_clinic_login(self):
        self.client.force_login(self.doctor_b)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code,403)
        self.client.force_login(self.doctor)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code,200)
    @override_settings(CLINIC_NODE_MODE='central')
    def test_central_does_not_mutate_visits(self):
        with self.assertRaises(ValidationError): workflows.save_opd(self.doctor,self.booking.pk,self.exam_data())
    def test_sync_idempotency_and_authoritative_server_binding(self):
        credential=self.credential();node=uuid.uuid4();envelope=self.envelope('visit',self.booking)
        self.assertEqual(sync.receive_records(credential,node,[envelope])[0]['status'],'accepted')
        self.assertEqual(sync.receive_records(credential,node,[envelope])[0]['status'],'duplicate')
        self.assertEqual(RemoteRecord.objects.count(),1)
        with self.assertRaises(ValidationError): sync.receive_records(credential,uuid.uuid4(),[envelope])
        conflict=copy.deepcopy(envelope);conflict['payload']['status']='cancelled'
        with self.assertRaises(ValidationError): sync.receive_records(credential,node,[conflict])
    def test_sync_preserves_financial_and_clinical_versions(self):
        workflows.collect_payment(self.operator,self.booking.pk,Decimal('1000'),'cash','',uuid.uuid4())
        workflows.save_opd(self.doctor,self.booking.pk,self.exam_data())
        credential=self.credential();node=uuid.uuid4();envelope=self.envelope('visit',self.booking)
        sync.receive_records(credential,node,[envelope])
        for key in ['transactions','examinations']:
            corrupted=copy.deepcopy(envelope);corrupted['version']+=1;corrupted['payload'][key]=[]
            with self.assertRaises(ValidationError):sync.receive_records(credential,node,[corrupted])
    def test_offline_queue_is_retained_and_retry_acknowledges(self):
        pending=SyncOutbox.objects.get(entity_type='visit',entity_id=self.booking.pk)
        with patch('clinic.sync.request_json',side_effect=RuntimeError('Offline')):
            with self.assertRaises(RuntimeError): sync.synchronize_once(self.a,'http://127.0.0.1:8129','private-test-key')
        pending.refresh_from_db();self.assertEqual(pending.acknowledged_version,0)
        credential=self.credential()
        def request(origin,token,path,payload):
            if path.endswith('push/'):
                return {'accepted':sync.receive_records(credential,payload['node_id'],payload['records'])}
            return sync.pull_records(self.a,payload['cursor'])
        with patch('clinic.sync.request_json',side_effect=request):sync.synchronize_once(self.a,'http://127.0.0.1:8129','private-test-key')
        pending.refresh_from_db();self.assertEqual(pending.acknowledged_version,pending.version)
        self.assertIsNotNone(SyncState.objects.get(clinic=self.a).last_success)
    def test_pull_is_organization_scoped_and_excludes_own(self):
        credential=self.credential();sync.receive_records(credential,uuid.uuid4(),[self.envelope('patient',self.patient)])
        other=SyncCredential.objects.create(clinic=self.c,token_hash='other')
        # No primary patients from another organization appear in pull metadata.
        response=sync.pull_records(self.b,0)
        self.assertEqual(len(response['records']),1)
        self.assertNotIn(str(self.c.global_id),[row['global_id'] for row in response['clinics']])
        self.assertEqual(sync.pull_records(self.a,0)['records'],[])
    @override_settings(CLINIC_NODE_MODE='central')
    def test_sync_api_rejects_missing_key_and_accepts_bound_key(self):
        self.credential()
        self.assertEqual(self.client.post('/sync/push/',data='{}',content_type='application/json').status_code,403)
        response=self.client.post('/sync/push/',data={'node_id':str(uuid.uuid4()),'records':[]},content_type='application/json',HTTP_AUTHORIZATION='Bearer private-test-key')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json(),{'accepted':[]})
        response=self.client.post('/sync/pull/',data={'cursor':0},content_type='application/json',HTTP_AUTHORIZATION='Bearer private-test-key')
        self.assertEqual(response.status_code,200)
    def test_identity_link_preserves_records_and_disallows_cycles(self):
        sync.link_patients(self.owner,self.foreign.global_id,self.patient.global_id,'Verified same person')
        self.assertEqual(sync.identity_group(self.org,self.patient.global_id),{self.patient.global_id,self.foreign.global_id})
        with self.assertRaises(ValidationError):sync.link_patients(self.owner,self.patient.global_id,self.foreign.global_id,'Reverse')
        self.booking_b.refresh_from_db();self.assertEqual(self.booking_b.patient_id,self.foreign.pk)
        with self.assertRaises(PermissionDenied):sync.link_patients(self.admin,self.foreign.global_id,self.patient.global_id,'No permission')
    def test_sync_url_rejects_plaintext_public_and_credentials(self):
        for url in ['http://example.com','https://user:password@example.com','https://example.com/other']:
            with self.assertRaises(RuntimeError):sync.validate_sync_url(url)
        self.assertEqual(sync.validate_sync_url('https://example.com'),'https://example.com')

    def test_opd_finalization_leaves_imaging_pending(self):
        imaging=Service.objects.create(name='Ultrasound',token_prefix='US',price=500,clinic=self.a)
        mixed=workflows.create_booking(self.operator,self.patient,[self.service,imaging],'walk_in',timezone.localdate(),None,uuid.uuid4())
        workflows.save_opd(self.doctor,mixed.pk,self.exam_data())
        mixed.refresh_from_db()
        self.assertEqual(mixed.items.get(service=self.service).status,'completed')
        self.assertNotEqual(mixed.items.get(service=imaging).status,'completed')
        self.assertNotEqual(mixed.status,'completed')

    def test_sync_identity_cannot_be_claimed_by_another_branch(self):
        credential=self.credential();envelope=self.envelope('patient',self.patient)
        sync.receive_records(credential,uuid.uuid4(),[envelope])
        forged=copy.deepcopy(envelope);forged['payload']['owner_clinic']=str(self.b.global_id)
        other=SyncCredential.objects.create(clinic=self.b,token_hash='another')
        with self.assertRaises(ValidationError):sync.receive_records(other,uuid.uuid4(),[forged])
