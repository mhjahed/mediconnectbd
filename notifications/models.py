import logging
import requests
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import models
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger('mediconnect')


class Notification(models.Model):
    TYPE_CHOICES = [
        ('APPOINTMENT_BOOKED', 'Appointment Booked'),
        ('APPOINTMENT_CONFIRMED', 'Appointment Confirmed'),
        ('APPOINTMENT_CANCELLED', 'Appointment Cancelled'),
        ('APPOINTMENT_REMINDER', 'Consultation Reminder'),
        ('PAYMENT_RECEIVED', 'Payment Submitted'),
        ('PAYMENT_VERIFIED', 'Payment Verified'),
        ('PAYMENT_REJECTED', 'Payment Rejected'),
        ('MEET_LINK_SET', 'Video Consultation Link Ready'),
        ('PRESCRIPTION_READY', 'Signed Prescription Available'),
        ('CREDENTIALS_ISSUED', 'Portal Credentials & Onboarding Key'),
        ('GENERAL', 'Hospital Notice'),
    ]

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    notification_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default='GENERAL', db_index=True)
    title = models.CharField(max_length=180)
    message = models.TextField()
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    email_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read', '-created_at']),
        ]

    def __str__(self):
        return f"[{self.notification_type}] {self.recipient.username}: {self.title}"


class EmailDispatchLog(models.Model):
    """
    Audit history for the MediConnect Gmail SMTP Mailer Console:
    tracks every template sent, recipient address, reference code, and delivery outcome.
    """
    TEMPLATE_CHOICES = [
        ('consultation_scheduled', 'Consultation Scheduled — Video / Outpatient'),
        ('consultation_reminder', '24-Hour Consultation Reminder'),
        ('payment_receipt', 'Payment Verified & Reception QR Pass'),
        ('outpatient_checkin', 'Outpatient Arrival & Document Prep'),
        ('prescription_ready', 'Signed SOAP Prescription & Lab Summary'),
        ('credentials_delivery', 'Account Ready & Portal Credentials / Key'),
        ('staff_onboarding', 'Clinician & Ward Badge Onboarding'),
        ('general_update', 'General Portal Update / Custom Message'),
    ]

    STATUS_CHOICES = [
        ('SENT', 'Sent via SMTP'),
        ('CONSOLE', 'Logged to Dev Console'),
        ('FAILED', 'Delivery Failed'),
    ]

    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='dispatched_emails',
    )
    recipient_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='received_emails',
    )
    recipient_name = models.CharField(max_length=150, blank=True)
    recipient_email = models.EmailField(db_index=True)
    template_key = models.CharField(max_length=40, choices=TEMPLATE_CHOICES, default='general_update', db_index=True)
    subject = models.CharField(max_length=220)
    reference_code = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='SENT', db_index=True)
    error_message = models.TextField(blank=True)
    payload_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.template_key} -> {self.recipient_email} [{self.status}]"


class MailerSMTPConfig(models.Model):
    """
    Singleton-style in-portal Gmail SMTP Configuration so the Administrator
    can enter their Gmail address & 16-character App Password directly inside
    the web UI (/notifications/mailer/settings/) without fighting Windows .env/.env.txt files.
    """
    is_enabled = models.BooleanField(default=True)
    smtp_host = models.CharField(max_length=120, default='smtp.gmail.com')
    smtp_port = models.PositiveIntegerField(default=587)
    use_tls = models.BooleanField(default=True)
    use_ssl = models.BooleanField(default=False)
    gmail_address = models.EmailField(blank=True, help_text='Your Gmail address, e.g. myclinic@gmail.com')
    app_password = models.CharField(max_length=128, blank=True, help_text='16-character Google App Password (spaces stripped automatically)')
    sender_display_name = models.CharField(max_length=120, default='MediConnect Clinical Registry')
    reply_to_email = models.EmailField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Gmail SMTP Configuration'
        verbose_name_plural = 'Gmail SMTP Configuration'

    def save(self, *args, **kwargs):
        if self.app_password:
            self.app_password = self.app_password.replace(' ', '').strip()
        if self.gmail_address:
            self.gmail_address = self.gmail_address.strip()
        super().save(*args, **kwargs)

    @classmethod
    def get_active(cls):
        try:
            cfg = cls.objects.order_by('-updated_at').first()
            if cfg and cfg.is_enabled and cfg.gmail_address and cfg.app_password:
                return cfg
        except Exception:
            pass
        return None


