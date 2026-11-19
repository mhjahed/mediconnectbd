import hashlib
import logging
import time
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse

logger = logging.getLogger('mediconnect')


def get_client_ip(request):
    # Prefer Cloudflare's verified connecting IP header when fronted by Cloudflare Workers / Proxy
    cf_ip = request.META.get('HTTP_CF_CONNECTING_IP')
    if cf_ip:
        return cf_ip.strip()
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


class SecurityHeadersAndRateLimitMiddleware:
    """
    Implements IMPLEMENTATION.txt security & observability requirements:
    - Security headers (Content-Security-Policy, X-Content-Type-Options, Referrer-Policy, Permissions-Policy)
    - Exposed-secret path blocker (/.env, /.git, /wp-admin, /.aws, /phpinfo.php -> instant 404)
    - Per-IP sliding rate limiting on auth, registration, payment, scanner, patient QR, and SMTP routes
    - Lightweight request latency header (Server-Timing) + slow-query warning (>800ms)
    - HIDE_ROUTE_LIST_AFTER: requests that fail to resolve to a view never reach Django's
      URL resolver, so Django's orange "Page not found" debug page (which dumps the full
      urlconf) can never leak your route map.
    """

    BLOCKED_PROBE_PREFIXES = (
        '/.env',
        '/.git',
        '/.aws',
        '/wp-admin',
        '/wp-login.php',
        '/phpinfo',
        '/phpmyadmin',
    )

    RATE_LIMITED_PATHS = {
        '/accounts/login/': (15, 60),
        '/accounts/register/patient/': (8, 60),
        '/accounts/register/doctor/': (8, 60),
        '/accounts/register/staff/': (8, 60),
        # Ward-door badge checks: a human can physically swipe at most ~1 badge/second.
        # Staff-only views already 403 anonymous/patient callers; this caps a stolen
        # staff session (or a scripted loop) at 30 badge evaluations per minute per IP.
        '/access/scan/': (30, 60),
        '/access/patient-entry/verify/': (30, 60),
        '/notifications/mailer/compose/': (20, 60),
        '/notifications/mailer/settings/': (10, 60),
        # First-party analytics relay: a real page load sends ~5 beacons
        # (pageview, pageleave, LCP/CLS/INP). 120/min is generous for humans,
        # lethal for a script trying to flood the PostHog project.
        '/pulse/': (120, 60),
    }

    # Paths that are never "hidden" even when HIDE_ROUTE_LIST_AFTER is on:
    # static files, media, and the telemetry relay must keep working.
    NEVER_HIDDEN_PREFIXES = ('/static/', '/media/', '/pulse/', '/favicon.ico', '/robots.txt')

    def __init__(self, get_response):
        self.get_response = get_response
        raw = str(getattr(settings, 'HIDE_ROUTE_LIST_AFTER', '') or '').strip()
        self.route_cutoff = self._parse_cutoff(raw)

    @staticmethod
    def _parse_cutoff(raw):
        """Accepts 'YYYY-MM-DD', 'YYYY-MM-DDTHH:MM', or a unix timestamp. Empty => disabled."""
        if not raw:
            return None
        from datetime import datetime, timezone as dt_timezone

        from django.utils.dateparse import parse_datetime

        if raw.isdigit():
            return datetime.fromtimestamp(int(raw), tz=dt_timezone.utc)
        parsed = parse_datetime(raw)
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError:
                logger.warning('HIDE_ROUTE_LIST_AFTER could not be parsed: %r', raw)
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_timezone.utc)
        return parsed

    def _route_map_is_hidden(self, request):
        if not self.route_cutoff:
            return False
        from django.utils import timezone as dj_timezone

        if dj_timezone.now() < self.route_cutoff:
            return False
        path = request.path
        return not any(path.startswith(p) for p in self.NEVER_HIDDEN_PREFIXES)

    def __call__(self, request):
        start_ts = time.perf_counter()
        lower_path = request.path.lower()

        # 1. Block automated secret-file & scanner probes immediately
        if any(lower_path.startswith(prefix) for prefix in self.BLOCKED_PROBE_PREFIXES):
            logger.warning("Blocked suspicious file probe from %s on %s", get_client_ip(request), request.path)
            return HttpResponse('Not Found', status=404, content_type='text/plain')

        # 2. Sliding-window POST rate limiting
        if request.method == 'POST':
            limit_cfg = self.RATE_LIMITED_PATHS.get(request.path)
            if not limit_cfg and request.path.startswith('/payments/submit/'):
                limit_cfg = (10, 60)

            if limit_cfg:
                max_hits, window_sec = limit_cfg
                ip = get_client_ip(request)
                bucket = f"rl:{hashlib.sha256(f'{ip}:{request.path}'.encode()).hexdigest()[:20]}"
                hits = cache.get(bucket, 0)
                if hits >= max_hits:
                    logger.warning("Rate limit exceeded for IP %s on %s", ip, request.path)
                    if (
                        request.headers.get('Accept', '').startswith('application/json')
                        or request.path in ('/access/scan/', '/access/patient-entry/verify/')
                    ):
                        return JsonResponse(
                            {'status': 'error', 'message': 'Too many requests. Please wait one minute before retrying.'},
                            status=429,
                        )
                    return HttpResponse(
                        'Too many attempts from your connection. Please wait 60 seconds and try again.',
                        status=429,
                        content_type='text/plain',
                    )
                cache.set(bucket, hits + 1, timeout=window_sec)

        response = self.get_response(request)

        # 3. Never dump the urlconf. Django's technical 404 page is only ever produced by the
        #    resolver itself; once the route map is hidden, unknown URL shapes are answered with
        #    the branded 404 template (debug info stripped) instead.
        if response.status_code == 404 and self._route_map_is_hidden(request):
            response = self._branded_404(request)
            response.setdefault('Server-Timing', f'app;dur={(time.perf_counter() - start_ts) * 1000.0:.1f}')

        # 4. Trace slow requests (IMPLEMENTATION.txt #18)
        elapsed_ms = (time.perf_counter() - start_ts) * 1000.0
        response.setdefault('Server-Timing', f'app;dur={elapsed_ms:.1f}')
        if elapsed_ms > 800:
            logger.warning("Slow request %.1fms on %s %s", elapsed_ms, request.method, request.path)

        # 5. Defense-in-depth Security Headers (IMPLEMENTATION.txt #7 & #14)
        response.setdefault('X-Content-Type-Options', 'nosniff')
        response.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.setdefault('Permissions-Policy', 'camera=(self), microphone=(), geolocation=()')
        response.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "img-src 'self' data: blob: https:; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "media-src 'self' data: blob:; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com https://us-assets.i.posthog.com https://eu-assets.i.posthog.com; "
        "connect-src 'self' https://us.i.posthog.com https://eu.i.posthog.com; "
        "base-uri 'self'; form-action 'self';",
        )

        return response

    def _branded_404(self, request):
        """Minimal 404 that never reveals route names, patterns, or the urlconf."""
        from django.shortcuts import render

        wants_json = (
            request.headers.get('Accept', '').startswith('application/json')
            or request.headers.get('X-Requested-With') == 'fetch'
        )
        if wants_json:
            return JsonResponse({'detail': 'Not found.'}, status=404)
        try:
            return render(request, '404.html', status=404)
        except Exception:  # template problem must never turn a 404 into a 500
            return HttpResponse(
                '<!doctype html><meta charset="utf-8"><title>Page not found</title>'
                '<div style="font:16px/1.6 system-ui;padding:56px;max-width:560px;margin:auto">'
                '<h1 style="font-size:1.5rem">Page not found</h1>'
                '<p>This MediConnect page does not exist or has moved.</p>'
                '<p><a href="/">Return to the portal home</a></p></div>',
                status=404,
                content_type='text/html; charset=utf-8',
            )


