from django.conf import settings
from django.core.cache import cache
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse, Http404, JsonResponse
from django.utils import timezone
from django.utils.timesince import timesince
from django.views.decorators.http import require_GET
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required
from accounts.models import CustomUser
from .models import (
    Notification,
    EmailDispatchLog,
    MailerSMTPConfig,
    create_notification,
    dispatch_email_alert,
    resolve_active_smtp_credentials,
)


MAILER_TEMPLATES = [
    {
        'key': 'consultation_scheduled',
        'label': 'Consultation Scheduled — Video / Outpatient',
        'short': 'Consultation Scheduled',
        'default_subject': 'MediConnect — Your Specialist Consultation is Confirmed',
        'header_tag': 'CONSULTATION SCHEDULED',
    },
    {
        'key': 'consultation_reminder',
        'label': '24-Hour Consultation Reminder',
        'short': 'Consultation Reminder',
        'default_subject': 'MediConnect — Reminder: Your Consultation is Tomorrow',
        'header_tag': 'CONSULTATION REMINDER',
    },
    {
        'key': 'payment_receipt',
        'label': 'Payment Verified & Reception QR Entry Pass',
        'short': 'Payment & QR Pass',
        'default_subject': 'MediConnect — Payment Verified & Reception QR Pass Active',
        'header_tag': 'PAYMENT VERIFIED',
    },
    {
        'key': 'outpatient_checkin',
        'label': 'Outpatient Hospital Visit & Triage Checklist',
        'short': 'Outpatient Visit Prep',
        'default_subject': 'MediConnect — Preparing for Your Outpatient Hospital Visit',
        'header_tag': 'OUTPATIENT VISIT',
    },
    {
        'key': 'prescription_ready',
        'label': 'Signed SOAP Prescription & Lab Summary Ready',
        'short': 'Prescription Ready',
        'default_subject': 'MediConnect — Your Signed A4 Prescription is Ready',
        'header_tag': 'PRESCRIPTION READY',
    },
    {
        'key': 'credentials_delivery',
        'label': 'Credentials Delivery — Username & Password / Key',
        'short': 'Credentials Delivery',
        'default_subject': 'MediConnect — Your Portal Account & Login Credentials',
        'header_tag': 'ACCOUNT READY',
    },
    {
        'key': 'staff_onboarding',
        'label': 'Clinician & Staff Key / Badge Onboarding',
        'short': 'Staff Onboarding',
        'default_subject': 'MediConnect — Clinician & Ward Badge Onboarding Authorization',
        'header_tag': 'STAFF ONBOARDING',
    },
    {
        'key': 'general_update',
        'label': 'General Portal Update — Custom Message Body',
        'short': 'General Update',
        'default_subject': 'MediConnect — Official Clinical Registry Notice',
        'header_tag': 'PORTAL UPDATE',
    },
]

MAILER_TEMPLATE_MAP = {item['key']: item for item in MAILER_TEMPLATES}


