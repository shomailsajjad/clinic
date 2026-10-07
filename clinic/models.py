from decimal import Decimal
import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone


class ScopedManager(models.Manager):
    def for_user(self, user):
        from .access import scope
        return scope(self.get_queryset(), user)


class Organization(models.Model):
    global_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=160)
    objects = ScopedManager()


class Clinic(models.Model):
    global_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    name = models.CharField(max_length=160)
    code = models.CharField(max_length=16, unique=True, validators=[
        RegexValidator(r'^[A-Z][A-Z0-9-]{1,15}$', 'Use 2–16 uppercase letters, digits or hyphens.')])
    address = models.TextField(blank=True, max_length=1000)
    phone = models.CharField(max_length=30, blank=True)
    doctor_name = models.CharField(max_length=160, blank=True)
    qualifications = models.CharField(max_length=160, blank=True)
    prescription_footer = models.CharField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    objects = ScopedManager()

    class Meta:
        ordering = ['name']

    def letterhead(self):
        return {key: getattr(self, key) for key in ['name', 'code', 'address', 'phone',
                'doctor_name', 'qualifications', 'prescription_footer']}

    def __str__(self):
        return self.name


class ClinicUserManager(UserManager):
    def for_user(self, user):
        from .access import scope
        return scope(self.get_queryset(), user)

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields['role'] = User.Role.ADMIN
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        OWNER = 'owner', 'Organisation admin'
        ADMIN = 'admin', 'Admin'
        OPERATOR = 'operator', 'Operator'
        DOCTOR = 'doctor', 'Doctor'

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.OPERATOR)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, default=1)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, default=1, null=True, blank=True)
    objects = ClinicUserManager()

    class Meta(AbstractUser.Meta):
        constraints = [models.UniqueConstraint(fields=['clinic'], condition=models.Q(role='doctor', is_active=True), name='one_active_doctor_per_clinic')]


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
    cnic = models.CharField(max_length=13, null=True, blank=True, validators=[
        RegexValidator(r'^\d{13}$', 'CNIC must contain 13 digits.'),
    ])
    address = models.TextField(blank=True, max_length=1000)
    referring_doctor = models.CharField(max_length=160, blank=True)
    clinical_history = models.TextField(blank=True, max_length=5000)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    global_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, default=1)
    owner_clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, default=1)
    objects = ScopedManager()

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['organization', 'cnic'], name='unique_organisation_cnic')]

    @property
    def patient_number(self):
        return f'P-{self.global_id.hex}'

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
    class Category(models.TextChoices):
        IMAGING = 'imaging', 'Radiology / imaging'
        OPD = 'opd', 'Doctor consultation / OPD'
    category = models.CharField(max_length=10, choices=Category.choices, default=Category.IMAGING)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, default=1)
    name = models.CharField(max_length=100)
    token_prefix = models.CharField(max_length=8, validators=[
        RegexValidator(r'^[A-Z][A-Z0-9]{0,7}$', 'Use 1–8 uppercase letters or digits, starting with a letter.'),
    ])
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0'))])
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = ScopedManager()

    class Meta:
        ordering = ['name']
        constraints = [models.CheckConstraint(condition=models.Q(price__gte=0), name='service_price_nonnegative'),
                       models.UniqueConstraint(fields=['clinic', 'name'], name='unique_clinic_service_name'),
                       models.UniqueConstraint(fields=['clinic', 'token_prefix'], name='unique_clinic_token_prefix')]

    def __str__(self):
        return self.name


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=50)
    record_type = models.CharField(max_length=50)
    record_id = models.PositiveBigIntegerField()
    changed_fields = models.JSONField(default=list)
    created_at = models.DateTimeField(default=timezone.now)
    objects = ScopedManager()

    class Meta:
        ordering = ['-created_at', '-pk']