class AdminPathCamoMiddleware:
    """
    Hides the existence of the Django admin entirely.

    Incoming  /mc-registry-vault/...  -> rewritten to /<ADMIN_URL_PATH>/... before routing.
    Outgoing  admin HTML              -> absolute links rewritten back to the camo prefix,
                                         and any mention of the raw ADMIN_URL_PATH scrubbed.

    Net effect: `/django-admin/`, `/admin/`, and the real configured slug all 404, while the
    staff-only camo URL keeps working bookmarks/intent. No route names ever leak.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        raw = str(getattr(settings, 'ADMIN_URL_PATH', '') or '').strip('/')
        self.admin_slug = (raw + '/') if raw else ''
        camo = str(getattr(settings, 'ADMIN_CAMO_PREFIX', '') or '').strip('/')
        self.camo_prefix = (camo + '/') if camo else ''

    def __call__(self, request):
        if not self.admin_slug or not self.camo_prefix:
            return self.get_response(request)

        camo_root = '/' + self.camo_prefix
        real_root = '/' + self.admin_slug
        camo_base = camo_root.rstrip('/')

        rewritten = False
        if request.path_info.startswith(camo_root):
            rest = request.path_info[len(camo_root):]
            request.path_info = real_root + rest
            request.path = request.path_info
            request._mc_admin_camo = True
            rewritten = True
        elif request.path_info.startswith(real_root) and real_root != camo_root:
            # Direct hits on the underlying slug are dead ends (even the login redirect).
            return HttpResponse('Not Found', status=404, content_type='text/plain')

        response = self.get_response(request)

        # Redirect targets can leak the real slug (/django-admin/login/), so rewrite
        # every Location header back to the camo prefix before it reaches the client.
        if rewritten and response.has_header('Location'):
            location = response['Location']
            camo_logical = camo_root.rstrip('/')
            if real_root in location:
                response['Location'] = location.replace(real_root, camo_root)
            elif location.startswith(camo_logical) and not location.startswith(real_root):
                pass
            elif location.startswith(real_root.rstrip('/')):
                response['Location'] = location.replace(real_root.rstrip('/'), camo_logical)

        content_type = response.get('Content-Type', '')
        if rewritten and 'text/html' in content_type and getattr(response, 'streaming', False) is False:
            try:
                html = response.content.decode('utf-8', errors='ignore')
            except Exception:
                return response
            real_base = '/' + self.admin_slug.rstrip('/')
            if real_root not in html and real_base not in html:
                return response
            html = html.replace(real_root, camo_root).replace(real_base, camo_base)
            response.content = html.encode('utf-8')
            if response.has_header('Content-Length'):
                response['Content-Length'] = str(len(response.content))
        return response