def _build_email_context(template_key: str, data: dict, recipient_name: str = 'Jahed') -> dict:
    meta = MAILER_TEMPLATE_MAP.get(template_key, MAILER_TEMPLATE_MAP['general_update'])
    site_url = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000').rstrip('/')
    cta_path = (data.get('cta_url') or '').strip()
    if cta_path.startswith('/'):
        cta_url = f"{site_url}{cta_path}"
    elif cta_path.startswith('http'):
        cta_url = cta_path
    else:
        cta_url = f"{site_url}/dashboard/"

    return {
        'subject': (data.get('subject') or meta['default_subject']).strip(),
        'header_tag': meta['header_tag'],
        'eyebrow': meta['header_tag'],
        'recipient_name': recipient_name or 'Patient',
        'consultation_type': (data.get('consultation_type') or 'Outpatient Telemedicine Consultation').strip(),
        'consultation_date': (data.get('consultation_date') or '18 Nov 2026').strip(),
        'consultation_time': (data.get('consultation_time') or '10:30 AM Dhaka time').strip(),
        'doctor_name': (data.get('doctor_name') or 'Dr. Mahmudur Rahman').strip(),
        'meet_link': (data.get('meet_link') or 'https://meet.google.com/med-conn-opd').strip(),
        'receipt_number': (data.get('receipt_number') or 'RCPT-20261114-A02223').strip(),
        'amount_paid': (data.get('amount_paid') or 'BDT 1,200').strip(),
        'pass_token': (data.get('pass_token') or 'PASS-6S82ST4ZB8').strip(),
        'diagnosis_summary': (data.get('diagnosis_summary') or 'Essential Hypertension (Stage 1)').strip(),
        'follow_up_note': (data.get('follow_up_note') or 'Review in 3 weeks with morning BP chart').strip(),
        'portal_username': (data.get('portal_username') or 'patient_tanvir').strip(),
        'portal_password': (data.get('portal_password') or 'PatientPass2026!').strip(),
        'access_key_code': (data.get('access_key_code') or 'MEDI-2026-KEY').strip(),
        'reference_code': (data.get('reference_code') or 'MC-2026-BD-0001').strip(),
        'headline': (data.get('headline') or 'General Portal Update').strip(),
        'custom_body': (data.get('custom_body') or 'This is a sample custom clinical notice body for the email preview.').strip(),
        'cta_label': (data.get('cta_label') or 'Open MediConnect Portal').strip(),
        'cta_url': cta_url,
        'support_email': getattr(settings, 'SUPPORT_CONTACT_EMAIL', 'support@mediconnect.bd'),
        'site_url': site_url,
    }


# ---------------------------------------------------------------------------
# Notification link resolution
# ---------------------------------------------------------------------------
# Stored links are written as literal paths, e.g. "/payments/12/receipt/". Some older
# rows (and the demo alert command) point at "/payments/" or "/records/" which are NOT
# real routes -> the user clicked an alert and landed on the 404 page. Rather than
# rewriting history, every stored link is resolved to a live URL with a safe fallback.
ROLE_FALLBACK_LINKS = {
    'ADMIN': '/dashboard/',
    'DOCTOR': '/appointments/doctor/',
    'NURSE': '/dashboard/',
    'STAFF': '/dashboard/',
    'PATIENT': '/dashboard/',
}

# Prefix -> list-page fallback, used when the exact object page is missing.
PREFIX_FALLBACK_LINKS = {
    '/records/': '/records/',
    '/payments/': '/payments/my/',
    '/appointments/': '/appointments/my/',
    '/notifications/': '/notifications/',
}


def safe_notification_link(link, user=None):
    """Return a URL that definitely resolves, or '' when nothing sensible exists."""
    from django.urls import NoReverseMatch, Resolver404, resolve
    from django.urls import reverse

    link = (link or '').strip()
    if link.startswith('/'):
        if not link.startswith('//'):          # never allow protocol-relative URLs
            try:
                resolve(link)
                return link
            except (Resolver404, Exception):
                for prefix, fallback in PREFIX_FALLBACK_LINKS.items():
                    if link.startswith(prefix):
                        return fallback

    role = getattr(user, 'role', None) or 'PATIENT'
    fallback = ROLE_FALLBACK_LINKS.get('ADMIN' if getattr(user, 'is_superuser', False) else role, '/dashboard/')
    try:
        reverse('dashboard:home')
        return fallback
    except NoReverseMatch:
        return '/'


