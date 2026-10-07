from django.core.exceptions import PermissionDenied


PATHS = {
    'user': 'clinic', 'service': 'clinic', 'booking': 'clinic',
    'bookingitem': 'booking__clinic', 'tokencounter': 'service__clinic',
    'token': 'item__booking__clinic', 'discountrequest': 'item__booking__clinic',
    'moneytransaction': 'booking__clinic', 'reporttemplate': 'service__clinic',
    'reportversion': 'item__booking__clinic', 'opdversion': 'booking__clinic',
    'auditevent': 'actor__clinic',
}


def is_owner(user):
    return user.role == 'owner'


def scope(queryset, user):
    if not user.is_authenticated or not user.is_active:
        return queryset.none()
    name = queryset.model._meta.model_name
    if name in ['patient', 'diagnosis', 'clinic']:
        return queryset.filter(organization_id=user.organization_id)
    if name == 'organization':
        return queryset.filter(pk=user.organization_id)
    path = PATHS.get(name)
    if not path:
        raise ValueError(f'No access policy registered for {name}.')
    if is_owner(user):
        if name in ['user', 'auditevent']:
            return queryset.filter(**{('actor__organization_id' if name == 'auditevent' else 'organization_id'): user.organization_id})
        return queryset.filter(**{f'{path}__organization_id': user.organization_id})
    return queryset.filter(**{f'{path}_id': user.clinic_id, f'{path}__organization_id': user.organization_id}) if user.clinic_id else queryset.none()


def require_clinic(user, clinic):
    if user.organization_id != clinic.organization_id or (
        not is_owner(user) and user.clinic_id != clinic.pk
    ):
        raise PermissionDenied


def can_read_local_history(user, clinic):
    return user.organization_id == clinic.organization_id and (
        user.role == 'doctor' or (user.clinic_id == clinic.pk and not is_owner(user)))
