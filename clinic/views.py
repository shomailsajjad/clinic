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

from .forms import PatientForm, ServiceForm, StaffForm
from .models import AuditEvent, Booking, Patient, Service, User


def roles_required(*roles):
    def decorator(view):
        @never_cache
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.user.role not in roles:
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
        'patient_count': Patient.objects.count(),
        'service_count': Service.objects.filter(is_active=True).count(),
        'recent_patients': Patient.objects.all()[:5],
        'today_bookings': Booking.objects.filter(scheduled_date=timezone.localdate()).exclude(status='cancelled').count(),
    })


@roles_required(User.Role.ADMIN, User.Role.OPERATOR, User.Role.DOCTOR)
def patient_list(request):
    patients = Patient.objects.all()
    query = request.GET.get('q', '').strip()[:160]
    if query:
        matching = Q(name__icontains=query) | Q(phone__icontains=query)
        normalized = query.replace('-', '').replace(' ', '')
        if normalized.isdigit():
            matching |= Q(cnic=normalized)
        number = query.upper().removeprefix('SPC-')
        if number.isdigit() and len(number) <= 18:
            matching |= Q(pk=int(number))
        patients = patients.filter(matching)
    return render(request, 'clinic/patient_list.html', {
        'page': Paginator(patients, 20).get_page(request.GET.get('page')), 'query': query,
    })


@roles_required(User.Role.ADMIN, User.Role.OPERATOR, User.Role.DOCTOR)
def patient_detail(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    return render(request, 'clinic/patient_detail.html', {'patient': patient, 'bookings': patient.bookings.all()[:15]})


@roles_required(User.Role.ADMIN, User.Role.OPERATOR)
def patient_edit(request, pk=None):
    patient = get_object_or_404(Patient, pk=pk) if pk else None
    form = PatientForm(request.POST or None, instance=patient)
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
    return render(request, 'clinic/service_list.html', {'services': Service.objects.all()})


@roles_required(User.Role.ADMIN)
def service_edit(request, pk=None):
    service = get_object_or_404(Service, pk=pk) if pk else None
    form = ServiceForm(request.POST or None, instance=service)
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
    return render(request, 'clinic/staff_list.html', {'staff': User.objects.order_by('username')})


@roles_required(User.Role.ADMIN)
def staff_edit(request, pk=None):
    staff = get_object_or_404(User, pk=pk) if pk else None
    form = StaffForm(request.POST or None, instance=staff)
    if request.method == 'POST' and form.is_valid():
        if staff and staff.pk == request.user.pk and (
            form.cleaned_data['role'] != User.Role.ADMIN or not form.cleaned_data['is_active']
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
    events = AuditEvent.objects.select_related('actor')
    return render(request, 'clinic/audit_list.html', {
        'page': Paginator(events, 30).get_page(request.GET.get('page')),
    })
