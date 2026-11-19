import secrets
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from core.utils import validate_uploaded_image


class CustomUser(AbstractUser):
    ROLE_CHOICES = [
        ('ADMIN', 'Admin'),
        ('DOCTOR', 'Doctor'),
        ('PATIENT', 'Patient'),
        ('NURSE', 'Nurse'),
        ('STAFF', 'General Staff'),
    ]

    role = models.CharField(max_length=12, choices=ROLE_CHOICES, default='PATIENT', db_index=True)
    phone = models.CharField(max_length=20, blank=True)
    profile_picture = models.ImageField(
        upload_to='profile_pics/',
        blank=True,
        null=True,
        validators=[validate_uploaded_image],
    )
    address = models.TextField(blank=True)
    date_of_birth = models.DateField(blank=True, null=True)
    gender = models.CharField(
        max_length=10,
        choices=[('MALE', 'Male'), ('FEMALE', 'Female'), ('OTHER', 'Other')],
        blank=True,
    )
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['role', 'is_active']),
        ]

    def __str__(self):
        name = self.get_full_name() or self.username
        return f"{name} ({self.get_role_display()})"

    @property
    def is_admin_user(self):
        return self.role == 'ADMIN' or self.is_superuser

    @property
    def is_doctor(self):
        return self.role == 'DOCTOR'

    @property
    def is_patient(self):
        return self.role == 'PATIENT'

    @property
    def is_nurse(self):
        return self.role == 'NURSE'

    @property
    def is_general_staff(self):
        return self.role == 'STAFF'

    @property
    def is_hospital_staff(self):
        return self.role in ('DOCTOR', 'NURSE', 'STAFF')

    @property
    def display_title(self):
        full = self.get_full_name() or self.username
        if self.role == 'DOCTOR' and not full.lower().startswith('dr.'):
            return f"Dr. {full}"
        return full

    @property
    def initials(self):
        if self.first_name and self.last_name:
            return f"{self.first_name[0]}{self.last_name[0]}".upper()
        return (self.username[:2] or 'MC').upper()

    @property
    def avatar_url(self):
        if self.profile_picture:
            try:
                return self.profile_picture.url
            except ValueError:
                pass
        seeded = {'dr_rahman', 'dr_nasrin', 'dr_farhan', 'dr_tahmina', 'dr_kamal', 'dr_rubaiya'}
        if self.username in seeded:
            return f"/static/images/doctors/{self.username}.webp"
        return ''


class PatientProfile(models.Model):
    BLOOD_GROUPS = [
        ('A+', 'A+'), ('A-', 'A-'),
        ('B+', 'B+'), ('B-', 'B-'),
        ('AB+', 'AB+'), ('AB-', 'AB-'),
        ('O+', 'O+'), ('O-', 'O-'),
    ]

    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='patient_profile')
    blood_group = models.CharField(max_length=5, choices=BLOOD_GROUPS, blank=True)
    allergies = models.TextField(blank=True, help_text='Known drug or food allergies')
    medical_conditions = models.TextField(blank=True, help_text='Existing medical conditions')
    emergency_contact_name = models.CharField(max_length=100, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    insurance_number = models.CharField(max_length=50, blank=True)

    def __str__(self):
        return f"Patient: {self.user.get_full_name() or self.user.username}"


class DoctorProfile(models.Model):
    SPECIALTIES = [
        ('CARDIOLOGY', 'Cardiology'),
        ('DERMATOLOGY', 'Dermatology'),
        ('ENDOCRINOLOGY', 'Endocrinology'),
        ('GASTROENTEROLOGY', 'Gastroenterology'),
        ('GENERAL', 'General Medicine'),
        ('GYNECOLOGY', 'Gynecology'),
        ('NEUROLOGY', 'Neurology'),
        ('ONCOLOGY', 'Oncology'),
        ('OPHTHALMOLOGY', 'Ophthalmology'),
        ('ORTHOPEDICS', 'Orthopedics'),
        ('PEDIATRICS', 'Pediatrics'),
        ('PSYCHIATRY', 'Psychiatry'),
        ('PULMONOLOGY', 'Pulmonology'),
        ('UROLOGY', 'Urology'),
        ('ENT', 'ENT (Ear, Nose, Throat)'),
        ('DENTISTRY', 'Dentistry'),
    ]

    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='doctor_profile')
    specialty = models.CharField(max_length=30, choices=SPECIALTIES, default='GENERAL', db_index=True)
    license_number = models.CharField(max_length=50, unique=True, verbose_name='BMDC Registration Number')
    experience_years = models.PositiveIntegerField(default=0)
    qualification = models.CharField(max_length=200, help_text='e.g. MBBS, FCPS (Medicine), MD')
    bio = models.TextField(blank=True)
    consultation_fee = models.DecimalField(max_digits=8, decimal_places=2, default=500.00)
    available_days = models.CharField(
        max_length=120,
        default='Mon,Tue,Wed,Thu,Sat',
        help_text='Comma-separated short days: Mon,Tue,Wed,Thu,Fri,Sat,Sun',
    )
    consultation_start_time = models.TimeField(default='09:00')
    consultation_end_time = models.TimeField(default='17:00')
    max_patients_per_day = models.PositiveIntegerField(default=16)
    is_available = models.BooleanField(default=True, db_index=True)
    department = models.CharField(max_length=100, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['specialty', 'is_available']),
        ]

    def __str__(self):
        return f"Dr. {self.user.get_full_name()} — {self.get_specialty_display()}"

    def get_available_days_list(self):
        return [d.strip() for d in self.available_days.split(',') if d.strip()]


