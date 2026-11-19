from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from .models import DailyAttendance


class ShiftAttendanceMiddleware:
    """
    Requires DOCTOR, NURSE, and STAFF users to confirm daily shift attendance
    before accessing clinical/operational portal routes (/dashboard/, /records/, /appointments/doctor/).
    Exempts public pages, authentication, profile, notifications, legal pages, and attendance check-in itself.
    """

    GATED_PREFIXES = (
        '/dashboard/',
        '/records/',
        '/appointments/doctor/',
        '/appointments/availability/',
    )

    EXEMPT_PATHS = (
        '/accounts/attendance/',
        '/accounts/logout/',
        '/accounts/login/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if (
            user
            and user.is_authenticated
            and not user.is_superuser
            and user.role in ('DOCTOR', 'NURSE', 'STAFF')
        ):
            path = request.path
            if any(path.startswith(p) for p in self.GATED_PREFIXES) and not any(
                path.startswith(e) for e in self.EXEMPT_PATHS
            ):
                today = timezone.localdate()
                checked_in = DailyAttendance.objects.filter(user=user, date=today).exists()
                if not checked_in:
                    checkin_url = reverse('accounts:attendance_checkin')
                    return redirect(f"{checkin_url}?next={path}")

        return self.get_response(request)
