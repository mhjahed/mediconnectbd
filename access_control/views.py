import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required, role_required
from core.middleware import get_client_ip
from appointments.models import Appointment
from payments.models import Payment
from .forms import StaffIDCardIssueForm, StaffIDCardEditZonesForm
from .models import StaffIDCard, AccessLog, HOSPITAL_ZONES, ZONE_LABEL_MAP


# Ward-door terminals are clinical infrastructure: staff + clinicians + admins only.
SCANNER_ROLES = ('ADMIN', 'DOCTOR', 'NURSE', 'STAFF')


@role_required(*SCANNER_ROLES)
def scanner_terminal_view(request):
    """
    Hospital Ward Door Terminal — camera Code-128/QR scanning (ZXing + html5-qrcode)
    plus manual keyboard/USB wedge entry and sequenced verdict audio cues.

    ACCESS CONTROL: login required AND role in SCANNER_ROLES (admins, doctors,
    nurses, general staff). Patients are rejected with 403 before any query runs,
    so an anonymous or patient account can never enumerate badges or hammer
    /access/scan/ to stress the database.
    """
    recent_logs = AccessLog.objects.select_related('card', 'card__user', 'scanned_by').order_by('-scanned_at')[:10]
    sample_cards = []
    sample_receipts = []
    is_staff_portal = True  # guaranteed by the role guard above
    if request.user.is_admin_user:
        sample_cards = StaffIDCard.objects.select_related('user').filter(status='ACTIVE')[:6]
    elif hasattr(request.user, 'id_card'):
        sample_cards = [request.user.id_card]
    if request.user.is_admin_user or request.user.role in ('NURSE', 'STAFF', 'DOCTOR'):
        # Only the reception desk needs live payment hints on the terminal
        sample_receipts = (
            Payment.objects.select_related('patient', 'appointment', 'appointment__doctor')
            .filter(status='VERIFIED')
            .order_by('-created_at')[:5]
        )

    return render(request, 'access_control/scanner.html', {
        'zones': HOSPITAL_ZONES,
        'recent_logs': recent_logs,
        'sample_cards': sample_cards,
        'sample_receipts': sample_receipts,
        'is_staff_portal': is_staff_portal,
    })


