from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required, patient_required
from accounts.models import CustomUser
from appointments.models import Appointment
from core.utils import generate_idempotency_token, claim_idempotency_token
from notifications.models import create_notification
from .forms import PaymentSubmissionForm
from .models import Payment


MERCHANT_ACCOUNTS = [
    {'code': 'BKASH', 'name': 'bKash Merchant', 'number': '01711-880022', 'instruction': 'Use Make Payment in bKash App'},
    {'code': 'NAGAD', 'name': 'Nagad Merchant', 'number': '01819-440055', 'instruction': 'Use Merchant Pay in Nagad App'},
    {'code': 'ROCKET', 'name': 'Rocket (DBBL)', 'number': '01912-330099-8', 'instruction': 'Use Bill Pay / Merchant Pay'},
    {'code': 'UPAY', 'name': 'Upay Merchant', 'number': '01615-770011', 'instruction': 'Use Merchant QR or Number'},
    {'code': 'BANK_TRANSFER', 'name': 'City Bank PLC · Gulshan Branch', 'number': 'A/C 1402998831001', 'instruction': 'NPSB / BEFTN Instant Transfer'},
]


@patient_required
def submit_payment_view(request, appointment_id):
    appt = get_object_or_404(
        Appointment.objects.select_related('doctor', 'doctor__doctor_profile'),
        pk=appointment_id,
        patient=request.user,
    )

    if appt.payment_exempt:
        messages.info(request, 'This is a free follow-up — no payment is required.')
        return redirect('appointments:detail', pk=appt.pk)

    if appt.payment_status == 'PAID':
        messages.info(request, 'Payment for this consultation is already verified.')
        return redirect('appointments:detail', pk=appt.pk)

    if appt.payments.filter(status='PENDING').exists():
        messages.info(request, 'Your payment submission is awaiting Finance verification.')
        return redirect('appointments:detail', pk=appt.pk)

    if request.method == 'POST':
        idem_key = request.POST.get('idempotency_key', '').strip()
        if not claim_idempotency_token(idem_key, scope=f"pay:{request.user.id}:{appt.pk}"):
            messages.warning(request, 'Duplicate payment submission blocked.')
            return redirect('appointments:detail', pk=appt.pk)

        form = PaymentSubmissionForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                payment = form.save(commit=False)
                payment.appointment = appt
                payment.patient = request.user
                payment.amount = appt.consultation_fee
                payment.status = 'PENDING'
                payment.idempotency_key = idem_key
                payment.save()

                appt.payment_status = 'PENDING_VERIFICATION'
                appt.save(update_fields=['payment_status', 'updated_at'])

            # Notify Hospital Admins
            for admin_user in CustomUser.objects.filter(role='ADMIN', is_active=True):
                create_notification(
                    recipient=admin_user,
                    notification_type='PAYMENT_RECEIVED',
                    title=f'Payment Verification Needed · BDT {payment.amount}',
                    message=f"{request.user.get_full_name()} submitted {payment.get_payment_method_display()} TrxID {payment.transaction_id} for Consultation #{appt.pk}.",
                    link='/payments/queue/',
                    send_email=False,
                )

            messages.success(request, f'Payment reference {payment.transaction_id} recorded. Finance will verify shortly.')
            return redirect('appointments:detail', pk=appt.pk)
    else:
        form = PaymentSubmissionForm()

    return render(request, 'payments/submit.html', {
        'appt': appt,
        'form': form,
        'merchant_accounts': MERCHANT_ACCOUNTS,
        'idempotency_key': generate_idempotency_token(),
    })


@patient_required
def patient_payments_view(request):
    qs = Payment.objects.filter(patient=request.user).select_related('appointment', 'appointment__doctor')
    paginator = Paginator(qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'payments/patient_list.html', {'page_obj': page_obj})


@admin_required
def admin_payment_queue_view(request):
    status_filter = request.GET.get('status', 'PENDING')
    qs = Payment.objects.select_related(
        'patient', 'appointment', 'appointment__doctor', 'verified_by'
    ).all()
    if status_filter != 'ALL':
        qs = qs.filter(status=status_filter)

    totals = {
        'verified_sum': Payment.objects.filter(status='VERIFIED').aggregate(s=Sum('amount'))['s'] or 0,
        'pending_count': Payment.objects.filter(status='PENDING').count(),
    }

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'payments/admin_queue.html', {
        'page_obj': page_obj,
        'status_filter': status_filter,
        'totals': totals,
    })


