import datetime
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import doctor_required, patient_required
from accounts.models import CustomUser, DoctorProfile
from core.utils import generate_idempotency_token, claim_idempotency_token
from notifications.models import create_notification
from .forms import generate_available_slots, AvailabilityForm, MeetLinkForm, DoctorReviewForm
from .models import Availability, Appointment, DoctorReview


def doctor_search_view(request):
    specialty = request.GET.get('specialty', '').strip()
    q = request.GET.get('q', '').strip()
    max_fee = request.GET.get('max_fee', '').strip()

    qs = (
        CustomUser.objects.filter(role='DOCTOR', is_active=True, doctor_profile__is_available=True)
        .select_related('doctor_profile')
        .annotate(
            verified_review_count=Count('reviews_received', distinct=True),
            verified_avg_rating=Avg('reviews_received__rating'),
        )
        .order_by('first_name', 'last_name')
    )

    if specialty:
        qs = qs.filter(doctor_profile__specialty=specialty)
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(doctor_profile__qualification__icontains=q)
            | Q(doctor_profile__specialty__icontains=q)
            | Q(doctor_profile__department__icontains=q)
        )
    if max_fee.isdigit():
        qs = qs.filter(doctor_profile__consultation_fee__lte=int(max_fee))

    # Support fast JSON response for debounced live filtering
    if request.GET.get('format') == 'json':
        items = []
        for doc in qs[:24]:
            prof = doc.doctor_profile
            items.append({
                'id': doc.id,
                'name': doc.display_title,
                'initials': doc.initials,
                'avatar_url': doc.avatar_url,
                'specialty': prof.get_specialty_display(),
                'qualification': prof.qualification,
                'experience_years': prof.experience_years,
                'fee': str(prof.consultation_fee),
                'available_days': prof.available_days,
                'review_count': doc.verified_review_count or 0,
                'avg_rating': round(doc.verified_avg_rating, 1) if doc.verified_avg_rating else None,
                'book_url': f"/appointments/book/{doc.id}/",
                'detail_url': f"/appointments/specialist/{doc.id}/",
            })
        return JsonResponse({'count': len(items), 'results': items})

    paginator = Paginator(qs, 12)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'appointments/search.html', {
        'page_obj': page_obj,
        'specialties': DoctorProfile.SPECIALTIES,
        'selected_specialty': specialty,
        'q': q,
        'max_fee': max_fee,
    })


def specialist_detail_view(request, doctor_id):
    doctor = get_object_or_404(
        CustomUser.objects.select_related('doctor_profile').annotate(
            verified_review_count=Count('reviews_received', distinct=True),
            verified_avg_rating=Avg('reviews_received__rating'),
        ),
        pk=doctor_id,
        role='DOCTOR',
        is_active=True,
    )
    reviews = DoctorReview.objects.filter(doctor=doctor).select_related('patient')[:10]
    min_date = timezone.localdate() + datetime.timedelta(days=3)

    return render(request, 'appointments/specialist_detail.html', {
        'doctor': doctor,
        'reviews': reviews,
        'min_date': min_date,
    })


def slots_api_view(request, doctor_id):
    doctor = get_object_or_404(CustomUser.objects.select_related('doctor_profile'), pk=doctor_id, role='DOCTOR')
    date_str = request.GET.get('date', '')
    try:
        target_date = datetime.date.fromisoformat(date_str)
    except ValueError:
        return JsonResponse({'slots': [], 'error': 'Select a valid calendar date.'}, status=400)

    # ?follow_up_of=<id> relaxes the 72-hour rule to 24 hours for reviews
    follow_up_of = request.GET.get('follow_up_of', '').strip()
    lead_days = 1 if follow_up_of.isdigit() else 3
    min_date = timezone.localdate() + datetime.timedelta(days=lead_days)
    if target_date < min_date:
        notice = '24 hours' if lead_days == 1 else '72 hours'
        return JsonResponse({
            'slots': [],
            'error': f'Bookings require {notice} notice. Earliest date: {min_date.strftime("%d %b %Y")}.',
        })

    slots = generate_available_slots(doctor, target_date, min_lead_days=lead_days)
    return JsonResponse({'date': date_str, 'slots': slots})


