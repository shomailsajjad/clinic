import re
import uuid
from decimal import Decimal

from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (Booking, Diagnosis, MoneyTransaction, Patient, ReportTemplate, Service, User)


class PatientForm(forms.ModelForm):
    cnic = forms.CharField(max_length=32, required=False, label='CNIC (optional)',
                           help_text='13 digits; hyphens are accepted.')

    class Meta:
        model = Patient
        fields = ['name', 'date_of_birth', 'age', 'age_unit', 'gender', 'phone', 'cnic',
                  'address', 'referring_doctor', 'clinical_history']
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'address': forms.Textarea(attrs={'rows': 2}),
            'clinical_history': forms.Textarea(attrs={'rows': 4}),
        }
        labels = {'cnic': 'CNIC (optional)', 'age': 'Age, if date of birth is unknown'}
        help_texts = {'cnic': '13 digits; hyphens are accepted.',
                      'date_of_birth': 'Enter the actual date only; otherwise use age and unit.'}

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if not name:
            raise ValidationError('Enter a patient name.')
        return name

    def clean_cnic(self):
        cnic = re.sub(r'[\s-]', '', self.cleaned_data.get('cnic') or '')
        return cnic or None

    def clean(self):
        data = super().clean()
        dob, age, unit = data.get('date_of_birth'), data.get('age'), data.get('age_unit')
        if dob and dob > timezone.localdate():
            self.add_error('date_of_birth', 'Date of birth cannot be in the future.')
        if not dob and age is None:
            self.add_error('age', 'Enter a date of birth or an age.')
        if age is not None:
            limits = {'years': 130, 'months': 1560, 'days': 47483}
            if age > limits.get(unit, 130):
                self.add_error('age', 'Age exceeds the supported range of 130 years.')
        if dob:
            data['age'] = None
        return data


class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ['name', 'token_prefix', 'price', 'is_active']
        labels = {'price': 'Standard price (PKR)'}

    def clean_token_prefix(self):
        return self.cleaned_data['token_prefix'].strip().upper()


class StaffForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, required=False,
                               help_text='Required for new accounts. Leave blank to keep an existing password.')

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'role', 'is_active']

    def clean_password(self):
        password = self.cleaned_data.get('password')
        if not self.instance.pk and not password:
            raise ValidationError('Set a password for this account.')
        if password:
            candidate = User(username=self.data.get('username', ''),
                             first_name=self.data.get('first_name', ''),
                             last_name=self.data.get('last_name', ''))
            password_validation.validate_password(password, candidate)
        return password

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get('password'):
            user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
        return user


class PricedServiceChoice(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.name} · PKR {obj.price:.2f}'


class BookingForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    patient = forms.ModelChoiceField(queryset=Patient.objects.all())
    services = PricedServiceChoice(queryset=Service.objects.filter(is_active=True),
                                  widget=forms.CheckboxSelectMultiple)
    kind = forms.ChoiceField(choices=Booking.Kind.choices, initial=Booking.Kind.WALK_IN)
    scheduled_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), initial=timezone.localdate)
    scheduled_time = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'type': 'time'}),
                                    help_text='Required for an appointment; leave blank for a walk-in.')
    referring_doctor = forms.CharField(max_length=160, required=False)
    clinical_history = forms.CharField(max_length=5000, required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def clean(self):
        data = super().clean()
        from .workflows import validate_date
        if data.get('kind') and data.get('scheduled_date'):
            try:
                validate_date(data['kind'], data['scheduled_date'], data.get('scheduled_time'))
            except ValidationError as error:
                raise ValidationError(error.messages)
        return data


class RescheduleForm(forms.Form):
    scheduled_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    scheduled_time = forms.TimeField(widget=forms.TimeInput(attrs={'type': 'time'}))


class DiscountForm(forms.Form):
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('.01'), label='Requested discount (PKR)')
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={'rows': 2}))


class PaymentForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('.01'), label='Amount collected (PKR)')
    method = forms.ChoiceField(choices=MoneyTransaction.Method.choices)
    reference = forms.CharField(max_length=120, required=False, label='Digital receipt / reference number')

    def clean(self):
        data = super().clean()
        if data.get('method') != 'cash' and not data.get('reference', '').strip():
            self.add_error('reference', 'A reference number is required for digital payments.')
        return data


class RefundForm(PaymentForm):
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={'rows': 2}))
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('.01'), label='Refund amount (PKR)')


