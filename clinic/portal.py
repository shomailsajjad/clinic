import csv
from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Max, Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import workflows
from .forms import (BookingForm, CorrectionForm, DateRangeForm, DiagnosisForm, DiscountForm,
                    PaymentForm, RefundForm, ReportForm, RescheduleForm, TemplateForm)
from .models import (Booking, BookingItem, Diagnosis, DiscountRequest, MoneyTransaction,
                     Patient, ReportTemplate, ReportVersion, Token, User)
from .views import roles_required

FRONT_DESK = (User.Role.ADMIN, User.Role.OPERATOR)
ALL_ROLES = (*FRONT_DESK, User.Role.DOCTOR)


def form_page(request, form, title, back_url, submit='Save'):
    return render(request, 'clinic/form.html', {'form': form, 'title': title,
                  'back_url': back_url, 'submit_label': submit})


@roles_required(*ALL_ROLES)
def booking_list(request):
    bookings = Booking.objects.select_related('patient', 'created_by').prefetch_related('items__tokens')
    date_value = request.GET.get('date', '')
    query = request.GET.get('q', '').strip()[:160]
    if date_value:
        from datetime import date
        try:
            bookings = bookings.filter(scheduled_date=date.fromisoformat(date_value))
        except ValueError:
            messages.error(request, 'Use a valid booking date.')
    if query:
        bookings = bookings.filter(Q(patient__name__icontains=query) | Q(patient__phone__icontains=query))
    return render(request, 'clinic/booking_list.html', {
        'page': Paginator(bookings, 20).get_page(request.GET.get('page')), 'query': query, 'date_value': date_value})