class Booking(models.Model):
    class Kind(models.TextChoices):
        WALK_IN = 'walk_in', 'Walk-in'
        APPOINTMENT = 'appointment', 'Appointment'

    class Status(models.TextChoices):
        BOOKED = 'booked', 'Booked'
        ARRIVED = 'arrived', 'Arrived'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    request_id = models.UUIDField(default=uuid.uuid4, unique=True)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='bookings')
    patient_snapshot = models.JSONField(default=dict)
    age_years = models.DecimalField(max_digits=6, decimal_places=2)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    scheduled_date = models.DateField()
    scheduled_time = models.TimeField(null=True, blank=True)
    referring_doctor = models.CharField(max_length=160, blank=True)
    clinical_history = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.BOOKED)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, default=1)
    clinic_snapshot = models.JSONField(default=dict)
    objects = ScopedManager()

    class Meta:
        ordering = ['-scheduled_date', '-pk']

    @property
    def number(self):
        return f'{self.clinic.code}-B-{self.pk:06d}'

    @property
    def gross(self):
        return sum((line.price for line in self.items.all()), Decimal('0.00'))

    @property
    def discount(self):
        return sum((line.discount for line in self.items.all()), Decimal('0.00'))

    @property
    def due(self):
        return self.gross - self.discount

    @property
    def has_paid(self):
        return self.transactions.filter(kind='collection').exists()

    @property
    def net_collected(self):
        total = Decimal('0.00')
        for entry in self.transactions.all():
            total += entry.amount if entry.kind == 'collection' else -entry.amount
        return total


class BookingItem(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name='items')
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    service_category = models.CharField(max_length=10, default='imaging')
    service_name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=12, choices=Booking.Status.choices, default=Booking.Status.BOOKED)
    objects = ScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['booking', 'service'], name='one_service_per_booking'),
            models.CheckConstraint(condition=models.Q(price__gte=0, discount__gte=0) & models.Q(discount__lte=models.F('price')), name='valid_booked_price'),
        ]

    @property
    def current_token(self):
        return self.tokens.filter(is_active=True).first()


class TokenCounter(models.Model):
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    date = models.DateField()
    last_number = models.PositiveIntegerField(default=0)
    objects = ScopedManager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['service', 'date'], name='one_daily_counter')]


class Token(models.Model):
    item = models.ForeignKey(BookingItem, on_delete=models.PROTECT, related_name='tokens')
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    date = models.DateField()
    number = models.PositiveIntegerField()
    prefix = models.CharField(max_length=8)
    is_active = models.BooleanField(default=True)
    objects = ScopedManager()

    class Meta:
        ordering = ['date', 'service_id', 'number']
        constraints = [
            models.UniqueConstraint(fields=['service', 'date', 'number'], name='unique_daily_token'),
            models.UniqueConstraint(fields=['item'], condition=models.Q(is_active=True), name='one_active_token_per_item'),
        ]

    @property
    def label(self):
        return f'{self.prefix}-{self.number:03d}'


class DiscountRequest(models.Model):
    item = models.ForeignKey(BookingItem, on_delete=models.PROTECT, related_name='discount_requests')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=500)
    status = models.CharField(max_length=10, choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='pending')
    requested_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='discount_requests')
    reviewed_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, related_name='discount_reviews')
    created_at = models.DateTimeField(default=timezone.now)
    reviewed_at = models.DateTimeField(null=True)
    objects = ScopedManager()


class MoneyTransaction(models.Model):
    class Kind(models.TextChoices):
        COLLECTION = 'collection', 'Collection'
        REFUND = 'refund', 'Refund'
        REVERSAL = 'reversal', 'Correction reversal'

    class Method(models.TextChoices):
        CASH = 'cash', 'Cash'
        BANK = 'bank', 'Bank transfer'
        CARD = 'card', 'Card'
        EASYPAISA = 'easypaisa', 'Easypaisa'
        JAZZCASH = 'jazzcash', 'JazzCash'

    request_id = models.UUIDField(default=uuid.uuid4, unique=True)
    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name='transactions')
    original = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='adjustments')
    kind = models.CharField(max_length=12, choices=Kind.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=10, choices=Method.choices)
    reference = models.CharField(max_length=120, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    snapshot = models.JSONField(default=dict)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)
    objects = ScopedManager()

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name='transaction_amount_positive')]

    @property
    def receipt_number(self):
        prefix = {'collection': 'R', 'refund': 'RF', 'reversal': 'RV'}[self.kind]
        return f'{self.booking.clinic.code}-{prefix}-{self.pk:07d}'


