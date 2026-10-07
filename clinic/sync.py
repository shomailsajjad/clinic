"""Single-authority clinic replication with durable revisions and immutable histories."""
import hashlib
import hmac
import json
import os
import uuid
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from datetime import date, datetime
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import (Booking, Clinic, Patient, PatientAlias, RemoteRecord, SyncClock,
                     SyncCredential, SyncOutbox, SyncState, MoneyTransaction)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def authenticated_clinic(request):
    if settings.CLINIC_NODE_MODE != 'central':
        raise PermissionDenied
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    if not token or len(token) > 200:
        raise PermissionDenied
    credential = SyncCredential.objects.select_related('clinic__organization').filter(
        token_hash=token_hash(token), is_active=True, clinic__is_active=True).first()
    if not credential:
        raise PermissionDenied
    return credential


def patient_payload(patient):
    return {'global_id': str(patient.global_id), 'owner_clinic': str(patient.owner_clinic.global_id),
        'name': patient.name, 'date_of_birth': str(patient.date_of_birth) if patient.date_of_birth else None,
        'age': patient.age, 'age_unit': patient.age_unit, 'gender': patient.gender, 'phone': patient.phone,
        'cnic': patient.cnic, 'address': patient.address, 'referring_doctor': patient.referring_doctor,
        'clinical_history': patient.clinical_history}


def visit_payload(booking):
    reports = []
    for item in booking.items.all():
        for report in item.reports.filter(finalized=True):
            reports.append({'id': f'{item.pk}:{report.version}', 'version': report.version,
                'service': item.service_name, 'findings': report.findings, 'impression': report.impression,
                'diagnoses': report.diagnosis_snapshot, 'values': report.values,
                'revision_reason': report.revision_reason, 'created_at': report.created_at.isoformat(),
                'author_id': report.author_id})
    examinations = [{'id': str(row.version), 'version': row.version, 'chief_complaint': row.chief_complaint,
        'history': row.history, 'examination': row.examination, 'vitals': row.vitals,
        'diagnoses': row.diagnosis_snapshot, 'medicines': row.medicines, 'advice': row.advice,
        'follow_up_date': str(row.follow_up_date) if row.follow_up_date else None,
        'revision_reason': row.revision_reason, 'created_at': row.created_at.isoformat(),
        'author_id': row.author_id, 'letterhead': row.clinic_snapshot}
        for row in booking.opd_versions.filter(finalized=True)]
    return {'global_id': str(booking.request_id), 'number': booking.number,
        'patient_id': str(booking.patient.global_id), 'patient': booking.patient_snapshot,
        'clinic': booking.clinic_snapshot or booking.clinic.letterhead(),
        'date': str(booking.scheduled_date), 'time': str(booking.scheduled_time) if booking.scheduled_time else None,
        'status': booking.status, 'kind': booking.kind, 'age_years': str(booking.age_years),
        'referring_doctor': booking.referring_doctor, 'clinical_history': booking.clinical_history,
        'gross': str(booking.gross), 'discount': str(booking.discount), 'due': str(booking.due),
        'items': [{'id': str(item.pk), 'service': item.service_name, 'price': str(item.price),
                   'discount': str(item.discount), 'status': item.status} for item in booking.items.all()],
        'transactions': [{'id': str(row.request_id), 'receipt': row.receipt_number, 'kind': row.kind,
            'amount': str(row.amount), 'method': row.method, 'reference': row.reference, 'reason': row.reason,
            'original': str(row.original.request_id) if row.original else None,
            'created_at': row.created_at.isoformat(), 'actor_id': row.created_by_id, 'snapshot': row.snapshot}
            for row in booking.transactions.order_by('pk')],
        'reports': sorted(reports, key=lambda row: row['id']),
        'examinations': sorted(examinations, key=lambda row: row['version'])}


def outgoing_envelope(outbox):
    if outbox.entity_type == 'patient':
        patient = Patient.objects.select_for_update().get(pk=outbox.entity_id)
        payload = patient_payload(patient)
        patient_id = patient.global_id
    else:
        booking = Booking.objects.select_for_update().get(pk=outbox.entity_id)
        payload = visit_payload(booking)
        patient_id = booking.patient.global_id
    outbox.refresh_from_db()
    return {'entity_type': outbox.entity_type, 'global_id': payload['global_id'],
        'patient_id': str(patient_id), 'version': outbox.version, 'payload': payload}