@patient_required
def book_appointment_view(request, doctor_id):
    doctor = get_object_or_404(
        CustomUser.objects.select_related('doctor_profile'),
        pk=doctor_id,
        role='DOCTOR',
        is_active=True,
    )
    min_date = timezone.localdate() + datetime.timedelta(days=3)

    # ---------------------------------------------------------------- follow-up
    # A patient who already consulted this doctor can return FREE of charge and
    # with their previous clinical history carried over. The source consultation
    # is validated here (same patient, same doctor, completed or paid) so the URL
    # parameter can never be used to attach someone else's record.
    follow_up_parent = None
    parent_id = (request.GET.get('follow_up_of') or request.POST.get('follow_up_of') or '').strip()
    if parent_id.isdigit() and request.user.is_authenticated:
        follow_up_parent = (
            Appointment.objects
            .filter(pk=int(parent_id), patient=request.user, doctor=doctor)
            .exclude(status__in=('CANCELLED', 'NO_SHOW'))
            .first()
        )
        if follow_up_parent is None:
            messages.warning(request, 'That previous consultation cannot be used for a follow-up with this specialist.')
    is_follow_up = follow_up_parent is not None

    # A free follow-up keeps the 72-hour rule only for the FIRST visit; reviews are
    # short-notice by nature, so the window shrinks to 24 hours (GET and POST alike).
    if is_follow_up:
        min_date = timezone.localdate() + datetime.timedelta(days=1)

    if request.method == 'POST':
        idem_key = request.POST.get('idempotency_key', '').strip()
        if not claim_idempotency_token(idem_key, scope=f"book:{request.user.id}"):
            existing = Appointment.objects.filter(patient=request.user, idempotency_key=idem_key).first()
            if existing:
                return redirect('payments:submit', appointment_id=existing.pk)
            messages.warning(request, 'Duplicate booking request prevented.')
            return redirect('appointments:patient_list')

        date_str = request.POST.get('appointment_date', '').strip()
        time_str = request.POST.get('appointment_time', '').strip()
        reason = request.POST.get('reason', '').strip()
        consent = request.POST.get('telemedicine_consent')

        if not consent:
            messages.error(request, 'Please confirm the telemedicine & 24-hour cancellation notice before booking.')
            return redirect('appointments:book', doctor_id=doctor.id)

        try:
            appt_date = datetime.date.fromisoformat(date_str)
            appt_time = datetime.time.fromisoformat(time_str)
        except ValueError:
            messages.error(request, 'Choose a valid consultation date and 30-minute time slot.')
            return redirect('appointments:book', doctor_id=doctor.id)

        if appt_date < min_date:
            lead = '24 hours' if is_follow_up else '3 days'
            messages.error(request, f'Consultations must be scheduled at least {lead} in advance (from {min_date.strftime("%d %b %Y")}).')
            return redirect(f"{request.path}?follow_up_of={follow_up_parent.pk}" if is_follow_up else request.path)

        if not reason:
            messages.error(request, 'Describe your symptoms or reason for consultation.')
            return redirect('appointments:book', doctor_id=doctor.id)

        lead_days = 1 if is_follow_up else 3
        valid_slots = {s['time_value']: s for s in generate_available_slots(doctor, appt_date, min_lead_days=lead_days)}
        slot_meta = valid_slots.get(appt_time.strftime('%H:%M'))
        if not slot_meta or slot_meta['is_booked']:
            messages.error(request, 'That time slot is unavailable or was just reserved. Please pick another slot.')
            return redirect('appointments:book', doctor_id=doctor.id)

        with transaction.atomic():
            conflict = (
                Appointment.objects.select_for_update()
                .filter(doctor=doctor, appointment_date=appt_date, appointment_time=appt_time)
                .exclude(status='CANCELLED')
                .exists()
            )
            if conflict:
                messages.error(request, 'That slot was just reserved by another patient. Please pick another slot.')
                return redirect('appointments:book', doctor_id=doctor.id)

            end_dt = datetime.datetime.combine(appt_date, appt_time) + datetime.timedelta(minutes=30)

            if is_follow_up:
                # Free review: instantly confirmed, no Finance step, no receipt needed.
                prev_record = getattr(follow_up_parent, 'medical_record', None)
                appt = Appointment.objects.create(
                    patient=request.user,
                    doctor=doctor,
                    appointment_date=appt_date,
                    appointment_time=appt_time,
                    end_time=end_dt.time(),
                    status='CONFIRMED',
                    payment_status='NOT_REQUIRED',
                    visit_type='FOLLOW_UP',
                    parent_appointment=follow_up_parent,
                    is_free_follow_up=True,
                    follow_up_reference=getattr(prev_record, 'follow_up_date', None),
                    reason=reason[:1200],
                    consultation_fee=0,
                    idempotency_key=idem_key,
                )
            else:
                appt = Appointment.objects.create(
                    patient=request.user,
                    doctor=doctor,
                    appointment_date=appt_date,
                    appointment_time=appt_time,
                    end_time=end_dt.time(),
                    status='PENDING',
                    payment_status='UNPAID',
                    reason=reason[:1200],
                    consultation_fee=doctor.doctor_profile.consultation_fee,
                    idempotency_key=idem_key,
                )

        if is_follow_up:
            create_notification(
                recipient=request.user,
                notification_type='APPOINTMENT_CONFIRMED',
                title='Free Follow-Up Confirmed',
                message=(
                    f"Your follow-up with {doctor.display_title} is confirmed for "
                    f"{appt_date.strftime('%d %b %Y')} at {appt_time.strftime('%I:%M %p')}. "
                    'No payment is required — your previous prescription and test results are already attached.'
                ),
                link=f"/appointments/{appt.pk}/",
            )
            messages.success(
                request,
                f'Free follow-up confirmed with {doctor.display_title} on '
                f'{appt_date.strftime("%d %b %Y")} at {appt_time.strftime("%I:%M %p")}. '
                'Your previous history is attached — no payment needed.',
            )
            return redirect('appointments:detail', pk=appt.pk)

        create_notification(
            recipient=request.user,
            notification_type='APPOINTMENT_BOOKED',
            title='Consultation Slot Reserved — Submit Payment',
            message=f"Reserved {doctor.display_title} on {appt_date.strftime('%d %b %Y')} at {appt_time.strftime('%I:%M %p')}. Submit payment reference to confirm.",
            link=f"/payments/submit/{appt.pk}/",
        )
        messages.success(request, 'Slot reserved. Submit your payment transaction reference to confirm the consultation.')
        return redirect('payments:submit', appointment_id=appt.pk)

    # Default initial date = earliest valid available weekday in next 14 days
    lead_days = 1 if is_follow_up else 3
    suggested_date = min_date
    for offset in range(14):
        cand = min_date + datetime.timedelta(days=offset)
        if generate_available_slots(doctor, cand, min_lead_days=lead_days):
            suggested_date = cand
            break

    initial_slots = generate_available_slots(doctor, suggested_date, min_lead_days=lead_days)

    previous_record = getattr(follow_up_parent, 'medical_record', None) if follow_up_parent else None
    return render(request, 'appointments/book.html', {
        'doctor': doctor,
        'min_date': min_date,
        'suggested_date': suggested_date,
        'initial_slots': initial_slots,
        'idempotency_key': generate_idempotency_token(),
        'is_follow_up': is_follow_up,
        'follow_up_parent': follow_up_parent,
        'previous_record': previous_record,
        'can_follow_up': request.user.is_authenticated and request.user.role == 'PATIENT',
    })


