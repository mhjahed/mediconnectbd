import json
import uuid
import requests
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.db.models import Avg, Count
from accounts.models import CustomUser, DoctorProfile


def home_view(request):
    """
    Public Medicare-inspired landing page with live Care Finder preview.
    Uses select_related + annotation to prevent N+1 queries.
    """
    featured_doctors = (
        CustomUser.objects.filter(role='DOCTOR', is_active=True, doctor_profile__is_available=True)
        .select_related('doctor_profile')
        .annotate(
            verified_review_count=Count('reviews_received', distinct=True),
            verified_avg_rating=Avg('reviews_received__rating'),
        )
        .order_by('first_name')[:6]
    )

    return render(request, 'core/home.html', {
        'featured_doctors': featured_doctors,
        'specialties': DoctorProfile.SPECIALTIES,
    })


def privacy_view(request):
    return render(request, 'core/legal_page.html', {
        'page_title': 'Privacy Policy & Data Minimization',
        'page_eyebrow': 'Governance · Document 01',
        'doc_type': 'privacy',
    })


def terms_view(request):
    return render(request, 'core/legal_page.html', {
        'page_title': 'Terms of Use & Telemedicine Disclaimer',
        'page_eyebrow': 'Governance · Document 02',
        'doc_type': 'terms',
    })


def refund_policy_view(request):
    return render(request, 'core/legal_page.html', {
        'page_title': 'Refund & 24-Hour Cancellation Policy',
        'page_eyebrow': 'Governance · Document 03',
        'doc_type': 'refund',
    })


def cookie_policy_view(request):
    return render(request, 'core/legal_page.html', {
        'page_title': 'Cookie Policy (Strictly Necessary Only)',
        'page_eyebrow': 'Governance · Document 04',
        'doc_type': 'cookies',
    })


def accessibility_view(request):
    return render(request, 'core/legal_page.html', {
        'page_title': 'Accessibility Statement & Compliance',
        'page_eyebrow': 'Governance · Document 05',
        'doc_type': 'accessibility',
    })


def robots_txt(request):
    site = settings.SITE_URL.rstrip('/')
    lines = [
        'User-agent: *',
        'Allow: /',
        'Disallow: /dashboard/',
        'Disallow: /django-admin/',
        'Disallow: /payments/',
        'Disallow: /records/',
        f'Sitemap: {site}/sitemap.xml',
    ]
    return HttpResponse('\n'.join(lines), content_type='text/plain; charset=utf-8')


def llms_txt(request):
    site = settings.SITE_URL.rstrip('/')
    content = f"""# MediConnect — Clinical Telemedicine & Hospital Access Platform

> MediConnect connects patients in Bangladesh with licensed hospital specialists for scheduled video consultations, verified local payments (bKash, Nagad, Rocket, Upay, Bank Transfer), printable SOAP prescriptions, and Code-128 hospital ward access control.

## Key Public Pages
- [Home & Care Finder]({site}/): Overview of specialist scheduling, quick patient/staff tasks, and published physician directory.
- [Search Specialists]({site}/appointments/search/): Filter licensed doctors by specialty, consultation fee, and 72-hour advance availability.
- [Patient Registration]({site}/accounts/register/patient/): Create a patient account with minimal required clinical contact details.
- [Staff Barcode Terminal]({site}/access/scanner/): Hospital door verification terminal supporting Code-128, QR, EAN, and PDF417 badges.

## Governance & Policies
- [Privacy Policy]({site}/legal/privacy/)
- [Terms & Clinical Disclaimer]({site}/legal/terms/)
- [24-Hour Refund Policy]({site}/legal/refunds/)
- [Cookie Policy]({site}/legal/cookies/)
- [Accessibility Statement]({site}/legal/accessibility/)
"""
    return HttpResponse(content, content_type='text/plain; charset=utf-8')


def error_403(request, exception=None):
    return render(request, '403.html', status=403)


def error_404(request, exception=None):
    return render(request, '404.html', status=404)


def error_500(request):
    return render(request, '500.html', status=500)


