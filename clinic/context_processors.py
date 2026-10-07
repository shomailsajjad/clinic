from django.conf import settings

def deployment(request):
    clinic = request.user.clinic if request.user.is_authenticated and request.user.clinic_id else None
    return {'clinic_demo_mode': settings.CLINIC_DEMO_MODE, 'active_clinic': clinic, 'letterhead': clinic.letterhead() if clinic else {}}