@patient_required
def patient_appointments_view(request):
    status_filter = request.GET.get('status', '')
    qs = (
        Appointment.objects.filter(patient=request.user)
        .select_related('doctor', 'doctor__doctor_profile')
        .order_by('-appointment_date', '-appointment_time')
    )
    if status_filter:
        qs = qs.filter(status=status_filter)

    paginator = Paginator(qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'appointments/patient_list.html', {
        'page_obj': page_obj,
        'status_filter': status_filter,
    })


@doctor_required
def doctor_appointments_view(request):
    """
    Payment-gated clinical queue: Doctors see ONLY verified PAID appointments
    so unverified bookings never clutter clinical schedules.
    """
    status_filter = request.GET.get('status', '')
    qs = (
        Appointment.objects.filter(doctor=request.user, payment_status='PAID')
        .select_related('patient', 'patient__patient_profile')
        .order_by('appointment_date', 'appointment_time')
    )
    if status_filter:
        qs = qs.filter(status=status_filter)

    paginator = Paginator(qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'appointments/doctor_list.html', {
        'page_obj': page_obj,
        'status_filter': status_filter,
    })


@login_required
def appointment_detail_view(request, pk):
    appt = get_object_or_404(
        Appointment.objects.select_related(
            'patient', 'patient__patient_profile', 'doctor', 'doctor__doctor_profile'
        ),
        pk=pk,
    )
    user = request.user
    if not (user.is_admin_user or appt.patient_id == user.id or appt.doctor_id == user.id):
        raise PermissionDenied('You do not have access to this consultation record.')

    meet_form = MeetLinkForm(initial={'meet_link': appt.meet_link}) if user.id == appt.doctor_id else None
    review_form = None
    existing_review = getattr(appt, 'review', None)
    if user.id == appt.patient_id and appt.status == 'COMPLETED' and not existing_review:
        review_form = DoctorReviewForm()

    payments = appt.payments.select_related('verified_by').order_by('-created_at')
    medical_record = getattr(appt, 'medical_record', None)

    # Continuity of care: a patient may return to the same specialist free of charge.
    follow_up_eligible = (
        user.id == appt.patient_id
        and appt.visit_type == 'NEW'
        and appt.status not in ('CANCELLED', 'NO_SHOW')
    )
    active_follow_up = (
        appt.follow_ups.exclude(status__in=('CANCELLED', 'NO_SHOW')).order_by('-created_at').first()
        if appt.visit_type == 'NEW' else None
    )
    previous_visits = []
    if appt.visit_type == 'FOLLOW_UP' and appt.parent_appointment_id:
        previous_visits = (
            Appointment.objects
            .filter(pk=appt.parent_appointment_id)
            .select_related('doctor', 'doctor__doctor_profile')
        )

    return render(request, 'appointments/detail.html', {
        'appt': appt,
        'meet_form': meet_form,
        'review_form': review_form,
        'existing_review': existing_review,
        'payments': payments,
        'medical_record': medical_record,
        'follow_up_eligible': follow_up_eligible,
        'active_follow_up': active_follow_up,
        'previous_visits': previous_visits,
        'previous_record': getattr(active_follow_up or (previous_visits[0] if previous_visits else None), 'medical_record', None),
    })


