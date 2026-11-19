import io
import secrets
import barcode
from barcode.writer import SVGWriter, ImageWriter
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from django.utils import timezone


HOSPITAL_ZONES = [
    # Ground Floor
    ('MAIN_ENTRANCE', 'Ground · 01 Main Entrance & Triage'),
    ('OPD_WING', 'Ground · 02 Outpatient Consultation Wing'),
    ('EMERGENCY_BAY', 'Ground · 03 Emergency & Trauma Bay'),
    ('PHARMACY_VAULT', 'Ground · 04 Central Pharmacy Dispensary'),
    ('RECEPTION_DESK', 'Ground · 05 Registration & Billing Desk'),
    # Floor 1 — Diagnostics
    ('PATHOLOGY_LAB', 'Floor 1 · 06 Pathology & Hematology Lab'),
    ('MICROBIOLOGY_LAB', 'Floor 1 · 07 Microbiology & PCR Lab'),
    ('RADIOLOGY_XRAY', 'Floor 1 · 08 Radiology & Digital X-Ray'),
    ('MRI_CT_SUITE', 'Floor 1 · 09 MRI & CT Imaging Suite'),
    ('BLOOD_BANK', 'Floor 1 · 10 Blood Bank & Transfusion'),
    # Floor 2 — Critical Care & Theatres
    ('ICU_GENERAL', 'Floor 2 · 11 General Intensive Care (ICU)'),
    ('CCU_CARDIAC', 'Floor 2 · 12 Coronary Care Unit (CCU)'),
    ('NICU_PEDIATRIC', 'Floor 2 · 13 Neonatal Intensive Care (NICU)'),
    ('OT_COMPLEX_A', 'Floor 2 · 14 Operation Theatre Complex A'),
    ('OT_COMPLEX_B', 'Floor 2 · 15 Operation Theatre Complex B'),
    ('STERILE_CSSD', 'Floor 2 · 16 Central Sterile Supply (CSSD)'),
    # Floor 3 — Inpatient Wards
    ('WARD_MEDICINE', 'Floor 3 · 17 Internal Medicine Inpatient Ward'),
    ('WARD_SURGERY', 'Floor 3 · 18 Surgical Recovery Ward'),
    ('WARD_MATERNITY', 'Floor 3 · 19 Maternity & Gynecology Ward'),
    # Floor 4 — Administration & Restricted Infrastructure
    ('MEDICAL_RECORDS_ARCHIVE', 'Floor 4 · 20 Clinical Records Archive'),
    ('ADMIN_BOARDROOM', 'Floor 4 · 21 Executive & Finance Registry'),
    ('SERVER_BIOMED', 'Floor 4 · 22 Core Server & Telemetry Room'),
]

ZONE_LABEL_MAP = dict(HOSPITAL_ZONES)


