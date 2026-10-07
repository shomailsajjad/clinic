from django.conf import settings
from django.db.models import F
from django.db.models.signals import post_save, m2m_changed
from django.dispatch import receiver
from django.utils import timezone

from .models import (Booking, BookingItem, DiscountRequest, MoneyTransaction, OPDVersion,
                     Patient, ReportVersion, SyncOutbox, Token)


def mark_dirty(kind, entity_id, clinic):
    if settings.CLINIC_NODE_MODE == 'central':
        return
    if settings.CLINIC_NODE_MODE == 'branch' and clinic.code != settings.CLINIC_LOCAL_CODE:
        return
    row, created = SyncOutbox.objects.get_or_create(entity_type=kind, entity_id=entity_id, clinic=clinic)
    if not created:
        SyncOutbox.objects.filter(pk=row.pk).update(version=F('version') + 1, updated_at=timezone.now())


@receiver(post_save, sender=Patient)
def patient_dirty(sender, instance, **kwargs):
    if not kwargs.get('raw') and instance.created_by.clinic_id == instance.owner_clinic_id:
        mark_dirty('patient', instance.pk, instance.owner_clinic)


@receiver(post_save, sender=Booking)
@receiver(post_save, sender=BookingItem)
@receiver(post_save, sender=Token)
@receiver(post_save, sender=DiscountRequest)
@receiver(post_save, sender=MoneyTransaction)
@receiver(post_save, sender=ReportVersion)
@receiver(post_save, sender=OPDVersion)
def visit_dirty(sender, instance, **kwargs):
    if kwargs.get('raw'):
        return
    if isinstance(instance, Booking):
        booking = instance
    elif isinstance(instance, (Token, DiscountRequest, ReportVersion)):
        booking = instance.item.booking
    else:
        booking = instance.booking
    mark_dirty('visit', booking.pk, booking.clinic)


@receiver(m2m_changed, sender=ReportVersion.diagnoses.through)
@receiver(m2m_changed, sender=OPDVersion.diagnoses.through)
def diagnoses_dirty(sender, instance, action, **kwargs):
    if action.startswith('post_'):
        booking = instance.booking if isinstance(instance, OPDVersion) else instance.item.booking
        mark_dirty('visit', booking.pk, booking.clinic)
