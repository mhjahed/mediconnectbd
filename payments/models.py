import base64
import io
import secrets
import qrcode
from django.conf import settings
from django.db import models
from django.utils import timezone


class Payment(models.Model):
    PAYMENT_METHODS = [
        ('BKASH', 'bKash'),
        ('NAGAD', 'Nagad'),
        ('ROCKET', 'Rocket (DBBL)'),
        ('UPAY', 'Upay'),
        ('BANK_TRANSFER', 'Bank Transfer (NPSB / BEFTN)'),
    ]
    STATUS_CHOICES = [
        ('PENDING', 'Pending Verification'),
        ('VERIFIED', 'Verified'),
        ('REJECTED', 'Rejected'),
        ('REFUNDED', 'Refunded'),
    ]

    appointment = models.ForeignKey(
        'appointments.Appointment',
        on_delete=models.CASCADE,
        related_name='payments',
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='payments_made',
    )
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS)
    transaction_id = models.CharField(max_length=80, unique=True, db_index=True)
    sender_number = models.CharField(max_length=30, help_text='Sender MFS number or bank account tail')
    receipt_number = models.CharField(max_length=30, unique=True, editable=False, db_index=True)
    qr_token = models.CharField(max_length=36, unique=True, blank=True, db_index=True, editable=False)
    idempotency_key = models.CharField(max_length=80, blank=True, db_index=True)

    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='PENDING', db_index=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_payments',
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    # Physical Clinic Reception QR Check-In Tracking
    patient_checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='checked_in_receipts',
    )

    rejection_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['patient', '-created_at']),
        ]

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            stamp = timezone.now().strftime('%Y%m%d')
            self.receipt_number = f"RCPT-{stamp}-{secrets.token_hex(3).upper()}"
        if not self.qr_token:
            alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
            while True:
                cand = 'PASS-' + ''.join(secrets.choice(alphabet) for _ in range(10))
                if not Payment.objects.filter(qr_token=cand).exists():
                    self.qr_token = cand
                    break
        super().save(*args, **kwargs)

    @property
    def qr_payload(self):
        return f"MC-PASS|{self.receipt_number}|{self.qr_token}"

    @property
    def qr_data_uri(self):
        qr = qrcode.QRCode(
            version=2,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=6,
            border=2,
        )
        qr.add_data(self.qr_payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color='#282D31', back_color='#FFFFFF')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        return f"data:image/png;base64,{b64}"

    def __str__(self):
        return f"{self.receipt_number} · BDT {self.amount} ({self.get_payment_method_display()} · {self.status})"