class StaffIDCard(models.Model):
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('SUSPENDED', 'Suspended'),
        ('EXPIRED', 'Expired'),
        ('REVOKED', 'Revoked'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='id_card',
    )
    staff_id_number = models.CharField(max_length=12, unique=True, db_index=True, editable=False)
    barcode_value = models.CharField(max_length=24, unique=True, db_index=True, editable=False)
    barcode_svg = models.TextField(blank=True, editable=False)
    barcode_image = models.ImageField(upload_to='barcodes/', blank=True, null=True)

    department_label = models.CharField(max_length=100, default='Outpatient Department')
    blood_group = models.CharField(max_length=5, blank=True, default='O+')
    allowed_zones = models.JSONField(default=list, help_text='List of authorized hospital zone codes')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='ACTIVE', db_index=True)

    issued_date = models.DateField(default=timezone.localdate)
    expiry_date = models.DateField()
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='issued_id_cards',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.staff_id_number:
            while True:
                cand = f"{secrets.randbelow(90000000) + 10000000}"
                if not StaffIDCard.objects.filter(staff_id_number=cand).exists():
                    self.staff_id_number = cand
                    break

        if not self.barcode_value:
            alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
            while True:
                code = 'MEDI' + ''.join(secrets.choice(alphabet) for _ in range(8))
                if not StaffIDCard.objects.filter(barcode_value=code).exists():
                    self.barcode_value = code
                    break

        if not self.expiry_date:
            self.expiry_date = timezone.localdate() + timezone.timedelta(days=365 * 2)

        if not self.barcode_svg:
            self._render_code128_assets()

        super().save(*args, **kwargs)

    def _render_code128_assets(self):
        code128_cls = barcode.get_barcode_class('code128')
        svg_buf = io.BytesIO()
        code128_cls(self.barcode_value, writer=SVGWriter()).write(
            svg_buf,
            options={
                'module_width': 0.32,
                'module_height': 12.0,
                'quiet_zone': 2.5,
                'font_size': 9,
                'text_distance': 3.5,
                'background': '#FFFFFF',
                'foreground': '#282D31',
            },
        )
        self.barcode_svg = svg_buf.getvalue().decode('utf-8')

        png_buf = io.BytesIO()
        code128_cls(self.barcode_value, writer=ImageWriter()).write(
            png_buf,
            options={
                'module_width': 0.35,
                'module_height': 14.0,
                'quiet_zone': 3.0,
                'write_text': True,
            },
        )
        self.barcode_image.save(
            f"{self.barcode_value}.png",
            ContentFile(png_buf.getvalue()),
            save=False,
        )

    @property
    def is_expired(self):
        return timezone.localdate() > self.expiry_date

    def evaluate_room_access(self, zone_code: str):
        """
        Strict room-level authorization check (fixes original repository's room-bypass flaw):
          1. Account must be active.
          2. Card status must be ACTIVE.
          3. Card must not be past expiry_date.
          4. Requested `zone_code` MUST exist in `allowed_zones` (unless user is ADMIN).
        Returns tuple: (granted: bool, reason_code: str, human_message: str)
        """
        if not self.user.is_active:
            return False, 'ACCOUNT_INACTIVE', 'Staff user account is deactivated.'
        if self.status != 'ACTIVE':
            return False, f'CARD_{self.status}', f'ID badge status is {self.get_status_display()}.'
        if self.is_expired:
            return False, 'CARD_EXPIRED', f'ID badge expired on {self.expiry_date.strftime("%d %b %Y")}.'
        if self.user.is_admin_user:
            return True, 'GRANTED_ADMIN', 'Full clinical administrative clearance.'
        if zone_code not in (self.allowed_zones or []):
            zone_name = ZONE_LABEL_MAP.get(zone_code, zone_code)
            return False, 'ZONE_RESTRICTED', f'Badge is not authorized for {zone_name}.'
        return True, 'GRANTED', 'Room clearance verified.'

    def get_allowed_zone_labels(self):
        return [ZONE_LABEL_MAP.get(z, z) for z in (self.allowed_zones or [])]

    def __str__(self):
        return f"Badge {self.barcode_value} · {self.user.display_title} [{self.status}]"


class AccessLog(models.Model):
    RESULT_CHOICES = [
        ('GRANTED', 'Access Granted'),
        ('DENIED', 'Access Denied'),
    ]

    card = models.ForeignKey(
        StaffIDCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='scan_logs',
    )
    scanned_code = models.CharField(max_length=64, db_index=True)
    zone_code = models.CharField(max_length=40, choices=HOSPITAL_ZONES, db_index=True)
    result = models.CharField(max_length=10, choices=RESULT_CHOICES, db_index=True)
    reason_code = models.CharField(max_length=40)
    message = models.CharField(max_length=240)
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='performed_scans',
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    scanned_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-scanned_at']
        indexes = [
            models.Index(fields=['result', '-scanned_at']),
            models.Index(fields=['zone_code', '-scanned_at']),
        ]

    @property
    def zone_label(self):
        return ZONE_LABEL_MAP.get(self.zone_code, self.zone_code)

    def __str__(self):
        return f"[{self.result}] {self.scanned_code} @ {self.zone_code} ({self.scanned_at:%Y-%m-%d %H:%M})"
