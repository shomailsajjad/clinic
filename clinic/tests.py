from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AuditEvent, Patient, Service, User


class ClinicWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('administrator', password='Synthetic-Test-Only!482')
        cls.operator = User.objects.create_user('operator1', password='Synthetic-Test-Only!482')
        cls.doctor = User.objects.create_user('doctor', role=User.Role.DOCTOR, password='Synthetic-Test-Only!482')
        cls.patient = Patient.objects.create(name='Synthetic Patient', age=30, gender='female',
                                             phone='03001234567', created_by=cls.operator)
        cls.service = Service.objects.create(name='Ultrasound', token_prefix='US', price=Decimal('2500.00'))

    def patient_data(self, **overrides):
        data = {'name': 'Synthetic New Patient', 'age': 25, 'age_unit': 'years',
                'gender': 'male', 'phone': '03001234567', 'cnic': '',
                'address': 'Synthetic address', 'referring_doctor': 'Synthetic Referrer',
                'clinical_history': 'Synthetic history'}
        data.update(overrides)
        return data

    def test_anonymous_routes_require_login(self):
        for route in ['dashboard', 'patient_list', 'patient_create', 'service_list',
                      'service_create', 'staff_list', 'staff_create', 'audit_list']:
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith('/login/'))

    def test_password_login_and_post_only_logout(self):
        response = self.client.post(reverse('login'), {'username': 'operator1', 'password': 'wrong'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        response = self.client.post(reverse('login'), {'username': 'operator1', 'password': 'Synthetic-Test-Only!482'})
        self.assertRedirects(response, reverse('dashboard'))
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        self.assertRedirects(self.client.post(reverse('logout')), reverse('login'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_inactive_user_cannot_login(self):
        self.operator.is_active = False
        self.operator.save()
        self.assertFalse(self.client.login(username='operator1', password='Synthetic-Test-Only!482'))

    def test_superuser_bootstrap_has_admin_role(self):
        self.assertEqual(self.admin.role, User.Role.ADMIN)

    def test_operator_can_register_and_search_patient(self):
        self.client.force_login(self.operator)
        response = self.client.post(reverse('patient_create'), self.patient_data(cnic='12345-1234567-1'))
        patient = Patient.objects.get(name='Synthetic New Patient')
        self.assertRedirects(response, reverse('patient_detail', args=[patient.pk]))
        self.assertEqual(patient.cnic, '1234512345671')
        self.assertEqual(patient.created_by, self.operator)
        self.assertEqual(AuditEvent.objects.get().action, 'patient.created')
        for query in [patient.name, patient.phone, '12345-1234567-1', patient.patient_number]:
            with self.subTest(query=query):
                response = self.client.get(reverse('patient_list'), {'q': query})
                self.assertContains(response, patient.patient_number)

    def test_multiple_patients_can_have_blank_cnic_and_shared_phone(self):
        self.client.force_login(self.operator)
        for name in ['Synthetic First', 'Synthetic Second']:
            response = self.client.post(reverse('patient_create'), self.patient_data(name=name))
            self.assertEqual(response.status_code, 302)
        self.assertEqual(Patient.objects.filter(cnic__isnull=True).count(), 3)

    def test_duplicate_and_invalid_cnic_are_rejected(self):
        self.client.force_login(self.operator)
        self.client.post(reverse('patient_create'), self.patient_data(cnic='1234512345671'))
        response = self.client.post(reverse('patient_create'), self.patient_data(cnic='12345-1234567-1'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('cnic', response.context['form'].errors)
        response = self.client.post(reverse('patient_create'), self.patient_data(cnic='invalid'))
        self.assertIn('cnic', response.context['form'].errors)
        self.assertEqual(Patient.objects.count(), 2)

    def test_age_and_date_of_birth_validation(self):
        self.client.force_login(self.operator)
        for values, error in [({'age': ''}, 'age'), ({'age': -1}, 'age'),
                              ({'age': 131}, 'age'),
                              ({'date_of_birth': timezone.localdate() + timedelta(days=1)}, 'date_of_birth')]:
            with self.subTest(values=values):
                response = self.client.post(reverse('patient_create'), self.patient_data(**values))
                self.assertEqual(response.status_code, 200)
                self.assertIn(error, response.context['form'].errors)
        self.assertEqual(Patient.objects.count(), 1)

    def test_infant_age_and_actual_date_of_birth_are_supported(self):
        self.client.force_login(self.operator)
        response = self.client.post(reverse('patient_create'), self.patient_data(age=3, age_unit='months'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Patient.objects.get(name='Synthetic New Patient').age_display, '3 months')
        response = self.client.post(reverse('patient_create'), self.patient_data(
            name='Synthetic Newborn', date_of_birth=timezone.localdate(), age=''))
        self.assertEqual(response.status_code, 302)
        newborn = Patient.objects.get(name='Synthetic Newborn')
        self.assertIsNone(newborn.age)
        self.assertEqual(newborn.age_display, '0 days')

    def test_patient_update_is_audited_and_cannot_spoof_creator(self):
        self.client.force_login(self.operator)
        response = self.client.post(reverse('patient_edit', args=[self.patient.pk]),
                                    self.patient_data(name='Synthetic Updated', created_by=self.admin.pk))
        self.assertEqual(response.status_code, 302)
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.created_by, self.operator)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, 'patient.updated')
        self.assertIn('name', event.changed_fields)

    def test_doctor_can_read_but_cannot_modify_patient(self):
        self.client.force_login(self.doctor)
        self.assertEqual(self.client.get(reverse('patient_detail', args=[self.patient.pk])).status_code, 200)
        for url in [reverse('patient_create'), reverse('patient_edit', args=[self.patient.pk])]:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, self.patient_data()).status_code, 403)
        self.assertEqual(Patient.objects.count(), 1)

    def test_non_admin_roles_cannot_manage_services_or_users(self):
        for user in [self.operator, self.doctor]:
            self.client.force_login(user)
            for url in [reverse('service_create'), reverse('service_edit', args=[self.service.pk]),
                        reverse('staff_create'), reverse('staff_edit', args=[self.admin.pk]),
                        reverse('staff_list'), reverse('audit_list')]:
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)
                    self.assertEqual(self.client.post(url, {'role': 'admin', 'price': '0'}).status_code, 403)
        self.service.refresh_from_db()
        self.assertEqual(self.service.price, Decimal('2500.00'))

    def test_admin_sets_exact_price_and_archives_service(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('service_create'), {'name': 'X-ray', 'token_prefix': 'xr',
                                                               'price': '1200.50', 'is_active': 'on'})
        self.assertRedirects(response, reverse('service_list'))
        service = Service.objects.get(name='X-ray')
        self.assertEqual(service.token_prefix, 'XR')
        self.assertEqual(service.price, Decimal('1200.50'))
        response = self.client.post(reverse('service_edit', args=[service.pk]), {
            'name': service.name, 'token_prefix': 'XR', 'price': '1300.75'})
        self.assertRedirects(response, reverse('service_list'))
        service.refresh_from_db()
        self.assertFalse(service.is_active)
        self.assertEqual(AuditEvent.objects.count(), 2)

    def test_negative_service_price_rejected_by_form_and_database(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('service_create'), {'name': 'Invalid', 'token_prefix': 'INV',
                                                               'price': '-1.00'})
        self.assertIn('price', response.context['form'].errors)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Service.objects.create(name='Invalid', token_prefix='INV', price=Decimal('-1.00'))

    def test_admin_creates_user_with_hashed_password(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('staff_create'), {'username': 'operator2', 'first_name': 'Synthetic',
            'role': 'operator', 'is_active': 'on', 'password': 'Unique-Synthetic!39584'})
        self.assertRedirects(response, reverse('staff_list'))
        user = User.objects.get(username='operator2')
        self.assertTrue(user.check_password('Unique-Synthetic!39584'))
        self.assertNotEqual(user.password, 'Unique-Synthetic!39584')
        self.assertFalse(user.is_superuser)
        self.assertNotIn('Unique-Synthetic!39584', str(AuditEvent.objects.get().changed_fields))

    def test_new_user_password_is_required_and_validated(self):
        self.client.force_login(self.admin)
        for password in ['', '12345678']:
            response = self.client.post(reverse('staff_create'), {'username': 'newuser', 'role': 'operator',
                                                                'password': password, 'is_active': 'on'})
            self.assertIn('password', response.context['form'].errors)
        self.assertFalse(User.objects.filter(username='newuser').exists())

    def test_blank_password_on_edit_preserves_existing_hash(self):
        self.client.force_login(self.admin)
        password_hash = self.operator.password
        response = self.client.post(reverse('staff_edit', args=[self.operator.pk]), {
            'username': 'operator1', 'role': 'operator', 'is_active': 'on', 'password': ''})
        self.assertRedirects(response, reverse('staff_list'))
        self.operator.refresh_from_db()
        self.assertEqual(self.operator.password, password_hash)

    def test_admin_cannot_deactivate_or_demote_own_account(self):
        self.client.force_login(self.admin)
        for values in [{'role': 'operator', 'is_active': 'on'}, {'role': 'admin'}]:
            response = self.client.post(reverse('staff_edit', args=[self.admin.pk]),
                                       {'username': 'administrator', 'password': '', **values})
            self.assertTrue(response.context['form'].non_field_errors())
            self.admin.refresh_from_db()
            self.assertTrue(self.admin.is_active)
            self.assertEqual(self.admin.role, 'admin')

    def test_csrf_protection_blocks_patient_write(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.operator)
        response = client.post(reverse('patient_create'), self.patient_data())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Patient.objects.count(), 1)

    def test_clinical_text_is_escaped_and_patient_pages_are_not_cached(self):
        self.patient.clinical_history = '<script>alert("synthetic")</script>'
        self.patient.save()
        self.client.force_login(self.doctor)
        response = self.client.get(reverse('patient_detail', args=[self.patient.pk]))
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>')
        self.assertIn('no-store', response.headers['Cache-Control'])


class DemoDeploymentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_failed_logins_are_throttled_and_can_resume_after_expiry(self):
        User.objects.create_user('synthetic_login', password='Synthetic-Test-Only!482')
        for _ in range(5):
            response = self.client.post(reverse('login'), {'username': 'synthetic_login', 'password': 'wrong'})
            self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse('login'), {
            'username': 'synthetic_login', 'password': 'Synthetic-Test-Only!482'})
        self.assertEqual(response.status_code, 429)
        self.assertNotIn('_auth_user_id', self.client.session)
        cache.clear()  # Represents expiry of the fifteen-minute attempt window.
        response = self.client.post(reverse('login'), {
            'username': 'synthetic_login', 'password': 'Synthetic-Test-Only!482'})
        self.assertRedirects(response, reverse('dashboard'))

    def test_successful_login_resets_failed_attempts(self):
        User.objects.create_user('synthetic_login', password='Synthetic-Test-Only!482')
        for _ in range(4):
            self.client.post(reverse('login'), {'username': 'synthetic_login', 'password': 'wrong'})
        self.assertEqual(self.client.post(reverse('login'), {
            'username': 'synthetic_login', 'password': 'Synthetic-Test-Only!482'}).status_code, 302)
        self.client.post(reverse('logout'))
        for _ in range(4):
            self.assertEqual(self.client.post(reverse('login'), {
                'username': 'synthetic_login', 'password': 'wrong'}).status_code, 200)

    @override_settings(CLINIC_DEMO_MODE=False)
    def test_demo_bootstrap_refuses_normal_clinic_configuration(self):
        with self.assertRaises(CommandError):
            call_command('bootstrap_demo')
        self.assertFalse(User.objects.exists())

    @override_settings(CLINIC_DEMO_MODE=True)
    def test_demo_bootstrap_requires_a_secure_password(self):
        for password in ['', '12345678']:
            with patch.dict('os.environ', {'CLINIC_DEMO_ADMIN_PASSWORD': password}):
                with self.assertRaises(CommandError):
                    call_command('bootstrap_demo')
        self.assertFalse(User.objects.exists())

    @override_settings(CLINIC_DEMO_MODE=True)
    def test_demo_bootstrap_is_repeatable_and_never_resets_existing_password(self):
        output = StringIO()
        password = 'Synthetic-Demo-Only!954'
        with patch.dict('os.environ', {'CLINIC_DEMO_ADMIN_PASSWORD': password}):
            call_command('bootstrap_demo', stdout=output)
        user = User.objects.get(username='admin')
        self.assertEqual(user.role, 'admin')
        self.assertTrue(user.check_password(password))
        original_hash = user.password
        with patch.dict('os.environ', {'CLINIC_DEMO_ADMIN_PASSWORD': 'Different-Synthetic!934'}):
            call_command('bootstrap_demo', stdout=output)
        user.refresh_from_db()
        self.assertEqual(user.password, original_hash)
        self.assertEqual(User.objects.count(), 1)
        self.assertNotIn(password, output.getvalue())
        self.assertFalse(Patient.objects.exists())
        self.assertFalse(Service.objects.exists())
        self.assertContains(self.client.get(reverse('login')), 'Testing demo.')