@doctor_required
@require_POST
def set_meet_link_view(request, pk):
    appt = get_object_or_404(Appointment, pk=pk, doctor=request.user)
    if appt.payment_status != 'PAID':
        messages.error(request, 'Payment must be verified before assigning a video consultation room.')
        return redirect('appointments:detail', pk=appt.pk)

    form = MeetLinkForm(request.POST)
    if form.is_valid():
        appt.meet_link = form.cleaned_data['meet_link']
        appt.meet_link_added_at = timezone.now()
        if appt.status == 'PENDING':
            appt.status = 'CONFIRMED'
        appt.save(update_fields=['meet_link', 'meet_link_added_at', 'status'])

        create_notification(
            recipient=appt.patient,
            notification_type='MEET_LINK_SET',
            title='Video Consultation Link Ready',
            message=f"{request.user.display_title} added the Google Meet link for your {appt.appointment_date.strftime('%d %b %Y')} consultation.",
            link=f"/appointments/{appt.pk}/",
            extra_context={
                'consultation_type': f"{request.user.doctor_profile.get_specialty_display()} Video Consultation",
                'consultation_date': appt.appointment_date.strftime('%d %b %Y'),
                'consultation_time': f"{appt.appointment_time.strftime('%I:%M %p')} Dhaka time",
                'doctor_name': request.user.display_title,
                'meet_link': appt.meet_link,
            },
        )
        messages.success(request, 'Google Meet link saved and dispatched to the patient.')
    else:
        messages.error(request, 'Enter a valid HTTPS video consultation URL.')

    return redirect('appointments:detail', pk=appt.pk)