def validate_envelope(envelope, clinic):
    if not isinstance(envelope, dict) or envelope.get('entity_type') not in ['patient', 'visit']:
        raise ValidationError('Unsupported record type.')
    try:
        global_id, patient_id = uuid.UUID(envelope['global_id']), uuid.UUID(envelope['patient_id'])
        version = envelope['version']
        payload = envelope['payload']
        if type(version) is not int or not 0 < version < 2**63 or not isinstance(payload, dict):
            raise ValueError
        if uuid.UUID(payload['global_id']) != global_id:
            raise ValueError
        if envelope['entity_type'] == 'patient':
            if patient_id != global_id or uuid.UUID(payload['owner_clinic']) != clinic.global_id:
                raise ValueError
            if not isinstance(payload.get('name'), str) or not payload['name'].strip():
                raise ValueError
        else:
            if uuid.UUID(payload['patient_id']) != patient_id or not isinstance(payload.get('date'), str):
                raise ValueError
            date.fromisoformat(payload['date'])
            if payload['clinic']['code'] != clinic.code:
                raise ValueError
            for key in ['gross', 'discount', 'due', 'age_years']:
                number = Decimal(payload[key])
                if not number.is_finite() or number < 0: raise ValueError
            for key in ['transactions', 'reports', 'examinations', 'items']:
                if not isinstance(payload.get(key), list) or len(payload[key]) > 1000:
                    raise ValueError
            seen = set()
            for row in payload['transactions']:
                datetime.fromisoformat(row['created_at'])
                if row['method'] not in MoneyTransaction.Method.values: raise ValueError
                amount = Decimal(row['amount'])
                if not amount.is_finite() or amount <= 0 or row['kind'] not in ['collection', 'refund', 'reversal'] or row['id'] in seen:
                    raise ValueError
                seen.add(row['id'])
            for key in ['reports', 'examinations']:
                ids = [row['id'] for row in payload[key]]
                if len(ids) != len(set(ids)):
                    raise ValueError
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ValidationError('Invalid synchronization record.') from None
    return global_id, patient_id, version, payload


@transaction.atomic
def receive_records(credential, node_id, records):
    if not isinstance(records, list) or len(records) > 100:
        raise ValidationError('Send at most 100 records per batch.')
    try:
        node_id = uuid.UUID(str(node_id))
    except ValueError:
        raise ValidationError('A valid clinic server identity is required.') from None
    credential = SyncCredential.objects.select_for_update().get(pk=credential.pk)
    if credential.node_id and credential.node_id != node_id:
        raise ValidationError('This clinic already has a different authoritative server.')
    if not credential.node_id:
        credential.node_id = node_id
        credential.save(update_fields=['node_id'])
    clinic = credential.clinic
    clock, _ = SyncClock.objects.get_or_create(organization=clinic.organization)
    clock = SyncClock.objects.select_for_update().get(pk=clock.pk)
    accepted = []
    for envelope in records:
        global_id, patient_id, version, payload = validate_envelope(envelope, clinic)
        if RemoteRecord.objects.filter(organization=clinic.organization, entity_type=envelope['entity_type'], global_id=global_id).exclude(clinic=clinic).exists():
            raise ValidationError('This identity is already owned by another source clinic.')
        record = RemoteRecord.objects.filter(clinic=clinic, entity_type=envelope['entity_type'], global_id=global_id).first()
        status = 'accepted'
        if record:
            if record.patient_id != patient_id:
                raise ValidationError('A visit cannot be reassigned to another patient through synchronization.')
            if version == record.version:
                if payload != record.payload:
                    raise ValidationError('The same record revision contains conflicting data.')
                status = 'duplicate'
            elif version < record.version:
                status = 'stale'
            elif envelope['entity_type'] == 'visit':
                for key in ['transactions', 'reports', 'examinations']:
                    incoming = {row['id']: row for row in payload[key]}
                    if any(incoming.get(row['id']) != row for row in record.payload[key]):
                        raise ValidationError('Previously synchronized financial or clinical history cannot be removed or rewritten.')
        if status == 'accepted':
            clock.sequence += 1
            RemoteRecord.objects.update_or_create(clinic=clinic, entity_type=envelope['entity_type'], global_id=global_id,
                defaults={'organization': clinic.organization, 'patient_id': patient_id, 'version': version,
                          'payload': payload, 'received_at': timezone.now(), 'change_number': clock.sequence})
        accepted.append({'entity_type': envelope['entity_type'], 'global_id': str(global_id),
                         'version': version, 'status': status})
    clock.save(update_fields=['sequence'])
    return accepted