class CorrectionForm(PaymentForm):
    amount = None
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={'rows': 2}),
                             help_text='The original collection is reversed and a corrected receipt is issued.')


class DiagnosisForm(forms.ModelForm):
    class Meta:
        model = Diagnosis
        fields = ['name', 'code', 'is_active']
        labels = {'code': 'Disease code (optional)'}


class TemplateForm(forms.ModelForm):
    measurement_fields = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 4}),
        label='Measurements / variables',
        help_text='One per line: Liver size | cm. Use [Liver size] in the findings or impression to insert its value.')
    base_version = forms.IntegerField(widget=forms.HiddenInput, initial=0)

    class Meta:
        model = ReportTemplate
        fields = ['name', 'service', 'disease_group', 'measurement_fields', 'findings', 'impression', 'is_active']
        widgets = {'findings': forms.Textarea(attrs={'rows': 6}), 'impression': forms.Textarea(attrs={'rows': 4})}
        labels = {'disease_group': 'Disease / examination group'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial['measurement_fields'] = '\n'.join(
                f'{field["label"]} | {field.get("unit", "")}' for field in self.instance.fields)
            self.initial['base_version'] = self.instance.version

    def clean_measurement_fields(self):
        from django.utils.text import slugify
        definitions = []
        names = set()
        for line in self.cleaned_data['measurement_fields'].splitlines():
            if not line.strip():
                continue
            label, _, unit = line.partition('|')
            label, unit = label.strip(), unit.strip()
            name = slugify(label).replace('-', '_')
            if not name or name in names or len(label) > 80 or len(unit) > 20 or '[' in label or ']' in label:
                raise ValidationError('Use distinct measurement names of up to 80 characters, with no brackets.')
            names.add(name)
            definitions.append({'name': name, 'label': label, 'unit': unit})
        if len(definitions) > 40:
            raise ValidationError('Use at most 40 measurements per template.')
        return definitions

    def clean(self):
        data = super().clean()
        labels = {field['label'] for field in data.get('measurement_fields', [])}
        for field_name in ['findings', 'impression']:
            for placeholder in re.findall(r'\[([^\]\n]+)\]', data.get(field_name, '')):
                if placeholder not in labels:
                    self.add_error(field_name, f'Add a measurement named {placeholder} or remove its placeholder.')
        return data

    def save(self, commit=True):
        template = super().save(commit=False)
        template.fields = self.cleaned_data['measurement_fields']
        if commit:
            template.save()
        return template


class ReportForm(forms.Form):
    base_version = forms.IntegerField(widget=forms.HiddenInput)
    template_version = forms.IntegerField(widget=forms.HiddenInput)
    findings = forms.CharField(max_length=20000, widget=forms.Textarea(attrs={'rows': 7}))
    impression = forms.CharField(max_length=10000, widget=forms.Textarea(attrs={'rows': 4}))
    diagnoses = forms.ModelMultipleChoiceField(queryset=Diagnosis.objects.filter(is_active=True),
        widget=forms.CheckboxSelectMultiple, help_text='Select structured diagnoses for disease-wise reports.')
    finalized = forms.BooleanField(required=False, label='Finalize report for printing')
    revision_reason = forms.CharField(required=False, max_length=500)

    def __init__(self, *args, template, previous=None, **kwargs):
        self.template = template
        super().__init__(*args, **kwargs)
        self.initial.update({'template_version': template.version,
            'base_version': previous.version if previous else 0,
            'findings': previous.template_snapshot.get('source_findings', previous.findings) if previous else template.findings,
            'impression': previous.template_snapshot.get('source_impression', previous.impression) if previous else template.impression,
            'diagnoses': previous.diagnoses.all() if previous else [],
            'finalized': bool(previous and previous.finalized)})
        for field in template.fields:
            name = 'measurement_' + field['name']
            self.fields[name] = forms.CharField(max_length=500, label=field['label'],
                help_text=field.get('unit', ''), initial=previous.values.get(field['name'], '') if previous else '')

    def measurement_values(self):
        return {field['name']: self.cleaned_data['measurement_' + field['name']] for field in self.template.fields}


class DateRangeForm(forms.Form):
    start = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), initial=timezone.localdate)
    end = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), initial=timezone.localdate)

    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['start'] > data['end']:
            raise ValidationError('Start date must be before or equal to end date.')
        return data