@doctor_required
@require_POST
def update_appointment_status_view(request, pk):
    appt = get_object_or_404(Appointment, pk=pk, doctor=request.user, payment_status='PAID')
    new_status = request.POST.get('status', '')
    allowed = {'CONFIRMED', 'IN_PROGRESS', 'COMPLETED', 'NO_SHOW'}
    if new_status in allowed:
        appt.status = new_status
        appt.save(update_fields=['status', 'updated_at'])
        messages.success(request, f'Consultation marked as {appt.get_status_display()}.')
    return redirect('appointments:detail', pk=appt.pk)


@login_required
@require_POST
def cancel_appointment_view(request, pk):
    appt = get_object_or_404(Appointment, pk=pk)
    user = request.user
    if not (user.is_admin_user or appt.patient_id == user.id):
        raise PermissionDenied('Not authorized to cancel this appointment.')

    if not user.is_admin_user and not appt.can_cancel:
        messages.error(request, 'Cancellations are closed within 24 hours of the scheduled consultation.')
        return redirect('appointments:detail', pk=appt.pk)

    appt.status = 'CANCELLED'
    appt.save(update_fields=['status', 'updated_at'])

    create_notification(
        recipient=appt.patient,
        notification_type='APPOINTMENT_CANCELLED',
        title='Appointment Cancelled',
        message=f"Consultation #{appt.pk} with {appt.doctor.display_title} on {appt.appointment_date.strftime('%d %b %Y')} has been cancelled.",
        link=f"/appointments/{appt.pk}/",
    )
    messages.info(request, 'Appointment cancelled. Paid consultations cancelled 24+ hours ahead qualify for refund review.')
    return redirect('appointments:detail', pk=appt.pk)


@patient_required
@require_POST
def submit_review_view(request, pk):
    appt = get_object_or_404(Appointment, pk=pk, patient=request.user, status='COMPLETED')
    if hasattr(appt, 'review'):
        messages.info(request, 'You have already submitted feedback for this consultation.')
        return redirect('appointments:detail', pk=appt.pk)

    form = DoctorReviewForm(request.POST)
    if form.is_valid():
        review = form.save(commit=False)
        review.appointment = appt
        review.patient = request.user
        review.doctor = appt.doctor
        review.save()
        messages.success(request, 'Thank you. Your verified post-visit feedback has been published.')
    else:
        messages.error(request, 'Please select a valid rating between 1 and 5.')
    return redirect('appointments:detail', pk=appt.pk)


@doctor_required
def manage_availability_view(request):
    if request.method == 'POST':
        form = AvailabilityForm(request.POST)
        if form.is_valid():
            slot = form.save(commit=False)
            slot.doctor = request.user
            try:
                slot.full_clean()
                slot.save()
                messages.success(request, 'Weekly schedule window added.')
                return redirect('appointments:availability')
            except Exception as exc:
                messages.error(request, f'Could not save schedule window: {exc}')
    else:
        form = AvailabilityForm()

    windows = Availability.objects.filter(doctor=request.user).order_by('day_of_week', 'start_time')
    return render(request, 'appointments/availability.html', {
        'form': form,
        'windows': windows,
    })


@doctor_required
@require_POST
def delete_availability_view(request, pk):
    win = get_object_or_404(Availability, pk=pk, doctor=request.user)
    win.delete()
    messages.info(request, 'Schedule window removed.')
    return redirect('appointments:availability')
