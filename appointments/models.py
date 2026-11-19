import datetime
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone


class Availability(models.Model):
    DAYS_OF_WEEK = [
        (0, 'Monday'),
        (1, 'Tuesday'),
        (2, 'Wednesday'),
        (3, 'Thursday'),
        (4, 'Friday'),
        (5, 'Saturday'),
        (6, 'Sunday'),
    ]

    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='availabilities',
        limit_choices_to={'role': 'DOCTOR'},
    )
    day_of_week = models.IntegerField(choices=DAYS_OF_WEEK)
    start_time = models.TimeField()
    end_time = models.TimeField()
    slot_duration = models.PositiveIntegerField(default=30, help_text='Minutes per slot')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['day_of_week', 'start_time']
        constraints = [
            models.UniqueConstraint(
                fields=['doctor', 'day_of_week', 'start_time'],
                name='unique_doctor_weekday_slot_start',
            )
        ]

    def clean(self):
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError('End time must be after start time.')

    def __str__(self):
        return f"{self.doctor.display_title} · {self.get_day_of_week_display()} ({self.start_time:%H:%M}–{self.end_time:%H:%M})"


class Appointment(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending Payment / Verification'),
        ('CONFIRMED', 'Confirmed'),
        ('IN_PROGRESS', 'In Consultation'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
        ('NO_SHOW', 'No Show'),
    ]
    PAYMENT_STATUS_CHOICES = [
        ('UNPAID', 'Unpaid'),
        ('PENDING_VERIFICATION', 'Submitted — Pending Verification'),
        ('PAID', 'Verified Paid'),
        ('REFUNDED', 'Refunded'),
        ('NOT_REQUIRED', 'No Payment Required (Free Follow-Up)'),
    ]

    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='patient_appointments',
        limit_choices_to={'role': 'PATIENT'},
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='doctor_appointments',
        limit_choices_to={'role': 'DOCTOR'},
    )
    appointment_date = models.DateField(db_index=True)
    appointment_time = models.TimeField()
    end_time = models.TimeField(blank=True, null=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='PENDING', db_index=True)
    payment_status = models.CharField(max_length=25, choices=PAYMENT_STATUS_CHOICES, default='UNPAID', db_index=True)
    reason = models.TextField(help_text='Presenting symptoms or reason for consultation')
    notes = models.TextField(blank=True)

    meet_link = models.URLField(blank=True)
    meet_link_added_at = models.DateTimeField(null=True, blank=True)
    reminder_sent = models.BooleanField(default=False)

    consultation_fee = models.DecimalField(max_digits=8, decimal_places=2, default=500.00)
    idempotency_key = models.CharField(max_length=80, blank=True, db_index=True)

    # --- Follow-up / continuity of care -------------------------------------
    VISIT_TYPE_CHOICES = [
        ('NEW', 'New Consultation'),
        ('FOLLOW_UP', 'Follow-Up Visit'),
    ]
    visit_type = models.CharField(
        max_length=12, choices=VISIT_TYPE_CHOICES, default='NEW', db_index=True,
        help_text='Follow-ups reuse the previous clinical record and are free of charge.',
    )
    parent_appointment = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='follow_ups',
        help_text='The consultation this visit is a follow-up of.',
    )
    is_free_follow_up = models.BooleanField(
        default=False,
        help_text='Doctor-authorized free review (fee waived; no payment receipt required).',
    )
    follow_up_reference = models.DateField(
        null=True, blank=True,
        help_text='Doctor-advised review date copied from the previous SOAP record.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['appointment_date', 'appointment_time']
        indexes = [
            models.Index(fields=['doctor', 'appointment_date', 'status']),
            models.Index(fields=['patient', 'appointment_date']),
            models.Index(fields=['payment_status', 'status']),
        ]

    def __str__(self):
        return f"#{self.pk} · {self.patient.get_full_name()} → {self.doctor.display_title} ({self.appointment_date} {self.appointment_time:%H:%M})"

    @property
    def scheduled_datetime(self):
        naive = datetime.datetime.combine(self.appointment_date, self.appointment_time)
        return timezone.make_aware(naive, timezone.get_current_timezone())

    @property
    def is_follow_up(self):
        return self.visit_type == 'FOLLOW_UP'

    @property
    def payment_exempt(self):
        """Free follow-ups skip the mobile-banking verification step entirely."""
        return self.is_free_follow_up or self.payment_status == 'NOT_REQUIRED'

    @property
    def can_cancel(self):
        """Patients can cancel at least 24 hours before consultation start."""
        if self.status in ('COMPLETED', 'CANCELLED', 'NO_SHOW'):
            return False
        delta = self.scheduled_datetime - timezone.now()
        return delta.total_seconds() >= 24 * 3600

    @property
    def can_join_meet(self):
        if not self.meet_link or self.payment_status != 'PAID':
            return False
        return self.status in ('CONFIRMED', 'IN_PROGRESS')


class DoctorReview(models.Model):
    """
    Verified post-visit patient review. Never seeded artificially unless attached to a real COMPLETED appointment.
    """
    appointment = models.OneToOneField(Appointment, on_delete=models.CASCADE, related_name='review')
    patient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews_given')
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews_received')
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(max_length=800, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.rating}/5 for {self.doctor.display_title} by {self.patient.get_full_name()}"
