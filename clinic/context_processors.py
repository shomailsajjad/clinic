from django.conf import settings


def deployment(request):
    return {'clinic_demo_mode': settings.CLINIC_DEMO_MODE}
