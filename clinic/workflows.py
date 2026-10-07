"""Transactional clinic operations; roles and financial limits are enforced here."""
import re
import time
from datetime import date
from decimal import Decimal
from functools import wraps

from django.core.exceptions import PermissionDenied, ValidationError
from django.conf import settings
from django.db import OperationalError, connection, transaction
from django.utils import timezone

from .models import (AuditEvent, Booking, BookingItem, DiscountRequest, MoneyTransaction,
                     OPDVersion, ReportVersion, Service, Token, TokenCounter, User)
from .access import require_clinic


def record_event(actor, action, record, fields=()):
    AuditEvent.objects.create(actor=actor, action=action, record_type=record._meta.model_name,
                              record_id=record.pk, changed_fields=list(fields))


def require_role(actor, *roles):
    allowed = actor.role in roles or (actor.role == 'owner' and User.Role.ADMIN in roles)
    if not actor.is_active or not allowed or (actor.clinic_id and not actor.clinic.is_active):
        raise PermissionDenied


def require_visit_write(actor, clinic):
    require_clinic(actor, clinic)
    if settings.CLINIC_NODE_MODE == 'central':
        raise ValidationError('Visits and financial entries are recorded on their originating clinic server.')


def atomic_operation(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        for attempt in range(8):
            try:
                with transaction.atomic():
                    return function(*args, **kwargs)
            except OperationalError as error:
                if connection.vendor != 'sqlite' or 'locked' not in str(error).lower() or attempt == 7:
                    raise
                # Retry the whole rolled-back operation, never a partial posting.
                time.sleep(min(.02 * 2 ** attempt, .5))
    return wrapped


def validate_date(kind, scheduled_date, scheduled_time):
    if kind not in Booking.Kind.values:
        raise ValidationError('Select an appointment or walk-in.')
    today = timezone.localdate()
    if scheduled_date < today:
        raise ValidationError('Choose today or a future date.')
    if kind == Booking.Kind.WALK_IN and scheduled_date != today:
        raise ValidationError('Walk-ins must be booked for today.')
    if kind == Booking.Kind.APPOINTMENT and not scheduled_time:
        raise ValidationError('Set an appointment time.')


def allocate_token(item, scheduled_date, service):
    # The service rows are already locked in ID order. That also serializes the
    # first counter creation for a service/date on PostgreSQL.
    counter, _ = TokenCounter.objects.get_or_create(service=service, date=scheduled_date)
    counter = TokenCounter.objects.select_for_update().get(pk=counter.pk)
    counter.last_number += 1
    counter.save(update_fields=['last_number'])
    return Token.objects.create(item=item, service=service, date=scheduled_date,
                                number=counter.last_number, prefix=service.token_prefix)


@atomic_operation
def create_booking(actor, patient, services, kind, scheduled_date, scheduled_time,
                   request_id, referring_doctor='', clinical_history=''):
    require_role(actor, User.Role.ADMIN, User.Role.OPERATOR)
    existing = Booking.objects.filter(request_id=request_id).first()
    if existing:
        require_clinic(actor, existing.clinic)
        if existing.created_by_id != actor.pk:
            raise PermissionDenied
        return existing
    validate_date(kind, scheduled_date, scheduled_time)
    selected = list(Service.objects.for_user(actor).select_for_update().filter(
        pk__in=[service.pk for service in services]).order_by('pk'))
    if not selected or any(not service.is_active for service in selected):
        raise ValidationError('Choose at least one active service.')
    existing = Booking.objects.filter(request_id=request_id).first()
    if existing:
        require_clinic(actor, existing.clinic)
        if existing.created_by_id != actor.pk:
            raise PermissionDenied
        return existing
    if patient.date_of_birth:
        age = Decimal((scheduled_date - patient.date_of_birth).days) / Decimal('365.2425')
    else:
        divisor = {'years': Decimal(1), 'months': Decimal(12), 'days': Decimal('365.2425')}[patient.age_unit]
        age = Decimal(patient.age) / divisor
    clinic = selected[0].clinic
    if patient.organization_id != actor.organization_id or any(service.clinic_id != clinic.pk for service in selected):
        raise ValidationError('Choose a patient and services from this organisation and a single clinic.')
    require_visit_write(actor, clinic)
    booking = Booking.objects.create(clinic=clinic, clinic_snapshot=clinic.letterhead(),
        request_id=request_id, patient=patient, kind=kind, scheduled_date=scheduled_date,
        scheduled_time=scheduled_time if kind == Booking.Kind.APPOINTMENT else None,
        age_years=age.quantize(Decimal('.01')),
        patient_snapshot={'global_id': str(patient.global_id), 'name': patient.name, 'patient_number': patient.patient_number,
                          'gender': patient.get_gender_display(), 'age': patient.age_display, 'phone': patient.phone},
        referring_doctor=referring_doctor, clinical_history=clinical_history,
        status=Booking.Status.ARRIVED if kind == Booking.Kind.WALK_IN else Booking.Status.BOOKED,
        created_by=actor)
    for service in selected:
        item = BookingItem.objects.create(booking=booking, service=service, service_name=service.name, service_category=service.category,
                                          price=service.price, status=booking.status)
        allocate_token(item, scheduled_date, service)
    record_event(actor, 'booking.created', booking, ['patient', 'services', 'scheduled_date'])
    return booking


@atomic_operation
def reschedule_booking(actor, booking_id, scheduled_date, scheduled_time):
    require_role(actor, User.Role.ADMIN, User.Role.OPERATOR)
    booking = Booking.objects.select_for_update().get(pk=booking_id)
    require_visit_write(actor, booking.clinic)
    if booking.status in [Booking.Status.CANCELLED, Booking.Status.COMPLETED]:
        raise ValidationError('Cancelled or completed bookings cannot be rescheduled.')
    validate_date(Booking.Kind.APPOINTMENT, scheduled_date, scheduled_time)
    if booking.items.filter(reports__isnull=False).exists() or booking.opd_versions.exists():
        raise ValidationError('Bookings with clinical reports cannot be rescheduled.')
    items = list(booking.items.order_by('service_id'))
    services = {service.pk: service for service in Service.objects.select_for_update().filter(
        pk__in=[item.service_id for item in items]).order_by('pk')}
    if scheduled_date != booking.scheduled_date:
        for item in items:
            item.tokens.filter(is_active=True).update(is_active=False)
            allocate_token(item, scheduled_date, services[item.service_id])
    booking.kind = Booking.Kind.APPOINTMENT
    booking.status = Booking.Status.BOOKED
    booking.scheduled_date, booking.scheduled_time = scheduled_date, scheduled_time
    booking.save(update_fields=['kind', 'status', 'scheduled_date', 'scheduled_time'])
    booking.items.update(status=Booking.Status.BOOKED)
    record_event(actor, 'booking.rescheduled', booking, ['scheduled_date', 'scheduled_time', 'tokens'])
    return booking


@atomic_operation
def change_booking_status(actor, booking_id, status):
    require_role(actor, User.Role.ADMIN, User.Role.OPERATOR)
    booking = Booking.objects.select_for_update().get(pk=booking_id)
    require_visit_write(actor, booking.clinic)
    if booking.status in [Booking.Status.CANCELLED, Booking.Status.COMPLETED]:
        raise ValidationError('This booking is already closed.')
    if status == Booking.Status.ARRIVED:
        if booking.scheduled_date != timezone.localdate():
            raise ValidationError('Arrival can only be marked on the scheduled date.')
    elif status == Booking.Status.CANCELLED:
        if booking.items.filter(reports__isnull=False).exists() or booking.opd_versions.exists():
            raise ValidationError('A booking with clinical reports cannot be cancelled.')
        Token.objects.filter(item__booking=booking, is_active=True).update(is_active=False)
    else:
        raise ValidationError('Unsupported status change.')
    booking.status = status
    booking.save(update_fields=['status'])
    booking.items.update(status=status)
    record_event(actor, f'booking.{status}', booking, ['status'])
    return booking


@atomic_operation
def request_discount(actor, item_id, amount, reason):
    require_role(actor, User.Role.ADMIN, User.Role.OPERATOR)
    item = BookingItem.objects.get(pk=item_id)
    booking = Booking.objects.select_for_update().get(pk=item.booking_id)
    require_visit_write(actor, booking.clinic)
    if booking.has_paid or booking.status == Booking.Status.CANCELLED:
        raise ValidationError('Discounts must be approved before collecting payment.')
    if not reason.strip() or amount <= 0 or amount > item.price:
        raise ValidationError('Enter a reason and a discount no greater than the service price.')
    if item.discount_requests.filter(status='pending').exists():
        raise ValidationError('This service already has a pending discount request.')
    record = DiscountRequest.objects.create(item=item, amount=amount, reason=reason,
                                            requested_by=actor)
    record_event(actor, 'discount.requested', record, ['amount', 'reason'])
    return record


@atomic_operation
def review_discount(actor, request_id, approve):
    require_role(actor, User.Role.ADMIN)
    discount = DiscountRequest.objects.get(pk=request_id)
    booking = Booking.objects.select_for_update().get(pk=discount.item.booking_id)
    require_visit_write(actor, booking.clinic)
    discount.refresh_from_db()
    if discount.status != 'pending':
        raise ValidationError('This request has already been reviewed.')
    if approve and (booking.has_paid or booking.status == Booking.Status.CANCELLED):
        raise ValidationError('This booking can no longer receive a discount.')
    if approve:
        discount.item.discount = discount.amount
        discount.item.save(update_fields=['discount'])
    discount.status = 'approved' if approve else 'rejected'
    discount.reviewed_by, discount.reviewed_at = actor, timezone.now()
    discount.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
    record_event(actor, f'discount.{discount.status}', discount, ['status'])
    return discount


def validate_payment_method(method, reference):
    if method not in MoneyTransaction.Method.values:
        raise ValidationError('Select a supported payment method.')
    if method != 'cash' and not reference.strip():
        raise ValidationError('Enter the digital payment receipt/reference number.')


def receipt_snapshot(booking):
    return {'clinic': booking.clinic_snapshot or booking.clinic.letterhead(), 'patient': booking.patient_snapshot, 'booking': booking.number,
            'date': str(booking.scheduled_date), 'gross': str(booking.gross),
            'discount': str(booking.discount), 'due': str(booking.due),
            'items': [{'name': item.service_name, 'price': str(item.price),
                       'discount': str(item.discount),
                       'token': item.current_token.label if item.current_token else 'Cancelled'}
                      for item in booking.items.all()]}


@atomic_operation
def collect_payment(actor, booking_id, amount, method, reference, request_id):
    require_role(actor, User.Role.ADMIN, User.Role.OPERATOR)
    existing = MoneyTransaction.objects.filter(request_id=request_id).first()
    if existing:
        if existing.created_by_id != actor.pk or existing.booking_id != booking_id:
            raise PermissionDenied
        return existing
    booking = Booking.objects.select_for_update().get(pk=booking_id)
    require_visit_write(actor, booking.clinic)
    validate_payment_method(method, reference)
    existing = MoneyTransaction.objects.filter(request_id=request_id).first()
    if existing:
        if existing.created_by_id != actor.pk or existing.booking_id != booking_id:
            raise PermissionDenied
        return existing
    if booking.status == Booking.Status.CANCELLED or booking.has_paid:
        raise ValidationError('This booking is cancelled or has already been paid.')
    if DiscountRequest.objects.filter(item__booking=booking, status='pending').exists():
        raise ValidationError('Wait for admin to review the pending discount before payment.')
    if amount <= 0 or amount != booking.due:
        raise ValidationError(f'Collect the full approved amount: PKR {booking.due:.2f}.')
    record = MoneyTransaction.objects.create(booking=booking, kind='collection', amount=amount,
        method=method, reference=reference.strip(), created_by=actor, request_id=request_id,
        snapshot=receipt_snapshot(booking))
    record_event(actor, 'payment.collected', record, ['amount', 'method', 'reference'])
    return record


@atomic_operation
def refund_payment(actor, original_id, amount, method, reference, reason, request_id):
    require_role(actor, User.Role.ADMIN)
    original = MoneyTransaction.objects.get(pk=original_id)
    require_visit_write(actor, original.booking.clinic)
    Booking.objects.select_for_update().get(pk=original.booking_id)
    existing = MoneyTransaction.objects.filter(request_id=request_id).first()
    if existing:
        if existing.created_by_id != actor.pk or existing.original_id != original_id:
            raise PermissionDenied
        return existing
    validate_payment_method(method, reference)
    refunded = sum((row.amount for row in original.adjustments.exclude(kind='collection')), Decimal('0.00'))
    if original.kind != 'collection' or amount <= 0 or amount > original.amount - refunded:
        raise ValidationError('Refund exceeds the remaining refundable amount.')
    if not reason.strip():
        raise ValidationError('Enter the refund reason.')
    record = MoneyTransaction.objects.create(booking=original.booking, original=original,
        kind='refund', amount=amount, method=method, reference=reference.strip(), reason=reason,
        request_id=request_id, created_by=actor, snapshot=original.snapshot)
    record_event(actor, 'payment.refunded', record, ['amount', 'method', 'reason'])
    return record


@atomic_operation
def correct_payment(actor, original_id, method, reference, reason, request_id):
    require_role(actor, User.Role.ADMIN)
    original = MoneyTransaction.objects.get(pk=original_id)
    require_visit_write(actor, original.booking.clinic)
    Booking.objects.select_for_update().get(pk=original.booking_id)
    existing = MoneyTransaction.objects.filter(request_id=request_id).first()
    if existing:
        if existing.created_by_id != actor.pk or existing.original_id != original_id:
            raise PermissionDenied
        return existing
    validate_payment_method(method, reference)
    if original.kind != 'collection' or original.adjustments.exists():
        raise ValidationError('Only an unrefunded, uncorrected collection can be corrected.')
    if not reason.strip():
        raise ValidationError('Enter the correction reason.')
    MoneyTransaction.objects.create(booking=original.booking, original=original, kind='reversal',
        amount=original.amount, method=original.method, reference=original.reference,
        reason=reason, created_by=actor, snapshot=original.snapshot)
    replacement = MoneyTransaction.objects.create(booking=original.booking, original=original,
        kind='collection', amount=original.amount, method=method, reference=reference.strip(),
        reason=reason, created_by=actor, request_id=request_id, snapshot=original.snapshot)
    record_event(actor, 'payment.corrected', replacement, ['method', 'reference', 'reason'])
    return replacement


def expand_placeholders(text, definitions, values):
    for field in definitions:
        text = text.replace(f'[{field["label"]}]', str(values.get(field['name'], '')))
    if re.search(r'\[[^\]\n]+\]', text):
        raise ValidationError('Resolve all measurement placeholders before saving the report.')
    return text


@atomic_operation
def save_report(actor, item_id, template, values, findings, impression, diagnoses,
                finalized, revision_reason, base_version, template_version):
    require_role(actor, User.Role.DOCTOR)
    if not findings.strip() or not impression.strip() or not diagnoses:
        raise ValidationError('Enter findings, an impression and at least one diagnosis.')
    item = BookingItem.objects.get(pk=item_id)
    booking = Booking.objects.select_for_update().get(pk=item.booking_id)
    require_visit_write(actor, booking.clinic)
    if any(row.organization_id != actor.organization_id for row in diagnoses):
        raise PermissionDenied
    if booking.status == Booking.Status.CANCELLED:
        raise ValidationError('Cannot report on a cancelled booking.')
    template.refresh_from_db()
    if not template.is_active or template.service_id != item.service_id or template.version != template_version:
        raise ValidationError('The template changed or does not match this service. Reload the report form.')
    previous = item.reports.first()
    if (previous.version if previous else 0) != base_version:
        raise ValidationError('Another report version was saved. Reload to avoid overwriting it.')
    if previous and previous.finalized and (not finalized or not revision_reason.strip()):
        raise ValidationError('Revisions of finalized reports need a reason and must be finalized.')
    rendered_findings = expand_placeholders(findings, template.fields, values)
    rendered_impression = expand_placeholders(impression, template.fields, values)
    record = ReportVersion.objects.create(item=item, version=base_version + 1, previous=previous,
        template=template, template_snapshot={'name': template.name, 'version': template.version,
        'fields': template.fields, 'findings': template.findings, 'impression': template.impression,
        'source_findings': findings, 'source_impression': impression},
        patient_snapshot=booking.patient_snapshot, values=values, findings=rendered_findings,
        impression=rendered_impression, diagnosis_snapshot=[{'name': row.name, 'code': row.code} for row in diagnoses],
        finalized=finalized, revision_reason=revision_reason, author=actor)
    record.diagnoses.set(diagnoses)
    if finalized:
        item.status = Booking.Status.COMPLETED
        item.save(update_fields=['status'])
        if not booking.items.exclude(status=Booking.Status.COMPLETED).exists():
            booking.status = Booking.Status.COMPLETED
            booking.save(update_fields=['status'])
    record_event(actor, 'report.finalized' if finalized else 'report.draft_saved', record, ['findings', 'impression', 'diagnoses'])
    return record


@atomic_operation
def save_opd(actor, booking_id, data):
    require_role(actor, User.Role.DOCTOR)
    booking = Booking.objects.select_for_update().get(pk=booking_id)
    require_visit_write(actor, booking.clinic)
    if booking.status == Booking.Status.CANCELLED:
        raise ValidationError('Cancelled visits cannot be examined.')
    if not booking.items.filter(service_category='opd').exists():
        raise ValidationError('This booking has no doctor consultation service.')
    previous = booking.opd_versions.first()
    if (previous.version if previous else 0) != data['base_version']:
        raise ValidationError('Another examination version was saved. Reload before continuing.')
    if previous and previous.finalized and (not data['finalized'] or not data['revision_reason'].strip()):
        raise ValidationError('A finalized prescription revision needs a reason and must be finalized.')
    if any(row.organization_id != actor.organization_id for row in data['diagnoses']):
        raise PermissionDenied
    vitals = {key: str(data[key]) for key in ['blood_pressure', 'pulse', 'temperature', 'weight']
              if data.get(key) not in [None, '']}
    record = OPDVersion.objects.create(booking=booking, previous=previous, version=data['base_version'] + 1,
        chief_complaint=data['chief_complaint'], history=data['history'], examination=data['examination'],
        vitals=vitals, medicines=data['prescription'], advice=data['advice'], follow_up_date=data['follow_up_date'],
        finalized=data['finalized'], revision_reason=data['revision_reason'],
        diagnosis_snapshot=[{'name': row.name, 'code': row.code} for row in data['diagnoses']],
        patient_snapshot=booking.patient_snapshot, clinic_snapshot=booking.clinic_snapshot or booking.clinic.letterhead(),
        author=actor)
    record.diagnoses.set(data['diagnoses'])
    if record.finalized:
        booking.items.filter(service_category='opd').update(status=Booking.Status.COMPLETED)
        if not booking.items.exclude(status=Booking.Status.COMPLETED).exists():
            booking.status = Booking.Status.COMPLETED
            booking.save(update_fields=['status'])
    record_event(actor, 'opd.finalized' if record.finalized else 'opd.draft_saved', record,
                 ['examination', 'diagnoses', 'prescription'])
    return record