def resolve_active_smtp_credentials() -> dict:
    """
    Resolves SMTP credentials with priority:
    1) Portal DB MailerSMTPConfig (saved in /notifications/mailer/settings/)
    2) Environment / .env variables (EMAIL_HOST_USER + EMAIL_HOST_PASSWORD)
    3) Fallback Console Preview mode
    """
    db_cfg = MailerSMTPConfig.get_active()
    if db_cfg:
        from_header = f"{db_cfg.sender_display_name} <{db_cfg.gmail_address}>" if db_cfg.sender_display_name else db_cfg.gmail_address
        return {
            'is_live_smtp': True,
            'source': 'Portal SMTP Settings',
            'host': db_cfg.smtp_host or 'smtp.gmail.com',
            'port': db_cfg.smtp_port or 587,
            'use_tls': db_cfg.use_tls,
            'use_ssl': db_cfg.use_ssl,
            'user': db_cfg.gmail_address,
            'password': db_cfg.app_password.replace(' ', '').strip(),
            'from_email': from_header,
            'reply_to': db_cfg.reply_to_email or db_cfg.gmail_address,
        }

    env_user = getattr(settings, 'EMAIL_HOST_USER', '').strip()
    env_pass = getattr(settings, 'EMAIL_HOST_PASSWORD', '').replace(' ', '').strip()
    if env_user and env_pass:
        return {
            'is_live_smtp': True,
            'source': '.env File',
            'host': getattr(settings, 'EMAIL_HOST', 'smtp.gmail.com'),
            'port': getattr(settings, 'EMAIL_PORT', 587),
            'use_tls': getattr(settings, 'EMAIL_USE_TLS', True),
            'use_ssl': getattr(settings, 'EMAIL_USE_SSL', False),
            'user': env_user,
            'password': env_pass,
            'from_email': f"MediConnect Clinical Registry <{env_user}>",
            'reply_to': getattr(settings, 'SUPPORT_CONTACT_EMAIL', env_user),
        }

    return {
        'is_live_smtp': False,
        'source': 'Console Mode (No Gmail Credentials Configured Yet)',
        'host': getattr(settings, 'EMAIL_HOST', 'smtp.gmail.com'),
        'port': getattr(settings, 'EMAIL_PORT', 587),
        'use_tls': True,
        'use_ssl': False,
        'user': '',
        'password': '',
        'from_email': getattr(settings, 'DEFAULT_FROM_EMAIL', 'MediConnect Registry <noreply@mediconnect.bd>'),
        'reply_to': getattr(settings, 'SUPPORT_CONTACT_EMAIL', 'support@mediconnect.bd'),
    }


def dispatch_email_alert(
    to_email: str,
    subject: str,
    body: str = '',
    html_body: str = '',
    reply_to: str = '',
) -> tuple[bool, str]:
    """
    Sends multipart (HTML + plain-text) transactional email via live Gmail SMTP
    whenever Portal SMTP Settings or .env credentials exist; otherwise logs to console.
    Returns (success_bool, 'SENT' | 'CONSOLE' | error_string).
    """
    if not to_email:
        return False, 'Missing recipient email address.'

    plain_text = body or (strip_tags(html_body).strip() if html_body else subject)
    resend_key = getattr(settings, 'RESEND_API_KEY', '').strip()

    if resend_key:
        try:
            payload = {
                'from': settings.DEFAULT_FROM_EMAIL,
                'to': [to_email],
                'subject': subject,
                'text': plain_text,
            }
            if html_body:
                payload['html'] = html_body
            resp = requests.post(
                'https://api.resend.com/emails',
                headers={
                    'Authorization': f'Bearer {resend_key}',
                    'Content-Type': 'application/json',
                },
                json=payload,
                timeout=8,
            )
            if resp.status_code in (200, 201):
                return True, 'SENT'
        except Exception as exc:
            logger.warning("Resend dispatch error: %s", exc)

    smtp_cfg = resolve_active_smtp_credentials()
    effective_reply = reply_to or smtp_cfg['reply_to']
    reply_list = [effective_reply] if effective_reply else None

    try:
        if smtp_cfg['is_live_smtp']:
            connection = get_connection(
                backend='django.core.mail.backends.smtp.EmailBackend',
                host=smtp_cfg['host'],
                port=smtp_cfg['port'],
                username=smtp_cfg['user'],
                password=smtp_cfg['password'],
                use_tls=smtp_cfg['use_tls'],
                use_ssl=smtp_cfg['use_ssl'],
                timeout=15,
            )
            msg = EmailMultiAlternatives(
                subject=subject,
                body=plain_text,
                from_email=smtp_cfg['from_email'],
                to=[to_email],
                reply_to=reply_list,
                connection=connection,
            )
            if html_body:
                msg.attach_alternative(html_body, 'text/html')
            msg.send(fail_silently=False)
            return True, 'SENT'

        # Fallback: no Gmail credentials configured yet -> Django's EMAIL_BACKEND (console/locmem in tests)
        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_text,
            from_email=smtp_cfg['from_email'],
            to=[to_email],
            reply_to=reply_list,
        )
        if html_body:
            msg.attach_alternative(html_body, 'text/html')
        msg.send(fail_silently=False)
        backend_path = getattr(settings, 'EMAIL_BACKEND', '')
        mode = 'CONSOLE' if 'console' in backend_path.lower() else 'SENT'
        return True, mode
    except Exception as exc:
        logger.warning("Gmail SMTP dispatch error: %s", exc)
        return False, str(exc)