@roles_required(*FRONT_DESK)
def booking_create(request):
    initial = {}
    patient_id = request.GET.get('patient')
    if patient_id:
        patient = get_object_or_404(Patient, pk=patient_id)
        initial = {'patient': patient, 'referring_doctor': patient.referring_doctor,
                   'clinical_history': patient.clinical_history}
    form = BookingForm(request.POST if request.method == 'POST' else None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            booking = workflows.create_booking(request.user, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Booking saved and service tokens issued.')
            return redirect('booking_detail', pk=booking.pk)
    return form_page(request, form, 'Book patient', 'booking_list', 'Book & issue tokens')


@roles_required(*ALL_ROLES)
def booking_detail(request, pk):
    booking = get_object_or_404(Booking.objects.select_related('patient', 'created_by'), pk=pk)
    items = list(booking.items.select_related('service').prefetch_related('tokens', 'reports'))
    for item in items:
        item.latest_report = item.reports.first()
    return render(request, 'clinic/booking_detail.html', {
        'booking': booking, 'items': items,
        'transactions': booking.transactions.select_related('created_by') if request.user.role != 'doctor' else [],
        'discount_requests': DiscountRequest.objects.filter(item__booking=booking).select_related('item', 'requested_by'),
    })


@roles_required(*FRONT_DESK)
def booking_reschedule(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    form = RescheduleForm(request.POST if request.method == 'POST' else None,
                          initial={'scheduled_date': booking.scheduled_date, 'scheduled_time': booking.scheduled_time})
    if request.method == 'POST' and form.is_valid():
        try:
            workflows.reschedule_booking(request.user, pk, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Appointment rescheduled. Date changes receive new tokens.')
            return redirect('booking_detail', pk=pk)
    return form_page(request, form, f'Reschedule {booking.number}', 'booking_list', 'Reschedule')


@roles_required(*FRONT_DESK)
@require_POST
def booking_status(request, pk):
    get_object_or_404(Booking, pk=pk)
    try:
        workflows.change_booking_status(request.user, pk, request.POST.get('status'))
    except ValidationError as error:
        messages.error(request, '; '.join(error.messages))
    else:
        messages.success(request, 'Booking status updated. Cancellation does not automatically refund a payment.')
    return redirect('booking_detail', pk=pk)


@roles_required(*ALL_ROLES)
def queue(request):
    tokens = Token.objects.filter(date=timezone.localdate(), is_active=True).select_related(
        'service', 'item__booking__patient').order_by('service__name', 'number')
    service = request.GET.get('service', '')
    if service.isdigit():
        tokens = tokens.filter(service_id=int(service))
    from .models import Service
    return render(request, 'clinic/queue.html', {'tokens': tokens, 'services': Service.objects.filter(is_active=True),
                                               'selected_service': service, 'today': timezone.localdate()})


@roles_required(*FRONT_DESK)
def token_print(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    return render(request, 'clinic/token_print.html', {'booking': booking, 'items': booking.items.all()})


@roles_required(*FRONT_DESK)
def discount_request(request, pk):
    item = get_object_or_404(BookingItem, pk=pk)
    form = DiscountForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            workflows.request_discount(request.user, pk, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Discount request sent to admin.')
            return redirect('booking_detail', pk=item.booking_id)
    return form_page(request, form, f'Request discount · {item.service_name}', 'booking_list', 'Request approval')


@roles_required(User.Role.ADMIN)
def discount_list(request):
    records = DiscountRequest.objects.select_related('item__booking__patient', 'requested_by', 'reviewed_by').order_by('-created_at')
    return render(request, 'clinic/discount_list.html', {'page': Paginator(records, 30).get_page(request.GET.get('page'))})


@roles_required(User.Role.ADMIN)
@require_POST
def discount_review(request, pk):
    get_object_or_404(DiscountRequest, pk=pk)
    action = request.POST.get('action')
    if action not in ['approve', 'reject']:
        messages.error(request, 'Choose approve or reject.')
    else:
        try:
            workflows.review_discount(request.user, pk, approve=action == 'approve')
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
        else:
            messages.success(request, 'Discount decision saved.')
    return redirect('discount_list')


@roles_required(*FRONT_DESK)
def payment_create(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    form = PaymentForm(request.POST if request.method == 'POST' else None, initial={'amount': booking.due})
    if request.method == 'POST' and form.is_valid():
        try:
            record = workflows.collect_payment(request.user, pk, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Payment recorded. Receipt is ready.')
            return redirect('receipt', pk=record.pk)
    return form_page(request, form, f'Collect payment · {booking.number}', 'booking_list', 'Record payment')


@roles_required(User.Role.ADMIN)
def payment_refund(request, pk):
    original = get_object_or_404(MoneyTransaction, pk=pk)
    form = RefundForm(request.POST if request.method == 'POST' else None, initial={'method': original.method})
    if request.method == 'POST' and form.is_valid():
        try:
            record = workflows.refund_payment(request.user, pk, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Refund recorded.')
            return redirect('receipt', pk=record.pk)
    return form_page(request, form, f'Refund · {original.receipt_number}', 'booking_list', 'Record refund')


@roles_required(User.Role.ADMIN)
def payment_correct(request, pk):
    original = get_object_or_404(MoneyTransaction, pk=pk)
    form = CorrectionForm(request.POST if request.method == 'POST' else None,
                          initial={'method': original.method, 'reference': original.reference})
    if request.method == 'POST' and form.is_valid():
        try:
            record = workflows.correct_payment(request.user, pk, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Correction saved with original receipt preserved.')
            return redirect('receipt', pk=record.pk)
    return form_page(request, form, f'Correct payment method/reference · {original.receipt_number}', 'booking_list', 'Save correction')


@roles_required(*FRONT_DESK)
def receipt(request, pk):
    record = get_object_or_404(MoneyTransaction.objects.select_related('created_by', 'original'), pk=pk)
    return render(request, 'clinic/receipt.html', {'record': record,
        'is_reversed': record.adjustments.filter(kind='reversal').exists()})


def range_data(request):
    today = timezone.localdate().isoformat()
    form = DateRangeForm({'start': request.GET.get('start', today), 'end': request.GET.get('end', today)})
    return form, form.is_valid()


@roles_required(User.Role.ADMIN)
def cash_report(request):
    form, valid = range_data(request)
    rows = MoneyTransaction.objects.none()
    if valid:
        rows = MoneyTransaction.objects.filter(created_at__date__gte=form.cleaned_data['start'],
            created_at__date__lte=form.cleaned_data['end']).select_related('created_by', 'booking__patient')
    totals = {'collection': Decimal('0.00'), 'refund': Decimal('0.00'), 'reversal': Decimal('0.00')}
    methods, operators = {}, {}
    for row in rows:
        totals[row.kind] += row.amount
        sign = 1 if row.kind == 'collection' else -1
        methods[row.method] = methods.get(row.method, Decimal('0.00')) + sign * row.amount
        operators[row.created_by.username] = operators.get(row.created_by.username, Decimal('0.00')) + sign * row.amount
    net = totals['collection'] - totals['refund'] - totals['reversal']
    if request.GET.get('export') == 'csv' and valid:
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="cash-ledger.csv"'
        writer = csv.writer(response)
        writer.writerow(['Time (Pakistan)', 'Receipt', 'Kind', 'PKR', 'Method', 'Reference', 'User', 'Booking'])
        for row in rows:
            writer.writerow([safe_csv(value) for value in [timezone.localtime(row.created_at).isoformat(),
                row.receipt_number, row.get_kind_display(), row.amount, row.get_method_display(), row.reference,
                row.created_by.username, row.booking.number]])
        return response
    return render(request, 'clinic/cash_report.html', {'form': form, 'rows': rows, 'totals': totals,
        'net': net, 'methods': methods.items(), 'operators': operators.items()})


@roles_required(User.Role.ADMIN)
def diagnosis_list(request):
    return render(request, 'clinic/diagnosis_list.html', {'diagnoses': Diagnosis.objects.all()})


@roles_required(User.Role.ADMIN)
def diagnosis_edit(request, pk=None):
    diagnosis = get_object_or_404(Diagnosis, pk=pk) if pk else None
    form = DiagnosisForm(request.POST if request.method == 'POST' else None, instance=diagnosis)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            record = form.save()
            workflows.record_event(request.user, 'diagnosis.updated', record, form.changed_data)
        return redirect('diagnosis_list')
    return form_page(request, form, 'Edit diagnosis' if diagnosis else 'Add diagnosis', 'diagnosis_list')


@roles_required(User.Role.ADMIN)
def template_list(request):
    return render(request, 'clinic/template_list.html', {'templates': ReportTemplate.objects.select_related('service')})


@roles_required(User.Role.ADMIN)
def template_edit(request, pk=None):
    template = get_object_or_404(ReportTemplate, pk=pk) if pk else None
    form = TemplateForm(request.POST if request.method == 'POST' else None, instance=template)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            latest = ReportTemplate.objects.select_for_update().get(pk=pk) if pk else None
            if latest and latest.version != form.cleaned_data['base_version']:
                form.add_error(None, 'This template changed. Reload before editing.')
            else:
                record = form.save(commit=False)
                record.version = latest.version + 1 if latest else 1
                record.save()
                workflows.record_event(request.user, 'template.updated', record, form.changed_data)
                messages.success(request, 'Template saved. Existing reports retain their original template snapshot.')
                return redirect('template_list')
    return form_page(request, form, 'Edit report template' if template else 'Add report template', 'template_list')


@roles_required(User.Role.DOCTOR)
def report_edit(request, pk):
    item = get_object_or_404(BookingItem.objects.select_related('booking__patient', 'service'), pk=pk)
    templates = ReportTemplate.objects.filter(service=item.service, is_active=True)
    template_id = request.GET.get('template')
    previous = item.reports.first()
    if not template_id and previous and previous.template.is_active:
        template_id = str(previous.template_id)
    if not template_id:
        return render(request, 'clinic/template_choose.html', {'item': item, 'templates': templates})
    template = get_object_or_404(templates, pk=template_id)
    form = ReportForm(request.POST if request.method == 'POST' else None, template=template, previous=previous)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data.copy()
        try:
            report = workflows.save_report(request.user, pk, template, form.measurement_values(),
                data['findings'], data['impression'], data['diagnoses'], data['finalized'],
                data['revision_reason'], data['base_version'], data['template_version'])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Report version saved. Previous versions are preserved.')
            return redirect('report_detail', pk=report.pk)
    return form_page(request, form, f'{item.service_name} report · {item.booking.patient.name}', 'booking_list', 'Save report version')


@roles_required(User.Role.DOCTOR)
def report_detail(request, pk):
    report = get_object_or_404(ReportVersion.objects.select_related('item__booking', 'author'), pk=pk)
    return render(request, 'clinic/report_detail.html', {'report': report, 'history': report.item.reports.all()})


@roles_required(User.Role.DOCTOR)
def report_print(request, pk):
    report = get_object_or_404(ReportVersion.objects.select_related('item__booking', 'author'), pk=pk, finalized=True)
    return render(request, 'clinic/report_print.html', {'report': report,
        'superseded': report.item.reports.filter(version__gt=report.version, finalized=True).exists()})


def safe_csv(value):
    value = str(value)
    return "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value


@roles_required(User.Role.ADMIN)
def patient_reports(request):
    form, valid = range_data(request)
    bookings = Booking.objects.none()
    if valid:
        bookings = Booking.objects.exclude(status='cancelled').filter(
            scheduled_date__gte=form.cleaned_data['start'], scheduled_date__lte=form.cleaned_data['end'])
    latest_ids = ReportVersion.objects.filter(finalized=True).values('item_id').annotate(latest=Max('pk')).values_list('latest', flat=True)
    diagnosis_id = request.GET.get('diagnosis', '')
    service_id = request.GET.get('service', '')
    referral = request.GET.get('referral', '').strip()[:160]
    if service_id.isdigit():
        bookings = bookings.filter(items__service_id=int(service_id))
    if diagnosis_id.isdigit():
        bookings = bookings.filter(items__reports__pk__in=latest_ids, items__reports__diagnoses__pk=int(diagnosis_id))
    if referral:
        bookings = bookings.filter(referring_doctor__icontains=referral)
    bookings = bookings.distinct().select_related('patient')
    if request.GET.get('export') == 'csv' and valid:
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="patient-report.csv"'
        writer = csv.writer(response)
        writer.writerow(['Booking', 'Patient number', 'Patient', 'Age at visit (years)', 'Date', 'Referring doctor'])
        for booking in bookings:
            writer.writerow([safe_csv(value) for value in [booking.number, booking.patient.patient_number,
                booking.patient_snapshot['name'], booking.age_years, booking.scheduled_date, booking.referring_doctor]])
        return response
    ages = {'Under 1': 0, '1–17': 0, '18–39': 0, '40–59': 0, '60+': 0}
    referrals = defaultdict(int)
    patients = set()
    for booking in bookings:
        age = booking.age_years
        bucket = 'Under 1' if age < 1 else '1–17' if age < 18 else '18–39' if age < 40 else '40–59' if age < 60 else '60+'
        ages[bucket] += 1
        referrals[booking.referring_doctor or 'Not provided'] += 1
        patients.add(booking.patient_id)
    diseases = defaultdict(set)
    for report in ReportVersion.objects.filter(pk__in=latest_ids, item__booking__in=bookings).prefetch_related('diagnoses'):
        for diagnosis in report.diagnoses.all():
            diseases[diagnosis.name].add(report.item.booking_id)
    service_counts = defaultdict(int)
    for item in BookingItem.objects.filter(booking__in=bookings):
        service_counts[item.service_name] += 1
    from .models import Service
    return render(request, 'clinic/patient_reports.html', {'form': form, 'page': Paginator(bookings, 30).get_page(request.GET.get('page')),
        'ages': ages.items(), 'referrals': sorted(referrals.items()), 'diseases': [(name, len(ids)) for name, ids in sorted(diseases.items())],
        'patient_count': len(patients), 'booking_count': bookings.count(), 'diagnoses': Diagnosis.objects.all(),
        'service_counts': sorted(service_counts.items()),
        'services': Service.objects.all(), 'diagnosis_id': diagnosis_id, 'service_id': service_id, 'referral': referral})


@roles_required(User.Role.ADMIN)
def backup_page(request):
    from django.conf import settings
    from datetime import datetime
    directory = settings.RUNTIME_DIR / 'backups'
    files = sorted(directory.glob('clinic-backup-*.zip'), key=lambda path: path.stat().st_mtime, reverse=True)[:10] if directory.exists() else []
    backups = [{'name': path.name, 'created': datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.get_current_timezone()),
                'size': path.stat().st_size} for path in files]
    return render(request, 'clinic/backup.html', {'backups': backups})


@roles_required(User.Role.ADMIN)
@require_POST
def backup_download(request):
    from .backup import create_backup
    try:
        path = create_backup()
    except (RuntimeError, OSError) as error:
        messages.error(request, 'Backup could not be created. Check database tools and available disk space.')
        return redirect('backup_page')
    workflows.record_event(request.user, 'backup.created', request.user, ['database'])
    return FileResponse(path.open('rb'), as_attachment=True, filename=path.name)