def clinic_metadata(clinic):
    return {'global_id': str(clinic.global_id), **clinic.letterhead(), 'is_active': clinic.is_active}


def pull_records(clinic, cursor):
    if type(cursor) is not int or cursor < 0:
        raise ValidationError('Invalid synchronization cursor.')
    clock = SyncClock.objects.filter(organization=clinic.organization).first()
    boundary = clock.sequence if clock else 0
    if cursor > boundary:
        raise ValidationError('Cursor is newer than the central database. Recovery is required.')
    rows = list(RemoteRecord.objects.filter(organization=clinic.organization,
        change_number__gt=cursor, change_number__lte=boundary).exclude(clinic=clinic).order_by('change_number')[:100])
    return {'records': [{'clinic': str(row.clinic.global_id), 'entity_type': row.entity_type,
        'global_id': str(row.global_id), 'patient_id': str(row.patient_id), 'version': row.version,
        'payload': row.payload, 'change_number': row.change_number} for row in rows],
        'cursor': rows[-1].change_number if len(rows) == 100 else boundary,
        'has_more': len(rows) == 100,
        'clinics': [clinic_metadata(row) for row in Clinic.objects.filter(organization=clinic.organization)],
        'aliases': [{'alias': str(row.alias_id), 'canonical': str(row.canonical_id), 'reason': row.reason}
                    for row in PatientAlias.objects.filter(organization=clinic.organization)]}