def _humanise_age(created_at):
    """Short, human 'just now / 4 min ago / 2 hours ago' label for popups."""
    delta = timezone.now() - created_at
    seconds = delta.total_seconds()
    if seconds < 60:
        return 'just now'
    if seconds < 3600:
        return f'{int(seconds // 60)} min ago'
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f'{hours} hour{"s" if hours > 1 else ""} ago'
    days = int(seconds // 86400)
    if days == 1:
        return 'yesterday'
    if days < 7:
        return f'{days} days ago'
    return timesince(created_at).split(',')[0] + ' ago'


def _invalidate_unread(user):
    try:
        cache.delete(f'mc_unread:{user.pk}')
    except Exception:
        pass


@login_required
@require_GET
def live_notifications_api(request):
    """
    Tiny polling endpoint for the portal alert popups + chime.

    GET /notifications/live/?after=<last_seen_id>&prime=1
      -> {"unread_count": 3, "latest_id": 42, "notifications": [ ... newest-first ... ]}

    * Scoped strictly to request.user (row-level guard, no IDOR).
    * `prime=1` returns only the newest id (used on first load so a brand-new
      browser does NOT play a chime burst for historical alerts).
    * Capped at 5 rows per poll so a large backlog can never spam the screen.
    """
    try:
        after = int(request.GET.get('after', '0'))
    except (TypeError, ValueError):
        after = 0

    base = Notification.objects.filter(recipient=request.user)
    unread = base.filter(is_read=False).count()
    try:
        cache.set(f'mc_unread:{request.user.pk}', unread, 30)
    except Exception:
        pass
    latest = base.order_by('-id').values_list('id', flat=True).first() or 0

    payload = {
        'unread_count': unread,
        'latest_id': latest,
        'notifications': [],
    }

    if request.GET.get('prime') == '1':
        return JsonResponse(payload)

    if latest > after:
        fresh = (
            base.filter(id__gt=after)
            .order_by('-id')[:5]
            .values('id', 'notification_type', 'title', 'message', 'link', 'created_at')
        )
        payload['notifications'] = [
            {
                'id': row['id'],
                'notification_type': row['notification_type'],
                'title': row['title'][:180],
                'message': (row['message'] or '')[:220],
                'link': safe_notification_link(row['link'], request.user),
                'age': _humanise_age(row['created_at']),
            }
            for row in fresh
        ]

    return JsonResponse(payload)


@login_required
def notification_list_view(request):
    qs = Notification.objects.filter(recipient=request.user).order_by('-created_at')
    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    for notif in page_obj:
        notif.safe_link = safe_notification_link(notif.link, request.user)
    return render(request, 'notifications/list.html', {'page_obj': page_obj})


@login_required
@require_POST
def mark_read_view(request, pk):
    notif = get_object_or_404(Notification, pk=pk, recipient=request.user)
    was_unread = not notif.is_read
    if was_unread:
        notif.is_read = True
        notif.save(update_fields=['is_read'])
        _invalidate_unread(request.user)

    # Popup toasts mark-as-read in the background and stay on the current page.
    if request.headers.get('X-Requested-With') == 'fetch' or request.POST.get('ajax') == '1':
        return JsonResponse({
            'ok': True,
            'id': notif.pk,
            'unread_count': Notification.objects.filter(recipient=request.user, is_read=False).count(),
        })

    target = safe_notification_link(notif.link, request.user)
    return redirect(target or '/')


@login_required
@require_POST
def mark_all_read_view(request):
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    _invalidate_unread(request.user)
    messages.info(request, 'All notifications marked as read.')
    return redirect('notifications:list')


@admin_required
def broadcast_notice_view(request):
    if request.method == 'POST':
        audience = request.POST.get('audience', 'ALL')
        title = request.POST.get('title', '').strip()[:180]
        body = request.POST.get('message', '').strip()
        also_email = request.POST.get('send_email_copy') == '1'
        if not title or not body:
            messages.error(request, 'Provide both a title and notice body.')
        else:
            qs = CustomUser.objects.filter(is_active=True)
            if audience != 'ALL':
                qs = qs.filter(role=audience)
            count = 0
            for recipient in qs:
                create_notification(
                    recipient,
                    'GENERAL',
                    title,
                    body,
                    link='/notifications/',
                    send_email=also_email,
                )
                count += 1
            messages.success(request, f'Published notice to {count} active user accounts.')
            return redirect('notifications:list')

    return render(request, 'notifications/broadcast.html')


# ==============================================================================
# GMAIL SMTP MAILER CONSOLE (Overview, Compose, Recipients, Send History, Preview)
# ==============================================================================

def _smtp_status_meta() -> dict:
    resolved = resolve_active_smtp_credentials()
    return {
        'is_live_smtp': resolved['is_live_smtp'],
        'source': resolved['source'],
        'host': resolved['host'],
        'port': resolved['port'],
        'user': resolved['user'] or 'Not configured yet (open SMTP Settings in sidebar to connect Gmail)',
        'from_email': resolved['from_email'],
    }


@admin_required
def mailer_overview_view(request):
    total_logs = EmailDispatchLog.objects.count()
    sent_count = EmailDispatchLog.objects.filter(status__in=['SENT', 'CONSOLE']).count()
    failed_count = EmailDispatchLog.objects.filter(status='FAILED').count()
    recipients_with_email = CustomUser.objects.filter(is_active=True).exclude(email='').count()
    recent_logs = EmailDispatchLog.objects.select_related('sent_by', 'recipient_user')[:8]

    return render(request, 'notifications/mailer_overview.html', {
        'active_tab': 'overview',
        'smtp': _smtp_status_meta(),
        'mailer_templates': MAILER_TEMPLATES,
        'stats': {
            'total_logs': total_logs,
            'sent_count': sent_count,
            'failed_count': failed_count,
            'recipients_count': recipients_with_email,
        },
        'recent_logs': recent_logs,
    })


@admin_required
def mailer_compose_view(request):
    users_qs = CustomUser.objects.filter(is_active=True).order_by('role', 'first_name', 'username')
    preselected_uid = request.GET.get('uid', '').strip()
    preselected_tpl = request.GET.get('template', 'consultation_scheduled').strip()
    if preselected_tpl not in MAILER_TEMPLATE_MAP:
        preselected_tpl = 'consultation_scheduled'

    if request.method == 'POST':
        tpl_key = request.POST.get('template_key', 'general_update').strip()
        if tpl_key not in MAILER_TEMPLATE_MAP:
            tpl_key = 'general_update'
        meta = MAILER_TEMPLATE_MAP[tpl_key]

        target_mode = request.POST.get('target_mode', 'single')
        custom_email = request.POST.get('custom_email', '').strip()
        custom_name = request.POST.get('custom_name', '').strip()
        selected_user_ids = request.POST.getlist('recipient_ids')
        role_group = request.POST.get('role_group', '').strip()

        targets = []  # list of (CustomUser | None, name, email, ref_code)
        if target_mode == 'custom':
            if custom_email:
                targets.append((None, custom_name or custom_email.split('@')[0], custom_email, request.POST.get('reference_code', '').strip() or 'MC-2026-BD-EXT'))
        elif target_mode == 'role' and role_group in ('PATIENT', 'DOCTOR', 'STAFF', 'ADMIN', 'ALL'):
            r_qs = users_qs.exclude(email='')
            if role_group != 'ALL':
                r_qs = r_qs.filter(role=role_group)
            for u in r_qs:
                targets.append((u, u.first_name or u.get_full_name() or u.username, u.email, f"MC-2026-BD-{u.id:04d}"))
        else:
            if selected_user_ids:
                for u in users_qs.filter(id__in=selected_user_ids).exclude(email=''):
                    targets.append((u, u.first_name or u.get_full_name() or u.username, u.email, f"MC-2026-BD-{u.id:04d}"))

        if not targets:
            messages.error(request, 'Select at least one recipient with an email address (or enter a direct email).')
        else:
            subject_input = request.POST.get('subject', '').strip() or meta['default_subject']
            ok_total = 0
            fail_total = 0
            last_err = ''

            for user_obj, r_name, r_email, auto_ref in targets:
                post_copy = request.POST.copy()
                if not post_copy.get('reference_code'):
                    post_copy['reference_code'] = auto_ref
                ctx = _build_email_context(tpl_key, post_copy, recipient_name=r_name)
                ctx['subject'] = subject_input
                html_body = render_to_string(f'emails/{tpl_key}.html', ctx)

                sent, mode_or_err = dispatch_email_alert(
                    to_email=r_email,
                    subject=subject_input,
                    html_body=html_body,
                )
                EmailDispatchLog.objects.create(
                    sent_by=request.user,
                    recipient_user=user_obj,
                    recipient_name=r_name,
                    recipient_email=r_email,
                    template_key=tpl_key,
                    subject=subject_input,
                    reference_code=ctx['reference_code'],
                    status=mode_or_err if sent else 'FAILED',
                    error_message='' if sent else mode_or_err,
                    payload_snapshot={
                        'consultation_type': ctx['consultation_type'],
                        'consultation_date': ctx['consultation_date'],
                        'consultation_time': ctx['consultation_time'],
                        'doctor_name': ctx['doctor_name'],
                    },
                )
                if sent:
                    ok_total += 1
                    last_mode = mode_or_err
                else:
                    fail_total += 1
                    last_err = mode_or_err

            if ok_total and not fail_total:
                if last_mode == 'CONSOLE':
                    messages.warning(
                        request,
                        'CONSOLE PREVIEW ONLY (not delivered to Gmail yet): Open "SMTP Settings" in the left menu and save your Gmail address + 16-char Google App Password to send real emails.',
                    )
                else:
                    messages.success(request, f'Successfully delivered "{meta["short"]}" via live Gmail SMTP to {ok_total} recipient(s)!')
                return redirect('notifications:mailer_history')
            elif ok_total and fail_total:
                messages.warning(request, f'Sent {ok_total}, failed {fail_total}. Last error: {last_err}')
                return redirect('notifications:mailer_history')
            else:
                messages.error(request, f'Gmail SMTP Error: {last_err}. Check your Gmail address & 16-char App Password in SMTP Settings.')

    return render(request, 'notifications/mailer_compose.html', {
        'active_tab': 'compose',
        'smtp': _smtp_status_meta(),
        'mailer_templates': MAILER_TEMPLATES,
        'users': users_qs,
        'preselected_uid': preselected_uid,
        'preselected_tpl': preselected_tpl,
    })


@admin_required
def mailer_recipients_view(request):
    role_filter = request.GET.get('role', '').strip()
    q = request.GET.get('q', '').strip()
    qs = CustomUser.objects.filter(is_active=True).order_by('role', 'first_name', 'username')
    if role_filter:
        qs = qs.filter(role=role_filter)
    if q:
        qs = qs.filter(email__icontains=q) | qs.filter(username__icontains=q) | qs.filter(first_name__icontains=q) | qs.filter(last_name__icontains=q)

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'notifications/mailer_recipients.html', {
        'active_tab': 'recipients',
        'smtp': _smtp_status_meta(),
        'page_obj': page_obj,
        'role_filter': role_filter,
        'q': q,
    })


