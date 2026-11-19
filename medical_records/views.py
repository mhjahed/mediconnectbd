from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404

from accounts.decorators import doctor_required
from appointments.models import Appointment
from notifications.models import create_notification
from .forms import MedicalRecordForm, PrescriptionFormSet, LabTestFormSet
from .models import MedicalRecord


@login_required
def record_list_view(request):
    user = request.user
    base_qs = MedicalRecord.objects.select_related(
        'patient', 'doctor', 'doctor__doctor_profile', 'appointment'
    ).prefetch_related('prescriptions', 'lab_tests')

    if user.is_admin_user:
        qs = base_qs.all()
    elif user.is_doctor:
        qs = base_qs.filter(doctor=user)
    elif user.is_patient:
        qs = base_qs.filter(patient=user)
    else:
        raise PermissionDenied('Clinical records are restricted to patients, treating physicians, and clinical administration.')

    paginator = Paginator(qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'medical_records/list.html', {'page_obj': page_obj})


@doctor_required
def create_or_edit_record_view(request, appointment_id):
    appt = get_object_or_404(
        Appointment.objects.select_related('patient', 'patient__patient_profile', 'doctor'),
        pk=appointment_id,
        doctor=request.user,
    )
    if appt.payment_status != 'PAID':
        messages.error(request, 'Cannot author a prescription for an unverified appointment.')
        return redirect('appointments:detail', pk=appt.pk)

    instance = getattr(appt, 'medical_record', None)

    if request.method == 'POST':
        form = MedicalRecordForm(request.POST, instance=instance)
        rx_formset = PrescriptionFormSet(request.POST, instance=instance, prefix='rx')
        lab_formset = LabTestFormSet(request.POST, instance=instance, prefix='lab')

        if form.is_valid() and rx_formset.is_valid() and lab_formset.is_valid():
            with transaction.atomic():
                record = form.save(commit=False)
                record.appointment = appt
                record.patient = appt.patient
                record.doctor = request.user
                record.save()

                rx_formset.instance = record
                rx_formset.save()

                lab_formset.instance = record
                lab_formset.save()

                if appt.status != 'COMPLETED':
                    appt.status = 'COMPLETED'
                    appt.save(update_fields=['status', 'updated_at'])

            create_notification(
                recipient=appt.patient,
                notification_type='PRESCRIPTION_READY',
                title='Signed Prescription & Clinical Record Ready',
                message=f"{request.user.display_title} finalized your SOAP clinical summary and prescription.",
                link=f"/records/{record.pk}/print/",
                extra_context={
                    'doctor_name': request.user.display_title,
                    'diagnosis_summary': record.diagnosis,
                    'follow_up_note': record.follow_up_date.strftime('%d %b %Y') if record.follow_up_date else 'As advised on A4 Rx',
                },
            )
            messages.success(request, 'Clinical record, medications, and lab orders saved.')
            return redirect('medical_records:detail', pk=record.pk)
    else:
        initial = {'chief_complaint': appt.reason} if not instance else None
        form = MedicalRecordForm(instance=instance, initial=initial)
        rx_formset = PrescriptionFormSet(instance=instance, prefix='rx')
        lab_formset = LabTestFormSet(instance=instance, prefix='lab')

    return render(request, 'medical_records/form.html', {
        'appt': appt,
        'form': form,
        'rx_formset': rx_formset,
        'lab_formset': lab_formset,
        'is_edit': instance is not None,
    })


@login_required
def record_detail_view(request, pk):
    record = get_object_or_404(
        MedicalRecord.objects.select_related(
            'patient', 'patient__patient_profile', 'doctor', 'doctor__doctor_profile', 'appointment'
        ).prefetch_related('prescriptions', 'lab_tests'),
        pk=pk,
    )
    user = request.user
    if not (user.is_admin_user or record.patient_id == user.id or record.doctor_id == user.id):
        raise PermissionDenied('Row-level privacy guard: you are not authorized to view this medical record.')

    return render(request, 'medical_records/detail.html', {'record': record})


@login_required
def print_prescription_view(request, pk):
    record = get_object_or_404(
        MedicalRecord.objects.select_related(
            'patient', 'patient__patient_profile', 'doctor', 'doctor__doctor_profile', 'appointment'
        ).prefetch_related('prescriptions', 'lab_tests'),
        pk=pk,
    )
    user = request.user
    if not (user.is_admin_user or record.patient_id == user.id or record.doctor_id == user.id):
        raise PermissionDenied('Not authorized to print this prescription.')

    return render(request, 'medical_records/print_rx.html', {'record': record})
