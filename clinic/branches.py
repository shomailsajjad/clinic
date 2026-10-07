import json
from decimal import Decimal
from django.utils import timezone
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from .models import Clinic, Booking, OPDVersion, RemoteRecord, SyncState, SyncOutbox, User
from .forms import ClinicForm, OPDForm
from .views import roles_required
from .portal import form_page
from . import workflows, sync

@roles_required(User.Role.DOCTOR)
def opd_edit(request, pk):
    booking = get_object_or_404(Booking.objects.for_user(request.user), pk=pk)
    form = OPDForm(request.POST if request.method == 'POST' else None, previous=booking.opd_versions.first(), user=request.user)
    if request.method == 'POST' and form.is_valid():
        try:
            report = workflows.save_opd(request.user, pk, form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            return redirect('opd_detail', pk=report.pk)
    return form_page(request, form, 'Examination and prescription', 'booking_list')

@roles_required(User.Role.DOCTOR)
def opd_detail(request, pk):
    report = get_object_or_404(OPDVersion.objects.for_user(request.user), pk=pk)
    return render(request, 'clinic/opd_detail.html', {'report': report})

@roles_required(User.Role.DOCTOR)
def opd_print(request, pk):
    report = get_object_or_404(OPDVersion.objects.for_user(request.user), pk=pk, finalized=True)
    return render(request, 'clinic/opd_print.html', {'report': report, 'letterhead': report.clinic_snapshot,
        'superseded': report.booking.opd_versions.filter(finalized=True, version__gt=report.version).exists()})

@roles_required(User.Role.DOCTOR)
def shared_history(request, pk):
    from .models import Patient
    patient = get_object_or_404(Patient.objects.for_user(request.user), pk=pk)
    identifiers = sync.identity_group(request.user.organization, patient.global_id)
    local = Booking.objects.filter(clinic__organization=request.user.organization, patient__global_id__in=identifiers).select_related('clinic', 'patient')
    records = [{'clinic': booking.clinic.name, 'payload': sync.visit_payload(booking)} for booking in local.order_by('-scheduled_date')]
    local_ids = set(local.values_list('request_id', flat=True))
    records += [{'clinic': row.clinic.name, 'payload': row.payload} for row in RemoteRecord.objects.filter(
        organization=request.user.organization, entity_type='visit', patient_id__in=identifiers).exclude(global_id__in=local_ids).select_related('clinic')]
    records.sort(key=lambda row: row['payload']['date'], reverse=True)
    state = SyncState.objects.filter(clinic=request.user.clinic).first()
    return render(request, 'clinic/shared_history.html', {'patient': patient, 'records': records, 'state': state})

@roles_required(User.Role.ADMIN)
def clinic_list(request):
    clinics = Clinic.objects.for_user(request.user)
    if request.user.role != 'owner':
        clinics = clinics.filter(pk=request.user.clinic_id)
    return render(request, 'clinic/clinics.html', {'clinics': clinics})

@roles_required(User.Role.ADMIN)
def clinic_edit(request, pk=None):
    if settings.CLINIC_NODE_MODE == 'branch' or (request.user.role != 'owner' and (pk is None or pk != request.user.clinic_id)):
        raise PermissionDenied
    clinic = get_object_or_404(Clinic.objects.for_user(request.user), pk=pk) if pk else Clinic(organization=request.user.organization)
    form = ClinicForm(request.POST if request.method == 'POST' else None, instance=clinic)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('clinic_list')
    return form_page(request, form, 'Clinic and letterhead', 'clinic_list')

@roles_required(User.Role.ADMIN)
def organization_report(request):
    if request.user.role != 'owner':
        raise PermissionDenied
    from .forms import DateRangeForm
    form = DateRangeForm(request.GET or None)
    rows = []
    if form.is_valid():
        start, end = form.cleaned_data['start'], form.cleaned_data['end']
        for clinic in Clinic.objects.for_user(request.user):
            visits = [sync.visit_payload(row) for row in Booking.objects.filter(clinic=clinic)]
            local_ids = set(Booking.objects.filter(clinic=clinic).values_list('request_id', flat=True))
            visits += [row.payload for row in RemoteRecord.objects.filter(clinic=clinic, entity_type='visit').exclude(global_id__in=local_ids)]
            cash = digital = refunds = Decimal('0')
            diagnoses, ages, referrals = {}, {}, {}
            for visit in visits:
                in_period = str(start) <= visit['date'] <= str(end) and visit['status'] != 'cancelled'
                age = Decimal(visit['age_years'])
                band = 'Under 18' if age < 18 else '18–59' if age < 60 else '60+'
                if in_period: ages[band] = ages.get(band, 0) + 1
                referral = visit['referring_doctor'] or 'Self referral'
                if in_period: referrals[referral] = referrals.get(referral, 0) + 1
                seen = set()
                # Count the latest finalized version of each examination/report only.
                latest = {}
                for record in visit['reports']:
                    key = record['id'].split(':')[0]
                    if key not in latest or record['version'] > latest[key]['version']:
                        latest[key] = record
                for record in list(latest.values()) + visit['examinations'][-1:]:
                    seen.update(item['name'] for item in record['diagnoses'])
                for name in seen if in_period else []:
                    diagnoses[name] = diagnoses.get(name, 0) + 1
                for payment in visit['transactions']:
                    from datetime import datetime
                    payment_date = timezone.localtime(datetime.fromisoformat(payment['created_at'])).date()
                    if not start <= payment_date <= end: continue
                    amount = Decimal(payment['amount'])
                    signed = amount if payment['kind'] == 'collection' else -amount
                    if payment['method'] == 'cash': cash += signed
                    else: digital += signed
                    if payment['kind'] == 'refund': refunds += amount
            rows.append({'clinic': clinic, 'visits': sum(str(start) <= v['date'] <= str(end) and v['status'] != 'cancelled' for v in visits), 'cash': cash, 'digital': digital,
                'refunds': refunds, 'total': cash + digital, 'diagnoses': diagnoses, 'ages': ages, 'referrals': referrals})
    return render(request, 'clinic/organization_report.html', {'form': form, 'rows': rows})

@roles_required(User.Role.ADMIN)
def sync_status(request):
    clinics = Clinic.objects.for_user(request.user)
    if request.user.role != 'owner': clinics = clinics.filter(pk=request.user.clinic_id)
    return render(request, 'clinic/sync_status.html', {'rows': [{'clinic': clinic,
        'state': SyncState.objects.filter(clinic=clinic).first(),
        'pending': SyncOutbox.objects.filter(clinic=clinic, version__gt=F('acknowledged_version')).count(),
        'last_received': RemoteRecord.objects.filter(clinic=clinic).order_by('-received_at').first()} for clinic in clinics]})

@roles_required(User.Role.ADMIN)
def branch_configuration(request, pk):
    if request.user.role != 'owner': raise PermissionDenied
    clinic = get_object_or_404(Clinic.objects.for_user(request.user), pk=pk)
    response = JsonResponse({'organization': {'global_id': str(clinic.organization.global_id), 'name': clinic.organization.name},
        'clinic': {'global_id': str(clinic.global_id), 'code': clinic.code, **clinic.letterhead()}})
    response['Content-Disposition'] = f'attachment; filename="{clinic.code}-configuration.json"'
    return response

@never_cache
@csrf_exempt
@require_POST
def sync_push(request):
    return sync_api(request, True)

@never_cache
@csrf_exempt
@require_POST
def sync_pull(request):
    return sync_api(request, False)

def sync_api(request, push):
    try:
        clinic = sync.authenticated_clinic(request)
        if len(request.body) > 8 * 1024 * 1024:
            return JsonResponse({'error': 'Batch too large.'}, status=413)
        body = json.loads(request.body)
        if not isinstance(body, dict): raise ValueError
        result = {'accepted': sync.receive_records(clinic, body.get('node_id'), body.get('records'))} if push else sync.pull_records(clinic.clinic, body.get('cursor'))
        return JsonResponse(result)
    except PermissionDenied:
        return JsonResponse({'error': 'Unauthorized synchronization.'}, status=403)
    except (ValueError, TypeError, KeyError, ValidationError):
        return JsonResponse({'error': 'Invalid or conflicting synchronization batch.'}, status=400)

@roles_required(User.Role.ADMIN, User.Role.OPERATOR, User.Role.DOCTOR)
@require_POST
@transaction.atomic
def activate_patient(request, global_id):
    from .models import Patient
    if request.user.role == 'owner' or settings.CLINIC_NODE_MODE == 'central': raise PermissionDenied
    remote = get_object_or_404(RemoteRecord, organization=request.user.organization, entity_type='patient', global_id=global_id)
    data = remote.payload
    patient = Patient.objects.filter(organization=request.user.organization, global_id=global_id).first()
    if not patient:
        if data.get('cnic') and Patient.objects.filter(organization=request.user.organization, cnic=data['cnic']).exists():
            from django.contrib import messages
            messages.error(request, 'This CNIC already exists with a different patient identity. Ask the organization administrator to reconcile the records.')
            return redirect('patient_list')
        patient = Patient.objects.create(global_id=global_id, organization=request.user.organization,
            owner_clinic=remote.clinic, created_by=request.user,
            **{key: data.get(key) for key in ['name', 'date_of_birth', 'age', 'age_unit', 'gender', 'phone', 'cnic', 'address', 'referring_doctor', 'clinical_history']})
    return redirect('patient_detail', pk=patient.pk)

@roles_required(User.Role.ADMIN)
def reconcile_patients(request):
    if request.user.role != 'owner': raise PermissionDenied
    from django import forms
    class LinkForm(forms.Form):
        duplicate_patient_uuid = forms.UUIDField()
        retained_patient_uuid = forms.UUIDField()
        reason = forms.CharField(max_length=500)
    form = LinkForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            sync.link_patients(request.user, form.cleaned_data['duplicate_patient_uuid'], form.cleaned_data['retained_patient_uuid'], form.cleaned_data['reason'])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            from django.contrib import messages
            messages.success(request, 'Patient identities linked. Historical clinical and financial records have been preserved.')
            return redirect('patient_list')
    return form_page(request, form, 'Link duplicate patient identities', 'patient_list', 'Link after verifying identity')