def create_notification(recipient, notification_type, title, message, link='', send_email=True, extra_context=None):
    """
    Creates an in-portal notification AND automatically dispatches a branded
    MediConnect HTML email (with the 3-part top color bar + MediConnect Palette #06 styling).
    """
    notif = Notification.objects.create(
        recipient=recipient,
        notification_type=notification_type,
        title=title,
        message=message,
        link=link,
    )
    if send_email and recipient.email:
        site_url = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000').rstrip('/')
        cta_url = f"{site_url}{link}" if link and link.startswith('/') else (link or f"{site_url}/dashboard/")
        ref_code = f"MC-2026-BD-{recipient.id:04d}"

        # Map notification_type to our structured HTML email templates
        template_map = {
            'APPOINTMENT_CONFIRMED': ('consultation_scheduled', 'CONSULTATION SCHEDULED'),
            'MEET_LINK_SET': ('consultation_scheduled', 'VIDEO LINK READY'),
            'APPOINTMENT_REMINDER': ('consultation_reminder', 'CONSULTATION REMINDER'),
            'PAYMENT_VERIFIED': ('payment_receipt', 'PAYMENT VERIFIED'),
            'PRESCRIPTION_READY': ('prescription_ready', 'PRESCRIPTION READY'),
            'CREDENTIALS_ISSUED': ('credentials_delivery', 'ACCOUNT READY'),
        }
        tpl_key, badge_right = template_map.get(notification_type, ('general_update', 'PORTAL UPDATE'))

        ctx = {
            'recipient_name': recipient.first_name or recipient.get_full_name() or recipient.username,
            'header_tag': badge_right,
            'eyebrow': dict(Notification.TYPE_CHOICES).get(notification_type, 'PORTAL UPDATE').upper(),
            'headline': title,
            'lead_text': message,
            'custom_body': message,
            'cta_label': 'Open MediConnect Portal',
            'cta_url': cta_url,
            'reference_code': ref_code,
            'support_email': getattr(settings, 'SUPPORT_CONTACT_EMAIL', 'support@mediconnect.bd'),
            'site_url': site_url,
        }
        if extra_context:
            ctx.update(extra_context)

        try:
            html_body = render_to_string(f'emails/{tpl_key}.html', ctx)
        except Exception:
            html_body = ''

        plain_body = f"{message}\n\nPortal link: {cta_url}" if cta_url else message
        sent, mode_or_err = dispatch_email_alert(
            to_email=recipient.email,
            subject=f"MediConnect — {title}",
            body=plain_body,
            html_body=html_body,
        )
        EmailDispatchLog.objects.create(
            recipient_user=recipient,
            recipient_name=recipient.get_full_name() or recipient.username,
            recipient_email=recipient.email,
            template_key=tpl_key,
            subject=f"MediConnect — {title}",
            reference_code=ctx.get('reference_code', ref_code),
            status=mode_or_err if sent else 'FAILED',
            error_message='' if sent else mode_or_err,
        )
        if sent:
            notif.email_sent = True
            notif.save(update_fields=['email_sent'])
    return notif