PRIVATE_TELEMETRY_PREFIXES = (
    '/records/',
    '/payments/',
    '/notifications/mailer/',
    '/notifications/live/',
    '/dashboard/',
    '/access/',
    '/accounts/',
)

# Only these events may be relayed. Anything else is refused before any network call,
# so this endpoint can never be used to inject arbitrary events into the project.
ALLOWED_TELEMETRY_EVENTS = ('$pageview', '$pageleave', '$web_vitals')

# Properties we are willing to forward (everything else is dropped).
ALLOWED_PROP_KEYS = {
    'load_ms', 'viewport_w', 'viewport_h',
    'duration_ms', 'scroll_pct', 'scroll_y',
    'lcp_ms', 'cls', 'inp_ms', 'ttfb_ms',
    'referrer', 'nav_type',
}


def _clean_number(value, maximum=3_600_000):
    """Telemetry numbers must be finite and sane; reject anything else silently."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num or num in (float('inf'), float('-inf')) or num < 0 or num > maximum:
        return None
    return round(num, 4)


@csrf_exempt
@require_POST
def telemetry_pulse_view(request):
    """
    First-party, consent-gated analytics relay (/pulse/).

    Why this exists: Brave Shields / uBlock block *.i.posthog.com outright, so the
    browser only ever talks to our OWN origin and Django forwards the event
    server-side. Result: real pageviews, pageleave (bounce/duration), session IDs
    and Core Web Vitals — with zero third-party requests from the clinical portal.

    Privacy rules, enforced here rather than trusted from the client:
      * clinical / account / dashboard routes are dropped with no outbound call
      * only three event names are relayed
      * only known numeric/string properties are forwarded
    """
    api_key = getattr(settings, 'POSTHOG_API_KEY', '').strip()
    if not api_key:
        return JsonResponse({'ok': False, 'reason': 'unconfigured'}, status=200)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        return JsonResponse({'ok': False}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({'ok': False}, status=400)

    event_name = str(payload.get('event') or '$pageview')[:64]
    if event_name not in ALLOWED_TELEMETRY_EVENTS:
        return JsonResponse({'ok': False, 'reason': 'event_not_allowed'}, status=400)

    path = str(payload.get('path') or '/').split('?')[0][:200]
    if path.startswith(PRIVATE_TELEMETRY_PREFIXES):
        return JsonResponse({'ok': True, 'skipped': True})

    anon_id = str(payload.get('anon_id') or uuid.uuid4())[:64]
    session_id = str(payload.get('session_id') or '')[:64]
    host = getattr(settings, 'POSTHOG_HOST', 'https://us.i.posthog.com').rstrip('/')

    props = {
        '$current_url': str(payload.get('url') or path)[:300],
        '$pathname': path,
        '$host': request.get_host(),
        'title': str(payload.get('title') or '')[:160],
        '$lib': 'mediconnect-first-party-relay',
    }
    # PostHog Web Analytics needs a UUIDv7-shaped session id on every event, otherwise
    # it silently drops the pageview from visitor/session counts.
    if session_id:
        props['$session_id'] = session_id
        props['$window_id'] = session_id
    if event_name == '$pageleave':
        props['$prev_pageview_pathname'] = path

    raw_props = payload.get('props') if isinstance(payload.get('props'), dict) else {}
    for key, value in raw_props.items():
        if key not in ALLOWED_PROP_KEYS:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            cleaned = _clean_number(value)
            if cleaned is not None:
                props[key] = cleaned
        elif isinstance(value, str):
            props[key] = value[:300]

    # Backwards compatibility with the older flat payload shape.
    for flat_key in ('load_ms', 'viewport_w', 'viewport_h'):
        if flat_key not in props and payload.get(flat_key) is not None:
            cleaned = _clean_number(payload.get(flat_key))
            if cleaned is not None:
                props[flat_key] = cleaned

    ph_body = {
        'api_key': api_key,
        'event': event_name,
        'distinct_id': anon_id,
        'properties': props,
    }
    try:
        requests.post(f"{host}/capture/", json=ph_body, timeout=4)
    except Exception:
        pass
    # Telemetry must never surface an error to the visitor.
    return JsonResponse({'ok': True})

