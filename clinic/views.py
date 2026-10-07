from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.utils import timezone
from django.conf import settings

from .forms import PatientForm, ServiceForm, StaffForm
from .models import AuditEvent, Booking, Patient, Service, User
from .access import can_read_local_history, is_owner


def roles_required(*roles):
    def decorator(view):
        @never_cache
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            allowed = request.user.role in roles or (is_owner(request.user) and User.Role.ADMIN in roles)
            if not allowed or (request.user.clinic_id and not request.user.clinic.is_active):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def audit(request, action, record, fields):
    AuditEvent.objects.create(actor=request.user, action=action,
                              record_type=record._meta.model_name,
                              record_id=record.pk, changed_fields=fields)


@roles_required(User.Role.ADMIN, User.Role.OPERATOR, User.Role.DOCTOR)
def dashboard(request):
    return render(request, 'clinic/dashboard.html', {
        'patient_count': Patient.objects.for_user(request.user).count(),
        'service_count': Service.objects.for_user(request.user).filter(is_active=True).count(),
        'recent_patients': Patient.objects.for_user(request.user).all()[:5],
        'today_bookings': Booking.objects.for_user(request.user).filter(scheduled_date=timezone.localdate()).exclude(status='cancelled').count(),
    })


@roles_required(User.Role.ADMIN, User.Role.OPERATOR, User.Role.DOCTOR)
def patient_list(request):
    patients = Patient.objects.for_user(request.user).all()
    query = request.GET.get('q', '').strip()[:160]
    if query:
        matching = Q(name__icontains=query) | Q(phone__icontains=query)
        normalized = query.replace('-', '').replace(' ', '')
        if normalized.isdigit():
            matching |= Q(cnic=normalized)
        number = query.upper().removeprefix('SPC-')
        import uuid
        try:
            matching |= Q(global_id=uuid.UUID(query.removeprefix('P-')))
        except ValueError:
            pass
        if number.isdigit() and len(number) <= 18:
            matching |= Q(pk=int(number))
        patients = patients.filter(matching)
    from .models import RemoteRecord
    remote = RemoteRecord.objects.filter(organization=request.user.organization, entity_type='patient').exclude(global_id__in=Patient.objects.for_user(request.user).values('global_id'))
    if query:
        remote = remote.filter(Q(payload__name__icontains=query) | Q(payload__phone__icontains=query) | Q(payload__cnic=query.replace('-', '')))
    return render(request, 'clinic/patient_list.html', {'directory': remote.select_related('clinic')[:50],
        'page': Paginator(patients, 20).get_page(request.GET.get('page')), 'query': query,
    })


@roles_required(User.Role.ADMIN, User.Role.OPERATOR, User.Role.DOCTOR)
def patient_detail(request, pk):
    patient = get_object_or_404(Patient.objects.for_user(request.user), pk=pk)
    return render(request, 'clinic/patient_detail.html', {'patient': patient,
        'can_read_history': can_read_local_history(request.user, patient.owner_clinic),
        'can_edit_patient': request.user.clinic_id == patient.owner_clinic_id and request.user.role in ['admin', 'operator'],
        'bookings': Booking.objects.for_user(request.user).filter(patient=patient)[:15]})


@roles_required(User.Role.ADMIN, User.Role.OPERATOR)
def patient_edit(request, pk=None):
    patient = get_object_or_404(Patient.objects.for_user(request.user), pk=pk) if pk else None
    if settings.CLINIC_NODE_MODE == 'central' or is_owner(request.user) or (patient and patient.owner_clinic_id != request.user.clinic_id):
        raise PermissionDenied
    form = PatientForm(request.POST or None, instance=patient, user=request.user)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            record = form.save(commit=False)
            if not patient:
                record.created_by = request.user
            record.save()
            audit(request, 'patient.updated' if patient else 'patient.created', record, form.changed_data)
        messages.success(request, 'Patient information saved.')
        return redirect('patient_detail', pk=record.pk)
    return render(request, 'clinic/form.html', {
        'form': form, 'title': 'Edit patient' if patient else 'Register patient',
        'back_url': 'patient_list', 'submit_label': 'Save patient',
    })


@roles_required(User.Role.ADMIN, User.Role.OPERATOR, User.Role.DOCTOR)
def service_list(request):
    return render(request, 'clinic/service_list.html', {'services': Service.objects.for_user(request.user).all()})


@roles_required(User.Role.ADMIN)
def service_edit(request, pk=None):
    service = get_object_or_404(Service.objects.for_user(request.user), pk=pk) if pk else None
    form = ServiceForm(request.POST or None, instance=service, user=request.user)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            record = form.save()
            audit(request, 'service.updated' if service else 'service.created', record, form.changed_data)
        messages.success(request, 'Service saved.')
        return redirect('service_list')
    return render(request, 'clinic/form.html', {
        'form': form, 'title': 'Edit service' if service else 'Add service',
        'back_url': 'service_list', 'submit_label': 'Save service',
    })


@roles_required(User.Role.ADMIN)
def staff_list(request):
    return render(request, 'clinic/staff_list.html', {'staff': User.objects.for_user(request.user).order_by('username')})


@roles_required(User.Role.ADMIN)
def staff_edit(request, pk=None):
    staff = get_object_or_404(User.objects.for_user(request.user), pk=pk) if pk else None
    form = StaffForm(request.POST or None, instance=staff, user=request.user)
    if request.method == 'POST' and form.is_valid():
        if staff and staff.pk == request.user.pk and (
            form.cleaned_data['role'] != request.user.role or not form.cleaned_data['is_active']
        ):
            form.add_error(None, 'You cannot deactivate or remove the admin role from your own account.')
        else:
            with transaction.atomic():
                record = form.save()
                audit(request, 'user.updated' if staff else 'user.created', record, form.changed_data)
            messages.success(request, 'User saved.')
            return redirect('staff_list')
    return render(request, 'clinic/form.html', {
        'form': form, 'title': 'Edit user' if staff else 'Add user',
        'back_url': 'staff_list', 'submit_label': 'Save user',
    })


@roles_required(User.Role.ADMIN)
def audit_list(request):
    events = AuditEvent.objects.for_user(request.user).select_related('actor')
    return render(request, 'clinic/audit_list.html', {
        'page': Paginator(events, 30).get_page(request.GET.get('page')),
    })
