from django.conf import settings
from django.core.cache import cache
from django.utils import timezone


def portal_context(request):
    ctx = {
        'unread_notifications_count': 0,
        'today_attendance': None,
        'GOOGLE_SITE_VERIFICATION': getattr(settings, 'GOOGLE_SITE_VERIFICATION', ''),
        'BING_SITE_VERIFICATION': getattr(settings, 'BING_SITE_VERIFICATION', ''),
        'POSTHOG_API_KEY': getattr(settings, 'POSTHOG_API_KEY', ''),
        'POSTHOG_HOST': getattr(settings, 'POSTHOG_HOST', 'https://us.i.posthog.com'),
    }
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        count = cache.get(f'mc_unread:{user.pk}')
        if count is None:
            count = user.notifications.filter(is_read=False).count()
            cache.set(f'mc_unread:{user.pk}', count, 30)
        ctx['unread_notifications_count'] = count
        if user.role in ('DOCTOR', 'NURSE', 'STAFF'):
            from .models import DailyAttendance
            ctx['today_attendance'] = DailyAttendance.objects.filter(
                user=user, date=timezone.localdate()
            ).first()
    return ctx