def validate_sync_url(url):
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ['', '/']:
        raise RuntimeError('Use a central server origin without credentials, query or path.')
    local = parsed.hostname in ['127.0.0.1', 'localhost', '::1']
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and local):
        raise RuntimeError('Synchronization requires HTTPS; loopback HTTP is allowed only for tests.')
    return url.rstrip('/')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def request_json(origin, token, path, data):
    payload = json.dumps(data, ensure_ascii=False).encode()
    request = urllib.request.Request(origin + path, data=payload,
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            content = response.read(10 * 1024 * 1024 + 1)
            if len(content) > 10 * 1024 * 1024:
                raise RuntimeError('Synchronization response is too large.')
            return json.loads(content)
    except (urllib.error.URLError, ValueError, TimeoutError):
        raise RuntimeError('Central server is unavailable or rejected synchronization; queued records remain on this clinic server.') from None


@transaction.atomic
def apply_pull(clinic, response):
    if not isinstance(response, dict) or not isinstance(response.get('records'), list) or not isinstance(response.get('clinics'), list):
        raise RuntimeError('Invalid synchronization response.')
    state, _ = SyncState.objects.select_for_update().get_or_create(clinic=clinic)
    if type(response.get('cursor')) is not int or response['cursor'] < state.cursor:
        raise RuntimeError('Synchronization cursor moved backwards.')
    for metadata in response['clinics']:
        Clinic.objects.update_or_create(global_id=uuid.UUID(metadata['global_id']), defaults={
            'organization': clinic.organization, **{key: metadata[key] for key in
                ['name', 'code', 'address', 'phone', 'doctor_name', 'qualifications', 'prescription_footer', 'is_active']}})
    for item in response['records']:
        source = Clinic.objects.get(global_id=uuid.UUID(item['clinic']), organization=clinic.organization)
        if source.pk == clinic.pk:
            raise RuntimeError('Central server returned an own-clinic record as a foreign cache.')
        global_id, patient_id, version, payload = validate_envelope(item, source)
        existing = RemoteRecord.objects.filter(clinic=source, entity_type=item['entity_type'], global_id=global_id).first()
        if existing and version == existing.version and payload != existing.payload:
            raise RuntimeError('Conflicting cached record revision.')
        if not existing or version > existing.version:
            RemoteRecord.objects.update_or_create(clinic=source, entity_type=item['entity_type'], global_id=global_id,
                defaults={'organization': clinic.organization, 'patient_id': patient_id, 'version': version,
                          'payload': payload, 'received_at': timezone.now(), 'change_number': item['change_number']})
        if item['entity_type'] == 'patient':
            # Refresh a borrowed identity without emitting a source-clinic event.
            Patient.objects.filter(organization=clinic.organization, owner_clinic=source, global_id=global_id).update(
                **{key: payload.get(key) for key in ['name', 'date_of_birth', 'age', 'age_unit', 'gender', 'phone', 'cnic', 'address', 'referring_doctor', 'clinical_history']})
    for item in response.get('aliases', []):
        PatientAlias.objects.update_or_create(organization=clinic.organization, alias_id=uuid.UUID(item['alias']),
            defaults={'canonical_id': uuid.UUID(item['canonical']), 'reason': item['reason'], 'approved_by': None})
    state.cursor = response['cursor']
    state.save(update_fields=['cursor'])


def synchronize_once(clinic, origin=None, token=None):
    origin = validate_sync_url(origin or os.environ.get('CLINIC_SYNC_URL', ''))
    token = token or os.environ.get('CLINIC_SYNC_KEY', '')
    if not token:
        raise RuntimeError('Configure the clinic synchronization key securely on the host.')
    state, _ = SyncState.objects.get_or_create(clinic=clinic)
    try:
        pending = list(SyncOutbox.objects.filter(clinic=clinic, version__gt=F('acknowledged_version')).order_by('pk')[:100])
        sent = []
        for row in pending:
            with transaction.atomic():
                row.refresh_from_db()
                sent.append((row.pk, outgoing_envelope(row)))
        response = request_json(origin, token, '/sync/push/', {'node_id': str(state.node_id), 'records': [item for _, item in sent]})
        if len(response.get('accepted', [])) != len(sent):
            raise RuntimeError('Central server did not acknowledge the complete batch.')
        for (pk, envelope), ack in zip(sent, response['accepted']):
            if any(ack.get(key) != envelope[key] for key in ['entity_type', 'global_id', 'version']) or ack.get('status') not in ['accepted', 'duplicate']:
                raise RuntimeError('Synchronization conflict or stale backup detected. Preserve local records and reconcile the server state.')
            SyncOutbox.objects.filter(pk=pk, acknowledged_version__lt=envelope['version']).update(acknowledged_version=envelope['version'])
        # Pull a bounded number of pages. Later worker cycles continue from the cursor.
        for _ in range(10):
            state.refresh_from_db()
            incoming = request_json(origin, token, '/sync/pull/', {'cursor': state.cursor})
            apply_pull(clinic, incoming)
            if not incoming.get('has_more'):
                break
        state.last_success, state.last_error = timezone.now(), ''
        state.save(update_fields=['last_success', 'last_error'])
        return len(sent)
    except (RuntimeError, ValueError, KeyError, ValidationError):
        state.last_error = 'Sync incomplete. Local work is retained; check connection, credentials or reconciliation conflicts.'
        state.save(update_fields=['last_error'])
        raise


def canonical_patient(organization, patient_id):
    current = uuid.UUID(str(patient_id))
    seen = set()
    for _ in range(50):
        if current in seen:
            raise ValidationError('Patient identity link contains a cycle.')
        seen.add(current)
        alias = PatientAlias.objects.filter(organization=organization, alias_id=current).first()
        if not alias:
            return current
        current = alias.canonical_id
    raise ValidationError('Patient identity chain is too long.')


def identity_group(organization, patient_id):
    canonical = canonical_patient(organization, patient_id)
    identifiers = {canonical, uuid.UUID(str(patient_id))}
    for alias in PatientAlias.objects.filter(organization=organization):
        if canonical_patient(organization, alias.alias_id) == canonical:
            identifiers.add(alias.alias_id)
    return identifiers


@transaction.atomic
def link_patients(actor, alias_id, canonical_id, reason):
    if actor.role != 'owner' or not actor.is_active:
        raise PermissionDenied
    clock, _ = SyncClock.objects.get_or_create(organization=actor.organization)
    SyncClock.objects.select_for_update().get(pk=clock.pk)
    alias = canonical_patient(actor.organization, alias_id)
    canonical = canonical_patient(actor.organization, canonical_id)
    for value in [alias, canonical]:
        if not Patient.objects.filter(organization=actor.organization, global_id=value).exists() and not RemoteRecord.objects.filter(
            organization=actor.organization, entity_type='patient', global_id=value).exists():
            raise ValidationError('Both patient identities must belong to this organisation.')
    if alias == canonical or not reason.strip():
        raise ValidationError('Choose distinct patient identities and enter a reconciliation reason.')
    PatientAlias.objects.create(organization=actor.organization, alias_id=alias, canonical_id=canonical,
                                approved_by=actor, reason=reason)
