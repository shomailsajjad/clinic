from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone


class ClinicUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields['role'] = User.Role.ADMIN
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        OPERATOR = 'operator', 'Operator'
        DOCTOR = 'doctor', 'Doctor'

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.OPERATOR)
    objects = ClinicUserManager()


class Patient(models.Model):
    class Gender(models.TextChoices):
        FEMALE = 'female', 'Female'
        MALE = 'male', 'Male'
        OTHER = 'other', 'Other'

    class AgeUnit(models.TextChoices):
        YEARS = 'years', 'Years'
        MONTHS = 'months', 'Months'
        DAYS = 'days', 'Days'

    name = models.CharField(max_length=160)
    date_of_birth = models.DateField(null=True, blank=True)
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    age_unit = models.CharField(max_length=6, choices=AgeUnit.choices, default=AgeUnit.YEARS)
    gender = models.CharField(max_length=10, choices=Gender.choices)
    phone = models.CharField(max_length=24, blank=True, validators=[
        RegexValidator(r'^\+?[0-9 ()-]{7,24}$', 'Enter a valid phone number.'),
    ])
    cnic = models.CharField(max_length=13, unique=True, null=True, blank=True, validators=[
        RegexValidator(r'^\d{13}$', 'CNIC must contain 13 digits.'),
    ])
    address = models.TextField(blank=True, max_length=1000)
    referring_doctor = models.CharField(max_length=160, blank=True)
    clinical_history = models.TextField(blank=True, max_length=5000)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    @property
    def patient_number(self):
        return f'SPC-{self.pk:06d}'

    @property
    def age_display(self):
        if self.date_of_birth:
            today = timezone.localdate()
            years = today.year - self.date_of_birth.year - (
                (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
            )
            if years:
                return f'{years} years'
            months = (today.year - self.date_of_birth.year) * 12 + today.month - self.date_of_birth.month
            months -= today.day < self.date_of_birth.day
            return f'{months} months' if months else f'{(today - self.date_of_birth).days} days'
        return f'{self.age} {self.age_unit}'

    def __str__(self):
        return f'{self.patient_number} · {self.name}'


class Service(models.Model):
    name = models.CharField(max_length=100, unique=True)
    token_prefix = models.CharField(max_length=8, unique=True, validators=[
        RegexValidator(r'^[A-Z][A-Z0-9]{0,7}$', 'Use 1–8 uppercase letters or digits, starting with a letter.'),
    ])
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0'))])
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [models.CheckConstraint(condition=models.Q(price__gte=0), name='service_price_nonnegative')]

    def __str__(self):
        return self.name


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=50)
    record_type = models.CharField(max_length=50)
    record_id = models.PositiveBigIntegerField()
    changed_fields = models.JSONField(default=list)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-pk']
