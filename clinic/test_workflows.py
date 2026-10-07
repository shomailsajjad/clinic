import json
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import workflows as flow
from .backup import create_backup, restore_backup, verify_backup
from .models import (Booking, Diagnosis, DiscountRequest, MoneyTransaction, Patient,
                     ReportTemplate, ReportVersion, Service, Token, TokenCounter, User)


class ClinicOperationsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('admin_test', password='Synthetic-Test-Only!482')
        cls.operator = User.objects.create_user('operator_test', password='Synthetic-Test-Only!482')
        cls.doctor = User.objects.create_user('doctor_test', role='doctor', password='Synthetic-Test-Only!482')
        cls.patient = Patient.objects.create(name='Synthetic Patient', age=35, gender='female', created_by=cls.operator)
        cls.ultrasound = Service.objects.create(name='Ultrasound', token_prefix='US', price=Decimal('2500.00'))
        cls.xray = Service.objects.create(name='X-ray', token_prefix='XR', price=Decimal('1000.00'))
        cls.diagnosis = Diagnosis.objects.create(name='Synthetic diagnosis')
        cls.template = ReportTemplate.objects.create(name='Synthetic ultrasound template', service=cls.ultrasound,
            fields=[{'name': 'measurement', 'label': 'Measurement', 'unit': 'cm'}],
            findings='Measurement: [Measurement] cm.', impression='Synthetic impression.')

    def book(self, **overrides):
        values = {'actor': self.operator, 'patient': self.patient, 'services': [self.ultrasound],
                  'kind': 'walk_in', 'scheduled_date': timezone.localdate(), 'scheduled_time': None,
                  'request_id': uuid.uuid4(), 'referring_doctor': 'Synthetic Referrer'}
        values.update(overrides)
        return flow.create_booking(**values)

    def pay(self, booking, **overrides):
        values = {'actor': self.operator, 'booking_id': booking.pk, 'amount': booking.due,
                  'method': 'cash', 'reference': '', 'request_id': uuid.uuid4()}
        values.update(overrides)
        return flow.collect_payment(**values)

    def report(self, item, **overrides):
        previous = item.reports.first()
        values = {'actor': self.doctor, 'item_id': item.pk, 'template': self.template,
                  'values': {'measurement': '12'}, 'findings': self.template.findings,
                  'impression': self.template.impression, 'diagnoses': [self.diagnosis],
                  'finalized': True, 'revision_reason': '', 'base_version': previous.version if previous else 0,
                  'template_version': self.template.version}
        values.update(overrides)
        return flow.save_report(**values)

    def test_separate_service_sequences_and_price_snapshots(self):
        booking = self.book(services=[self.ultrasound, self.xray])
        second = self.book()
        self.assertEqual(list(Token.objects.order_by('service_id', 'number').values_list('prefix', 'number')),
                         [('US', 1), ('US', 2), ('XR', 1)])
        self.ultrasound.price = Decimal('9999.00')
        self.ultrasound.save()
        self.assertEqual(booking.gross, Decimal('3500.00'))
        self.assertEqual(second.gross, Decimal('2500.00'))

    def test_daily_rollover_and_future_appointment_sequences(self):
        today = self.book()
        tomorrow = timezone.localdate() + timedelta(days=1)
        future = self.book(kind='appointment', scheduled_date=tomorrow, scheduled_time=time(10))
        self.assertEqual(today.items.get().current_token.number, 1)
        self.assertEqual(future.items.get().current_token.number, 1)
        self.assertEqual(future.items.get().current_token.date, tomorrow)
        self.assertEqual(TokenCounter.objects.count(), 2)

    def test_duplicate_booking_submission_is_idempotent(self):
        request_id = uuid.uuid4()
        first = self.book(request_id=request_id)
        second = self.book(request_id=request_id)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(Token.objects.count(), 1)

    def test_date_type_and_active_service_validation(self):
        for values in [{'scheduled_date': timezone.localdate() - timedelta(days=1)},
                       {'scheduled_date': timezone.localdate() + timedelta(days=1)},
                       {'kind': 'appointment'}, {'kind': 'invalid'}, {'services': []}]:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.book(**values)
        self.ultrasound.is_active = False
        self.ultrasound.save()
        with self.assertRaises(ValidationError):
            self.book()
        self.assertEqual(Booking.objects.count(), 0)

    def test_failed_booking_rolls_back_tokens_and_counter(self):
        with patch('clinic.workflows.record_event', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):
                self.book()
        self.assertFalse(Booking.objects.exists())
        self.assertFalse(Token.objects.exists())
        self.assertFalse(TokenCounter.objects.exists())
        self.assertEqual(self.book().items.get().current_token.number, 1)

    def test_cancelled_tokens_are_not_reused(self):
        booking = self.book()
        flow.change_booking_status(self.operator, booking.pk, 'cancelled')
        self.assertFalse(Token.objects.get(item__booking=booking).is_active)
        self.assertEqual(self.book().items.get().current_token.number, 2)
        with self.assertRaises(ValidationError):
            self.pay(booking)

    def test_rescheduling_preserves_old_token_and_issues_new_date_sequence(self):
        booking = self.book()
        original = booking.items.get().current_token
        new_date = timezone.localdate() + timedelta(days=1)
        flow.reschedule_booking(self.operator, booking.pk, new_date, time(11))
        original.refresh_from_db()
        self.assertFalse(original.is_active)
        token = booking.items.get().current_token
        self.assertEqual(token.date, new_date)
        self.assertEqual(token.number, 1)
        flow.reschedule_booking(self.operator, booking.pk, new_date, time(12))
        self.assertEqual(Token.objects.count(), 2)
        with self.assertRaises(ValidationError):
            flow.change_booking_status(self.operator, booking.pk, 'arrived')

    def test_discount_requires_admin_and_blocks_pending_payment(self):
        booking = self.book()
        item = booking.items.get()
        request = flow.request_discount(self.operator, item.pk, Decimal('500.00'), 'Synthetic concession')
        self.assertEqual(booking.due, Decimal('2500.00'))
        with self.assertRaises(ValidationError):
            self.pay(booking)
        with self.assertRaises(PermissionDenied):
            flow.review_discount(self.operator, request.pk, True)
        flow.review_discount(self.admin, request.pk, True)
        self.assertEqual(booking.due, Decimal('2000.00'))
        self.pay(booking)
        with self.assertRaises(ValidationError):
            flow.request_discount(self.operator, item.pk, Decimal('100.00'), 'Too late')

    def test_discount_overcharge_and_duplicate_pending_request_rejected(self):
        item = self.book().items.get()
        with self.assertRaises(ValidationError):
            flow.request_discount(self.operator, item.pk, Decimal('3000.00'), 'Synthetic')
        flow.request_discount(self.operator, item.pk, Decimal('100.00'), 'Synthetic')
        with self.assertRaises(ValidationError):
            flow.request_discount(self.operator, item.pk, Decimal('100.00'), 'Duplicate')

    def test_payment_requires_full_amount_and_digital_reference(self):
        booking = self.book()
        with self.assertRaises(ValidationError):
            self.pay(booking, amount=Decimal('100.00'))
        with self.assertRaises(ValidationError):
            self.pay(booking, method='bank')
        record = self.pay(booking, method='bank', reference='SYNTHETIC-REF-001')
        self.assertEqual(record.reference, 'SYNTHETIC-REF-001')
        self.assertEqual(record.snapshot['patient']['name'], self.patient.name)

    def test_payment_duplicate_submission_and_second_collection(self):
        booking = self.book()
        request_id = uuid.uuid4()
        record = self.pay(booking, request_id=request_id)
        self.assertEqual(self.pay(booking, request_id=request_id).pk, record.pk)
        with self.assertRaises(ValidationError):
            self.pay(booking)
        self.assertEqual(MoneyTransaction.objects.count(), 1)

    def test_refund_limit_roles_and_idempotency(self):
        booking = self.book()
        record = self.pay(booking)
        with self.assertRaises(PermissionDenied):
            flow.refund_payment(self.operator, record.pk, Decimal('100'), 'cash', '', 'Synthetic', uuid.uuid4())
        request_id = uuid.uuid4()
        refund = flow.refund_payment(self.admin, record.pk, Decimal('500'), 'cash', '', 'Synthetic', request_id)
        self.assertEqual(flow.refund_payment(self.admin, record.pk, Decimal('500'), 'cash', '', 'Synthetic', request_id).pk, refund.pk)
        with self.assertRaises(ValidationError):
            flow.refund_payment(self.admin, record.pk, Decimal('2001'), 'cash', '', 'Synthetic', uuid.uuid4())
        self.assertEqual(booking.net_collected, Decimal('2000'))
        flow.refund_payment(self.admin, record.pk, Decimal('2000'), 'cash', '', 'Synthetic', uuid.uuid4())
        self.assertEqual(booking.net_collected, Decimal('0'))
        with self.assertRaises(ValidationError):
            self.pay(booking)

    def test_cash_to_digital_correction_preserves_original_and_cash_totals(self):
        booking = self.book()
        original = self.pay(booking)
        replacement = flow.correct_payment(self.admin, original.pk, 'bank', 'SYNTHETIC-CORRECTED', 'Wrong method', uuid.uuid4())
        original.refresh_from_db()
        self.assertEqual(original.method, 'cash')
        self.assertEqual(replacement.amount, original.amount)
        self.assertEqual(MoneyTransaction.objects.filter(kind='reversal').count(), 1)
        self.assertEqual(booking.net_collected, Decimal('2500'))
        with self.assertRaises(ValidationError):
            flow.correct_payment(self.admin, original.pk, 'cash', '', 'Again', uuid.uuid4())
        self.client.force_login(self.admin)
        response = self.client.get(reverse('cash_report'))
        self.assertEqual(response.context['net'], Decimal('2500'))
        self.assertEqual(dict(response.context['methods']), {'cash': Decimal('0'), 'bank': Decimal('2500')})

    def test_finalized_report_revisions_preserve_values_and_restrict_printing(self):
        item = self.book().items.get()
        first = self.report(item)
        self.assertEqual(first.findings, 'Measurement: 12 cm.')
        with self.assertRaises(ValidationError):
            self.report(item, values={'measurement': '14'})
        second = self.report(item, values={'measurement': '14'}, revision_reason='Updated measurement')
        first.refresh_from_db()
        self.assertEqual(first.findings, 'Measurement: 12 cm.')
        self.assertEqual(second.findings, 'Measurement: 14 cm.')
        self.assertEqual(second.previous, first)
        self.assertTrue(second.is_revision)
        for user in [self.admin, self.operator]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('report_print', args=[second.pk])).status_code, 403)
            self.assertEqual(self.client.get(reverse('report_edit', args=[item.pk])).status_code, 403)
        self.client.force_login(self.doctor)
        self.assertContains(self.client.get(reverse('report_print', args=[second.pk])), '(Revised)')
        self.assertContains(self.client.get(reverse('report_print', args=[first.pk])), 'SUPERSEDED VERSION')

    def test_draft_cannot_print_and_stale_version_cannot_overwrite(self):
        item = self.book().items.get()
        draft = self.report(item, finalized=False)
        self.client.force_login(self.doctor)
        self.assertEqual(self.client.get(reverse('report_print', args=[draft.pk])).status_code, 404)
        with self.assertRaises(ValidationError):
            self.report(item, base_version=0)
        finalized = self.report(item)
        self.assertFalse(finalized.is_revision)

    def test_wrong_service_or_unknown_placeholders_rejected(self):
        item = self.book(services=[self.xray]).items.get()
        with self.assertRaises(ValidationError):
            self.report(item)
        item = self.book().items.get()
        with self.assertRaises(ValidationError):
            self.report(item, findings='[Missing field]')
        with self.assertRaises(PermissionDenied):
            self.report(item, actor=self.admin)

    def test_template_and_patient_changes_do_not_rewrite_old_reports(self):
        item = self.book().items.get()
        report = self.report(item)
        self.template.name = 'New template title'
        self.template.findings = 'Completely new template'
        self.template.version += 1
        self.template.save()
        self.patient.name = 'Changed patient name'
        self.patient.save()
        report.refresh_from_db()
        self.assertEqual(report.template_snapshot['name'], 'Synthetic ultrasound template')
        self.assertEqual(report.patient_snapshot['name'], 'Synthetic Patient')
        self.assertEqual(report.findings, 'Measurement: 12 cm.')
        with self.assertRaises(ValidationError):
            flow.reschedule_booking(self.operator, item.booking_id, timezone.localdate(), time(12))

    def test_patient_analytics_count_latest_diagnosis_and_distinct_visits(self):
        item = self.book().items.get()
        self.report(item)
        changed = Diagnosis.objects.create(name='Revised diagnosis')
        self.report(item, diagnoses=[changed], revision_reason='Correct diagnosis')
        self.client.force_login(self.admin)
        response = self.client.get(reverse('patient_reports'))
        self.assertEqual(response.context['patient_count'], 1)
        self.assertEqual(response.context['booking_count'], 1)
        self.assertEqual(response.context['diseases'], [('Revised diagnosis', 1)])
        response = self.client.get(reverse('patient_reports'), {'diagnosis': self.diagnosis.pk})
        self.assertEqual(response.context['booking_count'], 0)
        self.assertEqual(self.client.get(reverse('patient_reports'), {'export': 'csv'}).headers['Content-Type'], 'text/csv')

    def test_portal_pages_render_and_financial_admin_routes_are_restricted(self):
        booking = self.book()
        item = booking.items.get()
        payment = self.pay(booking)
        self.client.force_login(self.admin)
        for route in ['booking_list', 'booking_create', 'queue', 'discount_list', 'cash_report',
                      'patient_reports', 'diagnosis_list', 'diagnosis_create', 'template_list', 'template_create', 'backup_page']:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse(route)).status_code, 200)
        self.assertEqual(self.client.get(reverse('booking_detail', args=[booking.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('receipt', args=[payment.pk])).status_code, 200)
        self.client.force_login(self.operator)
        for route in ['cash_report', 'patient_reports', 'discount_list', 'template_list', 'backup_page', 'diagnosis_list']:
            self.assertEqual(self.client.get(reverse(route)).status_code, 403)
        self.assertEqual(self.client.post(reverse('payment_refund', args=[payment.pk]), {}).status_code, 403)
        self.client.force_login(self.doctor)
        self.assertEqual(self.client.get(reverse('booking_create')).status_code, 403)
        self.assertEqual(self.client.get(reverse('receipt', args=[payment.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('report_edit', args=[item.pk])).status_code, 200)

    def test_status_changes_and_backup_download_require_post(self):
        booking = self.book()
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('booking_status', args=[booking.pk])).status_code, 405)
        self.assertEqual(self.client.get(reverse('backup_download')).status_code, 405)

    def test_web_booking_payment_template_and_report_forms(self):
        self.client.force_login(self.operator)
        response = self.client.post(reverse('booking_create'), {'request_id': uuid.uuid4(),
            'patient': self.patient.pk, 'services': [self.ultrasound.pk], 'kind': 'walk_in',
            'scheduled_date': timezone.localdate().isoformat(), 'referring_doctor': 'Synthetic Referrer'})
        self.assertEqual(response.status_code, 302)
        booking = Booking.objects.get()
        response = self.client.post(reverse('payment_create', args=[booking.pk]), {
            'request_id': uuid.uuid4(), 'amount': '2500', 'method': 'bank', 'reference': 'SYNTHETIC'})
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.admin)
        response = self.client.post(reverse('template_create'), {'name': 'Web template', 'service': self.ultrasound.pk,
            'measurement_fields': 'Length | cm', 'findings': 'Length [Length]', 'impression': 'Synthetic findings',
            'is_active': 'on', 'base_version': 0})
        self.assertEqual(response.status_code, 302)
        template = ReportTemplate.objects.get(name='Web template')
        self.client.force_login(self.doctor)
        item = booking.items.get()
        response = self.client.post(reverse('report_edit', args=[item.pk]) + f'?template={template.pk}', {
            'base_version': 0, 'template_version': 1, 'findings': 'Length [Length]', 'impression': 'Synthetic findings',
            'measurement_length': '10', 'diagnoses': [self.diagnosis.pk], 'finalized': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ReportVersion.objects.get().findings, 'Length 10')


class BackupIntegrityTests(SimpleTestCase):
    def test_sqlite_backup_restore_and_checksum_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder / 'source.sqlite3'
            with sqlite3.connect(source) as db:
                db.execute('CREATE TABLE django_migrations (id INTEGER)')
                db.execute('CREATE TABLE clinic_user (id INTEGER)')
                db.execute('CREATE TABLE clinic_patient (name TEXT)')
                db.execute("INSERT INTO clinic_patient VALUES ('Synthetic Patient')")
            database = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': source}}
            with override_settings(DATABASES=database, RUNTIME_DIR=folder):
                path = create_backup(folder / 'backup.zip')
                self.assertEqual(verify_backup(path)['engine'], 'sqlite')
                target = folder / 'restored.sqlite3'
                restore_backup(path, target)
                with sqlite3.connect(target) as db:
                    self.assertEqual(db.execute('SELECT name FROM clinic_patient').fetchone()[0], 'Synthetic Patient')
                with self.assertRaises(RuntimeError):
                    restore_backup(path, source)
                corrupt = folder / 'corrupt.zip'
                with zipfile.ZipFile(path) as original, zipfile.ZipFile(corrupt, 'w') as archive:
                    archive.writestr('manifest.json', original.read('manifest.json'))
                    archive.writestr('database.sqlite3', b'not the backed-up database')
                with self.assertRaisesRegex(RuntimeError, 'checksum'):
                    verify_backup(corrupt)
