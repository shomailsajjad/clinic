from django.conf import settings
from django.http import HttpResponseForbidden


class BranchBindingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and settings.CLINIC_NODE_MODE == 'branch':
            if not user.clinic_id or user.clinic.code != settings.CLINIC_LOCAL_CODE:
                return HttpResponseForbidden('This account does not belong to this clinic server.')
        return self.get_response(request)