@role_required(*SCANNER_ROLES)
@require_POST
def verify_scan_api(request):
    """Badge verification endpoint — staff roles only (blocks anonymous bruteforce)."""
    try:
        if request.content_type and 'application/json' in request.content_type:
            payload = json.loads(request.body.decode('utf-8') or '{}')
        else:
            payload = request.POST
    except json.JSONDecodeError:
        return JsonResponse({'status': 'ERROR', 'message': 'Malformed scan payload.'}, status=400)

    raw_code = (payload.get('barcode') or '').strip().upper()
    zone_code = (payload.get('zone') or 'MAIN_ENTRANCE').strip().upper()

    if zone_code not in ZONE_LABEL_MAP:
        return JsonResponse({'status': 'ERROR', 'message': 'Unknown hospital zone selected.'}, status=400)

    if not raw_code or len(raw_code) > 96:
        return JsonResponse({'status': 'ERROR', 'message': 'Provide a valid badge code or 8-digit staff ID.'}, status=400)

    # Helpful redirect hint if someone scans a Patient Receipt QR on the Staff Ward Door tab
    if raw_code.startswith('MC-PASS|') or raw_code.startswith('RCPT-') or raw_code.startswith('PASS-'):
        return JsonResponse({
            'result': 'DENIED',
            'reason_code': 'PATIENT_RECEIPT_ON_STAFF_READER',
            'message': 'Patient Appointment Receipt detected. Switch to the "Patient Entry QR" tab (Staff/Admin sign-in) to verify patient check-in.',
            'zone_label': ZONE_LABEL_MAP[zone_code],
            'timestamp': timezone.localtime().strftime('%H:%M:%S'),
        })

    card = (
        StaffIDCard.objects.select_related('user')
        .filter(Q(barcode_value=raw_code) | Q(staff_id_number=raw_code))
        .first()
    )

    operator = request.user if request.user.is_authenticated else None
    ip_addr = get_client_ip(request)

    if not card:
        AccessLog.objects.create(
            card=None,
            scanned_code=raw_code[:64],
            zone_code=zone_code,
            result='DENIED',
            reason_code='UNKNOWN_BADGE',
            message='Unrecognized barcode or staff ID number.',
            scanned_by=operator,
            ip_address=ip_addr,
        )
        return JsonResponse({
            'result': 'DENIED',
            'reason_code': 'UNKNOWN_BADGE',
            'message': 'Unrecognized badge code. Access denied.',
            'zone_label': ZONE_LABEL_MAP[zone_code],
            'timestamp': timezone.localtime().strftime('%H:%M:%S'),
        })

    granted, reason_code, human_msg = card.evaluate_room_access(zone_code)
    result_str = 'GRANTED' if granted else 'DENIED'

    AccessLog.objects.create(
        card=card,
        scanned_code=card.barcode_value,
        zone_code=zone_code,
        result=result_str,
        reason_code=reason_code,
        message=human_msg,
        scanned_by=operator,
        ip_address=ip_addr,
    )

    return JsonResponse({
        'result': result_str,
        'reason_code': reason_code,
        'message': human_msg,
        'zone_label': ZONE_LABEL_MAP[zone_code],
        'timestamp': timezone.localtime().strftime('%H:%M:%S'),
        'holder': {
            'name': card.user.display_title,
            'role': card.user.get_role_display(),
            'department': card.department_label,
            'staff_id': card.staff_id_number,
            'barcode': card.barcode_value,
            'card_status': card.get_status_display(),
            'expiry': card.expiry_date.strftime('%d %b %Y'),
        },
    })


@role_required('ADMIN', 'DOCTOR', 'NURSE', 'STAFF')
def patient_entry_desk_view(request):
    """
    Dedicated Staff/Admin Reception QR Verification Desk.
    Staff scan a patient's printed/mobile Receipt QR (MC-PASS|RCPT-...|PASS-...) to verify
    verified payment, appointment schedule, and record patient outpatient arrival.
    """
    checked_in_today = (
        Payment.objects.filter(patient_checked_in_at__date=timezone.localdate())
        .select_related('patient', 'appointment', 'appointment__doctor', 'checked_in_by')
        .order_by('-patient_checked_in_at')[:12]
    )
    sample_receipts = (
        Payment.objects.select_related('patient', 'appointment', 'appointment__doctor')
        .order_by('-created_at')[:6]
    )
    return render(request, 'access_control/patient_desk.html', {
        'checked_in_today': checked_in_today,
        'sample_receipts': sample_receipts,
    })


