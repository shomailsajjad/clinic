from django.db import migrations

def freeze(apps, schema_editor):
    Booking = apps.get_model('clinic', 'Booking')
    Money = apps.get_model('clinic', 'MoneyTransaction')
    for booking in Booking.objects.select_related('clinic').filter(clinic_snapshot={}):
        head = {key: getattr(booking.clinic, key) for key in ['name','code','address','phone','doctor_name','qualifications','prescription_footer']}
        booking.clinic_snapshot = head
        booking.save(update_fields=['clinic_snapshot'])
    for record in Money.objects.select_related('booking'):
        if not isinstance(record.snapshot.get('clinic'), dict):
            record.snapshot = {**record.snapshot, 'clinic': record.booking.clinic_snapshot}
            record.save(update_fields=['snapshot'])

class Migration(migrations.Migration):
    dependencies = [('clinic','0004_synccredential_node_id_syncstate_node_id')]
    operations = [migrations.RunPython(freeze, migrations.RunPython.noop)]