class Diagnosis(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, default=1)
    name = models.CharField(max_length=160)
    code = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)
    objects = ScopedManager()

    class Meta:
        ordering = ['name']
        constraints = [models.UniqueConstraint(fields=['organization', 'name'], name='unique_organisation_diagnosis')]

    def __str__(self):
        return f'{self.name} ({self.code})' if self.code else self.name


class ReportTemplate(models.Model):
    name = models.CharField(max_length=160)
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    disease_group = models.CharField(max_length=160, blank=True)
    fields = models.JSONField(default=list)
    findings = models.TextField()
    impression = models.TextField()
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)
    objects = ScopedManager()

    class Meta:
        ordering = ['service__name', 'name']

    def __str__(self):
        return self.name


class ReportVersion(models.Model):
    item = models.ForeignKey(BookingItem, on_delete=models.PROTECT, related_name='reports')
    version = models.PositiveIntegerField()
    previous = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True, related_name='revision')
    template = models.ForeignKey(ReportTemplate, on_delete=models.PROTECT)
    template_snapshot = models.JSONField(default=dict)
    patient_snapshot = models.JSONField(default=dict)
    values = models.JSONField(default=dict)
    findings = models.TextField()
    impression = models.TextField()
    diagnoses = models.ManyToManyField(Diagnosis)
    diagnosis_snapshot = models.JSONField(default=list)
    finalized = models.BooleanField(default=False)
    revision_reason = models.CharField(max_length=500, blank=True)
    author = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)
    objects = ScopedManager()

    class Meta:
        ordering = ['-version']
        constraints = [models.UniqueConstraint(fields=['item', 'version'], name='unique_report_version')]

    @property
    def is_revision(self):
        return self.item.reports.filter(version__lt=self.version, finalized=True).exists()


class OPDVersion(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name='opd_versions')
    version = models.PositiveIntegerField()
    previous = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True)
    chief_complaint = models.TextField()
    history = models.TextField(blank=True)
    examination = models.TextField()
    vitals = models.JSONField(default=dict)
    diagnosis_snapshot = models.JSONField(default=list)
    diagnoses = models.ManyToManyField(Diagnosis)
    medicines = models.JSONField(default=list)
    advice = models.TextField(blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    finalized = models.BooleanField(default=False)
    revision_reason = models.CharField(max_length=500, blank=True)
    patient_snapshot = models.JSONField(default=dict)
    clinic_snapshot = models.JSONField(default=dict)
    author = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)
    objects = ScopedManager()

    class Meta:
        ordering = ['-version']
        constraints = [models.UniqueConstraint(fields=['booking', 'version'], name='unique_opd_version')]

    @property
    def is_revision(self):
        return self.booking.opd_versions.filter(version__lt=self.version, finalized=True).exists()


class SyncOutbox(models.Model):
    entity_type = models.CharField(max_length=12, choices=[('patient', 'Patient'), ('visit', 'Visit')])
    entity_id = models.PositiveBigIntegerField()
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT)
    version = models.PositiveBigIntegerField(default=1)
    acknowledged_version = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['entity_type', 'entity_id'], name='unique_outbox_entity')]


class SyncCredential(models.Model):
    clinic = models.OneToOneField(Clinic, on_delete=models.PROTECT)
    token_hash = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)
    node_id = models.UUIDField(null=True, blank=True)


class RemoteRecord(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT)
    entity_type = models.CharField(max_length=12)
    global_id = models.UUIDField()
    patient_id = models.UUIDField()
    version = models.PositiveBigIntegerField()
    payload = models.JSONField()
    received_at = models.DateTimeField(default=timezone.now)
    change_number = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['clinic', 'entity_type', 'global_id'], name='unique_remote_record')]


class SyncState(models.Model):
    clinic = models.OneToOneField(Clinic, on_delete=models.PROTECT)
    cursor = models.PositiveBigIntegerField(default=0)
    last_success = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True)
    node_id = models.UUIDField(default=uuid.uuid4)


class PatientAlias(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    alias_id = models.UUIDField(unique=True)
    canonical_id = models.UUIDField()
    approved_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True)
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(default=timezone.now)


class SyncClock(models.Model):
    organization = models.OneToOneField(Organization, on_delete=models.PROTECT)
    sequence = models.PositiveBigIntegerField(default=0)