@role_required('ADMIN', 'DOCTOR', 'NURSE', 'STAFF')
@require_POST
def verify_patient_qr_api(request):
    """
    Evaluates a scanned Patient Receipt QR code (`MC-PASS|RCPT-...|PASS-...`),
    receipt number (`RCPT-...`), qr_token (`PASS-...`), or transaction_id.
    Grants outpatient clinic entry ONLY when:
      1. Payment status == 'VERIFIED'
      2. Appointment payment_status == 'PAID' and status not in ('CANCELLED', 'NO_SHOW')
    """
    try:
        if request.content_type and 'application/json' in request.content_type:
            payload = json.loads(request.body.decode('utf-8') or '{}')
        else:
            payload = request.POST
    except json.JSONDecodeError:
        return JsonResponse({'result': 'DENIED', 'message': 'Malformed QR payload.'}, status=400)

    raw_input = (payload.get('qr_code') or '').strip().upper()
    if not raw_input or len(raw_input) > 120:
        return JsonResponse({'result': 'DENIED', 'reason_code': 'EMPTY_CODE', 'message': 'Scan a valid Patient Receipt QR code.'}, status=400)

    payment = None
    if raw_input.startswith('MC-PASS|'):
        parts = raw_input.split('|')
        if len(parts) >= 3:
            rcpt_num, token = parts[1].strip(), parts[2].strip()
            payment = (
                Payment.objects.select_related(
                    'patient', 'appointment', 'appointment__doctor', 'appointment__doctor__doctor_profile'
                )
                .filter(receipt_number=rcpt_num, qr_token=token)
                .first()
            )
    else:
        payment = (
            Payment.objects.select_related(
                'patient', 'appointment', 'appointment__doctor', 'appointment__doctor__doctor_profile'
            )
            .filter(
                Q(receipt_number=raw_input)
                | Q(qr_token=raw_input)
                | Q(transaction_id=raw_input)
            )
            .first()
        )

    now_stamp = timezone.localtime().strftime('%H:%M:%S')

    if not payment:
        return JsonResponse({
            'result': 'DENIED',
            'reason_code': 'RECEIPT_NOT_FOUND',
            'message': 'No matching patient receipt or QR pass found in hospital ledger.',
            'timestamp': now_stamp,
        })

    appt = payment.appointment
    patient_name = payment.patient.get_full_name() or payment.patient.username
    doc_name = appt.doctor.display_title
    specialty = appt.doctor.doctor_profile.get_specialty_display() if hasattr(appt.doctor, 'doctor_profile') else 'Outpatient'

    # Continuity of care: is this patient already booked for a free follow-up?
    next_follow_up = (
        Appointment.objects.filter(
            patient=payment.patient, doctor=appt.doctor, visit_type='FOLLOW_UP',
        ).exclude(status__in=('CANCELLED', 'NO_SHOW')).order_by('appointment_date').first()
    )

    appointment_info = {
        'receipt_number': payment.receipt_number,
        'qr_token': payment.qr_token,
        'patient_name': patient_name,
        'patient_phone': payment.patient.phone or '—',
        'doctor_name': doc_name,
        'specialty': specialty,
        'appointment_id': appt.pk,
        'appointment_date': appt.appointment_date.strftime('%d %b %Y'),
        'appointment_time': appt.appointment_time.strftime('%I:%M %p'),
        'appointment_status': appt.get_status_display(),
        'payment_status': payment.get_status_display(),
        'amount': f"BDT {payment.amount:.0f}",
        'method': payment.get_payment_method_display(),
        'detail_url': f"/appointments/{appt.pk}/",
        'visit_type': appt.get_visit_type_display(),
        'follow_up_booked': (
            f"{next_follow_up.appointment_date.strftime('%d %b %Y')} "
            f"{next_follow_up.appointment_time.strftime('%I:%M %p')}"
            if next_follow_up else ''
        ),
        'follow_up_note': (
            'Free follow-up already booked — no further payment required.'
            if next_follow_up else 'Eligible for a free follow-up with this specialist.'
        ),
    }

    if payment.status != 'VERIFIED' or appt.payment_status != 'PAID':
        return JsonResponse({
            'result': 'DENIED',
            'reason_code': f'PAYMENT_{payment.status}',
            'message': f'Entry Denied — Receipt {payment.receipt_number} is {payment.get_status_display()} (Finance verification required before clinic entry).',
            'timestamp': now_stamp,
            'booking': appointment_info,
        })

    if appt.status in ('CANCELLED', 'NO_SHOW'):
        return JsonResponse({
            'result': 'DENIED',
            'reason_code': f'APPT_{appt.status}',
            'message': f'Entry Denied — Appointment #{appt.pk} is marked {appt.get_status_display()}.',
            'timestamp': now_stamp,
            'booking': appointment_info,
        })

    # Valid & Paid -> Record arrival check-in if not already checked in
    already_checked = payment.patient_checked_in_at is not None
    if not already_checked:
        payment.patient_checked_in_at = timezone.now()
        payment.checked_in_by = request.user
        payment.save(update_fields=['patient_checked_in_at', 'checked_in_by'])

    checkin_label = timezone.localtime(payment.patient_checked_in_at).strftime('%d %b %H:%M')
    appointment_info['checked_in_at'] = checkin_label

    return JsonResponse({
        'result': 'GRANTED',
        'reason_code': 'VERIFIED_PATIENT_PASS',
        'message': (
            (
                f'Patient Verified & Checked In ({checkin_label}) — Direct to {doc_name} ({specialty}).'
                if not already_checked
                else f'Already Checked In at {checkin_label} — Confirmed for {doc_name} ({specialty}).'
            )
            + (
                f' Free follow-up on {next_follow_up.appointment_date.strftime("%d %b %Y")} — no payment due.'
                if next_follow_up else ''
            )
        ),
        'timestamp': now_stamp,
        'booking': appointment_info,
    })


