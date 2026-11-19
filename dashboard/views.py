from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from accounts.models import CustomUser, AccessKey, DailyAttendance
from appointments.models import Appointment
from payments.models import Payment
from medical_records.models import MedicalRecord
from access_control.models import StaffIDCard, AccessLog


@login_required
def dashboard_home_view(request):
    user = request.user
    today = timezone.localdate()

    if user.is_admin_user:
        metrics = {
            'doctors_count': CustomUser.objects.filter(role='DOCTOR', is_active=True).count(),
            'patients_count': CustomUser.objects.filter(role='PATIENT', is_active=True).count(),
            'staff_count': CustomUser.objects.filter(role__in=('NURSE', 'STAFF'), is_active=True).count(),
            'pending_payments': Payment.objects.filter(status='PENDING').count(),
            'verified_revenue': Payment.objects.filter(status='VERIFIED').aggregate(s=Sum('amount'))['s'] or 0,
            'unused_keys': AccessKey.objects.filter(status='UNUSED').count(),
            'active_badges': StaffIDCard.objects.filter(status='ACTIVE').count(),
            'today_attendance_count': DailyAttendance.objects.filter(date=today).count(),
        }
        pending_payments_list = (
            Payment.objects.filter(status='PENDING')
            .select_related('patient', 'appointment', 'appointment__doctor')
            .order_by('-created_at')[:6]
        )
        recent_scans = (
            AccessLog.objects.select_related('card', 'card__user')
            .order_by('-scanned_at')[:8]
        )
        return render(request, 'dashboard/admin_dashboard.html', {
            'metrics': metrics,
            'pending_payments_list': pending_payments_list,
            'recent_scans': recent_scans,
        })

    if user.is_doctor:
        paid_qs = Appointment.objects.filter(doctor=user, payment_status='PAID').select_related('patient')
        upcoming = paid_qs.filter(appointment_date__gte=today, status__in=('CONFIRMED', 'IN_PROGRESS')).order_by('appointment_date', 'appointment_time')[:8]
        completed_count = paid_qs.filter(status='COMPLETED').count()
        awaiting_meet = paid_qs.filter(meet_link='', status='CONFIRMED').count()
        recent_records = MedicalRecord.objects.filter(doctor=user).select_related('patient', 'appointment')[:5]

        return render(request, 'dashboard/doctor_dashboard.html', {
            'upcoming': upcoming,
            'completed_count': completed_count,
            'awaiting_meet': awaiting_meet,
            'total_paid': paid_qs.count(),
            'recent_records': recent_records,
            'id_card': getattr(user, 'id_card', None),
            'streak': DailyAttendance.calculate_streak(user),
        })

    if user.is_nurse or user.is_general_staff:
        card = getattr(user, 'id_card', None)
        recent_scans = card.scan_logs.all()[:10] if card else []
        return render(request, 'dashboard/staff_dashboard.html', {
            'card': card,
            'recent_scans': recent_scans,
            'streak': DailyAttendance.calculate_streak(user),
            'attendance_history': DailyAttendance.objects.filter(user=user).order_by('-date')[:10],
        })

    # Default: Patient Dashboard
    appts = (
        Appointment.objects.filter(patient=user)
        .select_related('doctor', 'doctor__doctor_profile')
        .order_by('-appointment_date', '-appointment_time')
    )
    unpaid_appts = appts.filter(payment_status='UNPAID').exclude(status='CANCELLED')[:4]
    upcoming_appts = appts.filter(appointment_date__gte=today).exclude(status='CANCELLED')[:6]
    records = (
        MedicalRecord.objects.filter(patient=user)
        .select_related('doctor', 'doctor__doctor_profile', 'appointment')
        .prefetch_related('prescriptions')[:5]
    )

    return render(request, 'dashboard/patient_dashboard.html', {
        'unpaid_appts': unpaid_appts,
        'upcoming_appts': upcoming_appts,
        'records': records,
        'total_appts': appts.count(),
    })
