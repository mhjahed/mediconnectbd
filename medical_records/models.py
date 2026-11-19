from django.conf import settings
from django.db import models


class MedicalRecord(models.Model):
    appointment = models.OneToOneField(
        'appointments.Appointment',
        on_delete=models.CASCADE,
        related_name='medical_record',
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='medical_records',
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='authored_records',
    )

    # Vitals
    blood_pressure = models.CharField(max_length=20, blank=True, help_text='e.g. 120/80 mmHg')
    pulse_rate = models.PositiveIntegerField(null=True, blank=True, help_text='bpm')
    temperature = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text='°F')
    spo2 = models.PositiveIntegerField(null=True, blank=True, help_text='%')
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)

    # SOAP Clinical Documentation
    chief_complaint = models.TextField(help_text='Subjective: Presenting complaint & history')
    examination_findings = models.TextField(blank=True, help_text='Objective: Clinical examination & vitals summary')
    diagnosis = models.CharField(max_length=300, help_text='Assessment / Primary Diagnosis')
    clinical_advice = models.TextField(blank=True, help_text='Plan: Dietary, lifestyle, and follow-up advice')
    follow_up_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['patient', '-created_at']),
            models.Index(fields=['doctor', '-created_at']),
        ]

    def __str__(self):
        return f"Rx #{self.pk} · {self.patient.get_full_name()} ({self.diagnosis})"


class Prescription(models.Model):
    FREQUENCY_CHOICES = [
        ('1+0+1', '1+0+1 (Morning & Night)'),
        ('1+1+1', '1+1+1 (Three times daily)'),
        ('0+0+1', '0+0+1 (Once at bedtime)'),
        ('1+0+0', '1+0+0 (Once every morning)'),
        ('SOS', 'SOS (As needed)'),
        ('STAT', 'STAT (Immediate single dose)'),
    ]
    MEAL_TIMING = [
        ('AFTER_MEAL', 'After Meal'),
        ('BEFORE_MEAL', 'Before Meal'),
        ('WITH_MEAL', 'With Meal'),
        ('ANYTIME', 'Anytime'),
    ]

    medical_record = models.ForeignKey(
        MedicalRecord,
        on_delete=models.CASCADE,
        related_name='prescriptions',
    )
    medication_name = models.CharField(max_length=150, help_text='e.g. Tab. Bisoprolol 2.5 mg')
    dosage = models.CharField(max_length=80, default='1 Tablet')
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default='1+0+1')
    meal_timing = models.CharField(max_length=20, choices=MEAL_TIMING, default='AFTER_MEAL')
    duration = models.CharField(max_length=60, default='14 Days')
    instructions = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.medication_name} ({self.frequency} · {self.duration})"


class LabTest(models.Model):
    STATUS_CHOICES = [
        ('ORDERED', 'Ordered'),
        ('COMPLETED', 'Completed'),
    ]

    medical_record = models.ForeignKey(
        MedicalRecord,
        on_delete=models.CASCADE,
        related_name='lab_tests',
    )
    test_name = models.CharField(max_length=160, help_text='e.g. CBC with ESR, Fasting Lipid Profile')
    clinical_notes = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='ORDERED')
    result_summary = models.TextField(blank=True)

    def __str__(self):
        return f"{self.test_name} ({self.status})"