@login_required
def my_id_card_view(request):
    card = getattr(request.user, 'id_card', None)
    if not card and not request.user.is_hospital_staff and not request.user.is_admin_user:
        raise PermissionDenied('Physical hospital ID badges are issued only to clinical and operations staff.')

    recent_scans = card.scan_logs.all()[:12] if card else []
    return render(request, 'access_control/my_card.html', {
        'card': card,
        'recent_scans': recent_scans,
    })


@login_required
def print_badge_view(request, pk):
    card = get_object_or_404(StaffIDCard.objects.select_related('user'), pk=pk)
    if not (request.user.is_admin_user or card.user_id == request.user.id):
        raise PermissionDenied('Not authorized to print another staff member badge.')
    return render(request, 'access_control/print_badge.html', {'card': card})


@admin_required
def manage_cards_view(request):
    if request.method == 'POST':
        form = StaffIDCardIssueForm(request.POST)
        if form.is_valid():
            card = form.save(commit=False)
            card.issued_by = request.user
            card.allowed_zones = form.cleaned_data['allowed_zones']
            card.save()
            messages.success(request, f'Issued Code-128 badge {card.barcode_value} for {card.user.display_title}.')
            return redirect('access_control:manage_cards')
    else:
        initial = {'expiry_date': timezone.localdate() + timezone.timedelta(days=730)}
        form = StaffIDCardIssueForm(initial=initial)

    cards = StaffIDCard.objects.select_related('user', 'issued_by').all()
    paginator = Paginator(cards, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'access_control/manage_cards.html', {
        'form': form,
        'page_obj': page_obj,
    })


@admin_required
def edit_card_view(request, pk):
    card = get_object_or_404(StaffIDCard.objects.select_related('user'), pk=pk)
    if request.method == 'POST':
        form = StaffIDCardEditZonesForm(request.POST, instance=card)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.allowed_zones = form.cleaned_data['allowed_zones']
            updated.save()
            messages.success(request, f'Updated room permissions and status for badge {card.barcode_value}.')
            return redirect('access_control:manage_cards')
    else:
        form = StaffIDCardEditZonesForm(instance=card, initial={'allowed_zones': card.allowed_zones})

    return render(request, 'access_control/edit_card.html', {'card': card, 'form': form})


@admin_required
def audit_logs_view(request):
    result_filter = request.GET.get('result', '')
    zone_filter = request.GET.get('zone', '')
    qs = AccessLog.objects.select_related('card', 'card__user', 'scanned_by').all()
    if result_filter:
        qs = qs.filter(result=result_filter)
    if zone_filter:
        qs = qs.filter(zone_code=zone_filter)

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'access_control/audit_logs.html', {
        'page_obj': page_obj,
        'zones': HOSPITAL_ZONES,
        'result_filter': result_filter,
        'zone_filter': zone_filter,
    })