class StaffProfile(models.Model):
    DEPARTMENTS = [
        ('EMERGENCY', 'Emergency Department'),
        ('ICU', 'Intensive Care Unit (ICU)'),
        ('OPD', 'Outpatient Department (OPD)'),
        ('PHARMACY', 'Pharmacy'),
        ('LABORATORY', 'Pathology & Laboratory'),
        ('RADIOLOGY', 'Radiology & Imaging'),
        ('RECEPTION', 'Front Desk & Reception'),
        ('ADMINISTRATION', 'Hospital Administration'),
        ('SECURITY', 'Ward Security'),
        ('MAINTENANCE', 'Facilities & Biomedical'),
    ]
    SHIFTS = [
        ('MORNING', 'Morning Shift (06:00 – 14:00)'),
        ('AFTERNOON', 'Afternoon Shift (14:00 – 22:00)'),
        ('NIGHT', 'Night Shift (22:00 – 06:00)'),
    ]

    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='staff_profile')
    department = models.CharField(max_length=30, choices=DEPARTMENTS, default='OPD', db_index=True)
    position_title = models.CharField(max_length=100, default='Registered Nurse')
    shift = models.CharField(max_length=15, choices=SHIFTS, default='MORNING')
    employee_code = models.CharField(max_length=30, unique=True)
    joining_date = models.DateField(default=timezone.localdate)

    def __str__(self):
        return f"{self.user.get_full_name()} — {self.get_department_display()}"


class AccessKey(models.Model):
    """
    Single-use registration key issued by Admin for Either:
      - DOCTOR (prefix MEDI-XXXX-XXXX-XXXX, binds specialty)
      - NURSE or STAFF (prefix STAFF-XXXX-XXXX-XXXX, binds department)
    """
    STATUS_CHOICES = [
        ('UNUSED', 'Unused'),
        ('USED', 'Used'),
        ('REVOKED', 'Revoked'),
    ]
    TARGET_ROLES = [
        ('DOCTOR', 'Doctor (MEDI-XXXX)'),
        ('NURSE', 'Nurse (STAFF-XXXX)'),
        ('STAFF', 'General Staff (STAFF-XXXX)'),
    ]

    key = models.CharField(max_length=32, unique=True, editable=False, db_index=True)
    target_role = models.CharField(max_length=12, choices=TARGET_ROLES, default='DOCTOR', db_index=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='generated_keys',
    )
    assigned_to = models.OneToOneField(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='used_access_key',
    )
    doctor_name_hint = models.CharField(max_length=100, blank=True, help_text='Intended recipient name')
    specialty_hint = models.CharField(max_length=30, choices=DoctorProfile.SPECIALTIES, blank=True)
    department_hint = models.CharField(max_length=30, choices=StaffProfile.DEPARTMENTS, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='UNUSED', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_unique_key(self.target_role)
        super().save(*args, **kwargs)

    @classmethod
    def generate_unique_key(cls, target_role='DOCTOR'):
        prefix = 'MEDI' if target_role == 'DOCTOR' else 'STAFF'
        alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # excludes ambiguous 0/O/1/I
        while True:
            blocks = [''.join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
            candidate = f"{prefix}-{'-'.join(blocks)}"
            if not cls.objects.filter(key=candidate).exists():
                return candidate

    @property
    def is_valid(self):
        if self.status != 'UNUSED':
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True

    def mark_used(self, user):
        self.status = 'USED'
        self.assigned_to = user
        self.used_at = timezone.now()
        self.save(update_fields=['status', 'assigned_to', 'used_at'])

    def revoke(self):
        if self.status == 'UNUSED':
            self.status = 'REVOKED'
            self.save(update_fields=['status'])

    def __str__(self):
        return f"{self.key} [{self.target_role} · {self.status}]"


class DailyAttendance(models.Model):
    """
    Daily shift check-in / check-out log for Doctors, Nurses, and General Staff.
    Enforced by ShiftAttendanceMiddleware prior to operational portal use.
    """
    STATUS_CHOICES = [
        ('PRESENT', 'On Shift / Present'),
        ('COMPLETED', 'Shift Completed'),
    ]

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='attendance_logs')
    date = models.DateField(default=timezone.localdate, db_index=True)
    check_in_time = models.DateTimeField(default=timezone.now)
    check_out_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='PRESENT')
    shift_note = models.CharField(max_length=160, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-date', '-check_in_time']
        constraints = [
            models.UniqueConstraint(fields=['user', 'date'], name='unique_daily_staff_attendance'),
        ]

    def __str__(self):
        return f"{self.user.username} · {self.date} ({self.status})"

    @classmethod
    def calculate_streak(cls, user):
        dates = list(
            cls.objects.filter(user=user)
            .order_by('-date')
            .values_list('date', flat=True)[:60]
        )
        if not dates:
            return 0
        today = timezone.localdate()
        streak = 0
        cursor = today if dates[0] == today else (today - timezone.timedelta(days=1))
        date_set = set(dates)
        while cursor in date_set:
            streak += 1
            cursor -= timezone.timedelta(days=1)
        return streak
