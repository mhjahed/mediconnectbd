from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def role_required(*allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if request.user.is_superuser or request.user.role in allowed_roles:
                return view_func(request, *args, **kwargs)
            raise PermissionDenied('Your account role is not authorized for this area.')
        return _wrapped
    return decorator


def admin_required(view_func):
    return role_required('ADMIN')(view_func)


def doctor_required(view_func):
    return role_required('DOCTOR')(view_func)


def patient_required(view_func):
    return role_required('PATIENT')(view_func)


def staff_member_required(view_func):
    return role_required('DOCTOR', 'NURSE', 'STAFF')(view_func)
