import datetime
from django import forms
from django.utils import timezone
from accounts.forms import NeuFormMixin
from .models import Availability, Appointment, DoctorReview


def generate_available_slots(doctor, target_date: datetime.date, min_lead_days=3):
    """
    Generates 30-minute consultation slots for `doctor` on `target_date`.
    Enforces:
      1. Minimum 3-day (72-hour) advance booking rule from local today.
      2. Doctor's explicit Availability records for that weekday (or fallback to DoctorProfile hours).
      3. Excludes slots already booked with non-cancelled appointments.
    """
    min_date = timezone.localdate() + datetime.timedelta(days=min_lead_days)
    if target_date < min_date:
        return []

    weekday = target_date.weekday()
    windows = list(
        Availability.objects.filter(doctor=doctor, day_of_week=weekday, is_active=True)
        .order_by('start_time')
    )

    # Fallback to DoctorProfile day string + start/end hours when custom Availability rows aren't set
    if not windows and hasattr(doctor, 'doctor_profile'):
        prof = doctor.doctor_profile
        short_day = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][weekday]
        if short_day in prof.get_available_days_list():
            windows = [
                Availability(
                    doctor=doctor,
                    day_of_week=weekday,
                    start_time=prof.consultation_start_time,
                    end_time=prof.consultation_end_time,
                    slot_duration=30,
                )
            ]

    booked_times = set(
        Appointment.objects.filter(
            doctor=doctor,
            appointment_date=target_date,
        )
        .exclude(status='CANCELLED')
        .values_list('appointment_time', flat=True)
    )

    slots = []
    for win in windows:
        step = datetime.timedelta(minutes=win.slot_duration or 30)
        cursor = datetime.datetime.combine(target_date, win.start_time)
        limit = datetime.datetime.combine(target_date, win.end_time)
        while cursor + step <= limit:
            slot_time = cursor.time()
            slots.append({
                'time_value': slot_time.strftime('%H:%M'),
                'label': f"{slot_time.strftime('%I:%M %p')} – {(cursor + step).time().strftime('%I:%M %p')}",
                'is_booked': slot_time in booked_times,
            })
            cursor += step
    return slots


class AvailabilityForm(NeuFormMixin, forms.ModelForm):
    class Meta:
        model = Availability
        fields = ['day_of_week', 'start_time', 'end_time', 'slot_duration', 'is_active']
        widgets = {
            'start_time': forms.TimeInput(attrs={'type': 'time'}),
            'end_time': forms.TimeInput(attrs={'type': 'time'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class MeetLinkForm(NeuFormMixin, forms.Form):
    meet_link = forms.URLField(
        required=True,
        label='Google Meet Consultation URL',
        widget=forms.URLInput(attrs={'placeholder': 'https://meet.google.com/xxx-xxxx-xxx'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()

    def clean_meet_link(self):
        url = self.cleaned_data['meet_link'].strip()
        if not url.startswith('https://'):
            raise forms.ValidationError('Video consultation link must use HTTPS (e.g. https://meet.google.com/...).')
        return url


class DoctorReviewForm(NeuFormMixin, forms.ModelForm):
    class Meta:
        model = DoctorReview
        fields = ['rating', 'comment']
        widgets = {
            'rating': forms.Select(choices=[(i, f"{i} / 5") for i in range(5, 0, -1)]),
            'comment': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Share factual feedback from your completed consultation...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
