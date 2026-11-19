import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from appointments.models import Appointment
from notifications.models import create_notification


class Command(BaseCommand):
    help = 'Dispatches 10-minute pre-consultation reminders to patients and doctors.'

    def handle(self, *args, **options):
        now = timezone.now()
        window_end = now + datetime.timedelta(minutes=15)
        today = timezone.localdate()

        candidates = (
            Appointment.objects.filter(
                appointment_date=today,
                payment_status='PAID',
                status='CONFIRMED',
                reminder_sent=False,
            )
            .select_related('patient', 'doctor')
        )

        sent_count = 0
        for appt in candidates:
            sched = appt.scheduled_datetime
            if now <= sched <= window_end:
                create_notification(
                    recipient=appt.patient,
                    notification_type='APPOINTMENT_REMINDER',
                    title='Consultation Starting in 10 Minutes',
                    message=f"Your video consultation with {appt.doctor.display_title} starts at {appt.appointment_time.strftime('%I:%M %p')}.",
                    link=f"/appointments/{appt.pk}/",
                )
                create_notification(
                    recipient=appt.doctor,
                    notification_type='APPOINTMENT_REMINDER',
                    title='Next Patient in 10 Minutes',
                    message=f"Consultation with {appt.patient.get_full_name()} starts at {appt.appointment_time.strftime('%I:%M %p')}.",
                    link=f"/appointments/{appt.pk}/",
                )
                appt.reminder_sent = True
                appt.save(update_fields=['reminder_sent'])
                sent_count += 1

        self.stdout.write(self.style.SUCCESS(f"Dispatched reminders for {sent_count} upcoming consultations."))