@admin_required
@require_POST
def verify_payment_action_view(request, pk):
    payment = get_object_or_404(
        Payment.objects.select_related('appointment', 'appointment__doctor', 'patient'),
        pk=pk,
    )
    decision = request.POST.get('decision', '')
    reason = request.POST.get('rejection_reason', '').strip()

    with transaction.atomic():
        appt = payment.appointment
        if decision == 'VERIFY':
            payment.status = 'VERIFIED'
            payment.verified_by = request.user
            payment.verified_at = timezone.now()
            payment.rejection_reason = ''
            payment.save()

            appt.payment_status = 'PAID'
            if appt.status == 'PENDING':
                appt.status = 'CONFIRMED'
            appt.save(update_fields=['payment_status', 'status', 'updated_at'])

            create_notification(
                recipient=payment.patient,
                notification_type='PAYMENT_VERIFIED',
                title='Payment Verified & Consultation Confirmed',
                message=f"Receipt {payment.receipt_number} (BDT {payment.amount}) verified. Consultation with {appt.doctor.display_title} on {appt.appointment_date.strftime('%d %b %Y')} is confirmed.",
                link=f"/payments/{payment.pk}/receipt/",
                extra_context={
                    'doctor_name': appt.doctor.display_title,
                    'consultation_date': appt.appointment_date.strftime('%d %b %Y'),
                    'consultation_time': appt.appointment_time.strftime('%I:%M %p'),
                    'receipt_number': payment.receipt_number,
                    'amount_paid': f"BDT {payment.amount:,.0f}",
                    'pass_token': payment.qr_token,
                },
            )
            create_notification(
                recipient=appt.doctor,
                notification_type='APPOINTMENT_CONFIRMED',
                title='New Paid Consultation on Your Docket',
                message=f"{payment.patient.get_full_name()} is confirmed for {appt.appointment_date.strftime('%d %b %Y')} at {appt.appointment_time.strftime('%I:%M %p')}. Please assign a Google Meet link.",
                link=f"/appointments/{appt.pk}/",
                extra_context={
                    'consultation_type': f"{appt.doctor.doctor_profile.get_specialty_display()} Consultation (Patient: {payment.patient.get_full_name()})",
                    'consultation_date': appt.appointment_date.strftime('%d %b %Y'),
                    'consultation_time': f"{appt.appointment_time.strftime('%I:%M %p')} Dhaka time",
                    'doctor_name': appt.doctor.display_title,
                    'meet_link': appt.meet_link,
                },
            )
            messages.success(request, f'Verified payment {payment.transaction_id} and unlocked consultation #{appt.pk}.')

        elif decision == 'REJECT':
            payment.status = 'REJECTED'
            payment.verified_by = request.user
            payment.verified_at = timezone.now()
            payment.rejection_reason = reason or 'Transaction reference did not match merchant settlement log.'
            payment.save()

            appt.payment_status = 'UNPAID'
            appt.save(update_fields=['payment_status', 'updated_at'])

            create_notification(
                recipient=payment.patient,
                notification_type='PAYMENT_REJECTED',
                title='Payment Reference Requires Resubmission',
                message=f"TrxID {payment.transaction_id} could not be verified: {payment.rejection_reason}",
                link=f"/payments/submit/{appt.pk}/",
            )
            messages.warning(request, f'Rejected payment {payment.transaction_id}.')

        elif decision == 'REFUND':
            payment.status = 'REFUNDED'
            payment.verified_by = request.user
            payment.verified_at = timezone.now()
            payment.save()

            appt.payment_status = 'REFUNDED'
            appt.status = 'CANCELLED'
            appt.save(update_fields=['payment_status', 'status', 'updated_at'])
            messages.info(request, f'Marked receipt {payment.receipt_number} as refunded.')

    return redirect('payments:admin_queue')


@login_required
def payment_receipt_view(request, pk):
    payment = get_object_or_404(
        Payment.objects.select_related(
            'patient', 'appointment', 'appointment__doctor', 'appointment__doctor__doctor_profile', 'verified_by'
        ),
        pk=pk,
    )
    user = request.user
    if not (user.is_admin_user or payment.patient_id == user.id or payment.appointment.doctor_id == user.id):
        raise PermissionDenied('You do not have permission to view this payment receipt.')

    return render(request, 'payments/receipt.html', {'payment': payment})
