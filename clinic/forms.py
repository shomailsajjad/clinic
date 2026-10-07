import re

from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Patient, Service, User


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