@admin_required
def mailer_history_view(request):
    tpl_filter = request.GET.get('template', '').strip()
    status_filter = request.GET.get('status', '').strip()
    qs = EmailDispatchLog.objects.select_related('sent_by', 'recipient_user').all()
    if tpl_filter:
        qs = qs.filter(template_key=tpl_filter)
    if status_filter:
        qs = qs.filter(status=status_filter)

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'notifications/mailer_history.html', {
        'active_tab': 'history',
        'smtp': _smtp_status_meta(),
        'mailer_templates': MAILER_TEMPLATES,
        'page_obj': page_obj,
        'tpl_filter': tpl_filter,
        'status_filter': status_filter,
    })


@admin_required
def mailer_preview_view(request, template_key: str):
    if template_key not in MAILER_TEMPLATE_MAP:
        raise Http404('Unknown email template.')
    sample_name = request.GET.get('name', 'Tanvir').strip() or 'Tanvir'
    ctx = _build_email_context(template_key, request.GET, recipient_name=sample_name)
    html = render_to_string(f'emails/{template_key}.html', ctx)
    return HttpResponse(html)


@admin_required
def mailer_smtp_settings_view(request):
    """
    Allows the Admin to save their Gmail address + 16-character Google App Password
    directly inside the browser (zero .env / .env.txt file headaches) and immediately
    fire a live test email to verify real inbox delivery.
    """
    cfg = MailerSMTPConfig.objects.order_by('-updated_at').first()
    if not cfg:
        cfg = MailerSMTPConfig(
            smtp_host=getattr(settings, 'EMAIL_HOST', 'smtp.gmail.com') or 'smtp.gmail.com',
            smtp_port=getattr(settings, 'EMAIL_PORT', 587) or 587,
            use_tls=True,
            gmail_address=getattr(settings, 'EMAIL_HOST_USER', ''),
            app_password=getattr(settings, 'EMAIL_HOST_PASSWORD', ''),
            sender_display_name='MediConnect Clinical Registry',
        )

    if request.method == 'POST':
        action = request.POST.get('action', 'save')
        cfg.is_enabled = request.POST.get('is_enabled') == '1'
        cfg.smtp_host = (request.POST.get('smtp_host') or 'smtp.gmail.com').strip()
        try:
            cfg.smtp_port = int(request.POST.get('smtp_port') or 587)
        except ValueError:
            cfg.smtp_port = 587
        security_mode = request.POST.get('security_mode', 'tls')
        cfg.use_tls = security_mode == 'tls'
        cfg.use_ssl = security_mode == 'ssl'
        cfg.gmail_address = (request.POST.get('gmail_address') or '').strip()
        entered_pass = (request.POST.get('app_password') or '').replace(' ', '').strip()
        if entered_pass:
            cfg.app_password = entered_pass
        cfg.sender_display_name = (request.POST.get('sender_display_name') or 'MediConnect Clinical Registry').strip()
        cfg.reply_to_email = (request.POST.get('reply_to_email') or cfg.gmail_address).strip()

        if not cfg.gmail_address or not cfg.app_password:
            messages.error(request, 'Please enter BOTH your Gmail address and your 16-character Google App Password.')
        else:
            cfg.save()
            if action == 'save_and_test':
                test_to = (request.POST.get('test_recipient') or cfg.gmail_address).strip()
                ctx = _build_email_context(
                    'general_update',
                    {
                        'headline': 'Gmail SMTP Connection Verified!',
                        'custom_body': f'Success! Your MediConnect Portal is now connected to live Gmail SMTP ({cfg.gmail_address}) and ready to deliver scheduled consultation invites, reminders, QR receipts, and login credentials.',
                    },
                    recipient_name=request.user.first_name or request.user.username,
                )
                html_body = render_to_string('emails/general_update.html', ctx)
                sent, mode_or_err = dispatch_email_alert(
                    to_email=test_to,
                    subject='MediConnect — Live Gmail SMTP Test Successful',
                    html_body=html_body,
                )
                EmailDispatchLog.objects.create(
                    sent_by=request.user,
                    recipient_name=request.user.username,
                    recipient_email=test_to,
                    template_key='general_update',
                    subject='MediConnect — Live Gmail SMTP Test Successful',
                    reference_code='MC-SMTP-TEST',
                    status=mode_or_err if sent else 'FAILED',
                    error_message='' if sent else mode_or_err,
                )
                if sent and mode_or_err == 'SENT':
                    messages.success(request, f'Gmail SMTP saved & LIVE test email delivered to {test_to}! Check your Gmail Inbox / Sent folder now.')
                    return redirect('notifications:mailer_history')
                else:
                    messages.error(request, f'Saved settings, but Gmail rejected the connection: {mode_or_err}')
            else:
                messages.success(request, f'Live Gmail SMTP credentials saved ({cfg.gmail_address}). All emails will now send to real inboxes!')
                return redirect('notifications:mailer_compose')

    return render(request, 'notifications/mailer_settings.html', {
        'active_tab': 'settings',
        'cfg': cfg,
        'smtp': _smtp_status_meta(),
    })

