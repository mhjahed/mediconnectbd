import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import CustomUser, PatientProfile, DoctorProfile, StaffProfile, AccessKey, DailyAttendance
from appointments.models import Availability, Appointment, DoctorReview
from payments.models import Payment
from medical_records.models import MedicalRecord, Prescription, LabTest
from access_control.models import StaffIDCard, AccessLog
from notifications.models import Notification


class Command(BaseCommand):
    help = 'Idempotently seeds realistic MediConnect accounts, schedules, payments, prescriptions, and Code-128 badges.'

    def handle(self, *args, **options):
        today = timezone.localdate()

        # 1. Administrator
        admin_user, created = CustomUser.objects.get_or_create(
            username='admin',
            defaults={
                'first_name': 'Tariqul',
                'last_name': 'Islam',
                'email': 'admin@mediconnect.bd',
                'phone': '01711001100',
                'role': 'ADMIN',
                'is_staff': True,
                'is_superuser': True,
            },
        )
        if created:
            admin_user.set_password('AdminPass2026!')
            admin_user.save()

        # 2. Licensed Physicians (6 Specialties)
        doctors_spec = [
            ('dr_rahman', 'Mahmudur', 'Rahman', 'CARDIOLOGY', 'BMDC-A-48219', 'MBBS, FCPS (Cardiology), MD', 14, 1200, 'Mon,Tue,Wed,Thu,Sat',
             'Senior interventional cardiologist focusing on hypertension, ischemic heart disease, and preventive lipid management.'),
            ('dr_nasrin', 'Nasrin', 'Sultana', 'NEUROLOGY', 'BMDC-A-51904', 'MBBS, MD (Neurology)', 11, 1000, 'Sun,Mon,Tue,Thu,Sat',
             'Consultant neurologist treating migraine, peripheral neuropathy, epilepsy, and post-stroke rehabilitation.'),
            ('dr_farhan', 'Farhan', 'Kabir', 'PEDIATRICS', 'BMDC-A-63012', 'MBBS, DCH, FCPS (Pediatrics)', 9, 800, 'Sun,Mon,Wed,Thu,Fri,Sat',
             'Outpatient pediatric specialist covering childhood immunization, respiratory illness, and growth nutrition.'),
            ('dr_tahmina', 'Tahmina', 'Akter', 'DERMATOLOGY', 'BMDC-A-70451', 'MBBS, DDV (BSMMU)', 8, 900, 'Mon,Tue,Wed,Fri,Sat',
             'Clinical dermatologist treating eczema, fungal infections, acne vulgaris, and allergic dermatitis.'),
            ('dr_kamal', 'Kamal', 'Uddin', 'DENTISTRY', 'BMDC-D-19340', 'BDS, FCPS (Oral & Maxillofacial)', 12, 700, 'Sun,Tue,Wed,Thu,Sat',
             'Dental and maxillofacial consultant handling endodontic pain evaluation and surgical referrals.'),
            ('dr_rubaiya', 'Rubaiya', 'Chowdhury', 'GENERAL', 'BMDC-A-58821', 'MBBS, FCPS (Internal Medicine)', 10, 750, 'Sun,Mon,Tue,Wed,Thu,Sat',
             'Internal medicine specialist managing diabetes mellitus, thyroid disorders, and febrile illnesses.'),
        ]

        doctor_objs = []
        for uname, fn, ln, spec, lic, qual, exp, fee, days, bio in doctors_spec:
            doc, was_created = CustomUser.objects.get_or_create(
                username=uname,
                defaults={
                    'first_name': fn,
                    'last_name': ln,
                    'email': f'{uname}@mediconnect.bd',
                    'phone': '01712009900',
                    'role': 'DOCTOR',
                },
            )
            if was_created:
                doc.set_password('DoctorPass2026!')
                doc.save()

            DoctorProfile.objects.update_or_create(
                user=doc,
                defaults={
                    'specialty': spec,
                    'license_number': lic,
                    'qualification': qual,
                    'experience_years': exp,
                    'consultation_fee': fee,
                    'available_days': days,
                    'consultation_start_time': datetime.time(9, 0),
                    'consultation_end_time': datetime.time(16, 0),
                    'bio': bio,
                    'is_available': True,
                    'department': dict(DoctorProfile.SPECIALTIES).get(spec, ''),
                },
            )

            # Check in primary doctors for today so demo login lands directly on dashboard
            DailyAttendance.objects.get_or_create(
                user=doc,
                date=today,
                defaults={'status': 'PRESENT', 'shift_note': 'Outpatient Telemedicine Desk'},
            )
            doctor_objs.append(doc)

        # Create explicit Availability windows for dr_rahman across Mon-Sat
        for wd in [0, 1, 2, 3, 5]:
            Availability.objects.get_or_create(
                doctor=doctor_objs[0],
                day_of_week=wd,
                start_time=datetime.time(9, 30),
                defaults={'end_time': datetime.time(15, 30), 'slot_duration': 30, 'is_active': True},
            )

        # 3. Nurse & General Staff
        nurse_user, created = CustomUser.objects.get_or_create(
            username='nurse_afsana',
            defaults={
                'first_name': 'Afsana',
                'last_name': 'Begum',
                'email': 'afsana.nurse@mediconnect.bd',
                'phone': '01815003300',
                'role': 'NURSE',
            },
        )
        if created:
            nurse_user.set_password('StaffPass2026!')
            nurse_user.save()
        StaffProfile.objects.update_or_create(
            user=nurse_user,
            defaults={
                'department': 'ICU',
                'position_title': 'Senior ICU Charge Nurse',
                'shift': 'MORNING',
                'employee_code': 'EMP-NURSE-01',
            },
        )
        DailyAttendance.objects.get_or_create(
            user=nurse_user,
            date=today,
            defaults={'status': 'PRESENT', 'shift_note': 'Floor 2 Critical Care Station'},
        )

        staff_user, created = CustomUser.objects.get_or_create(
            username='staff_rafiq',
            defaults={
                'first_name': 'Rafiqul',
                'last_name': 'Hasan',
                'email': 'rafiq.staff@mediconnect.bd',
                'phone': '01911004400',
                'role': 'STAFF',
            },
        )
        if created:
            staff_user.set_password('StaffPass2026!')
            staff_user.save()
        StaffProfile.objects.update_or_create(
            user=staff_user,
            defaults={
                'department': 'RECEPTION',
                'position_title': 'OPD Registration Officer',
                'shift': 'MORNING',
                'employee_code': 'EMP-STAFF-02',
            },
        )
        DailyAttendance.objects.get_or_create(
            user=staff_user,
            date=today,
            defaults={'status': 'PRESENT', 'shift_note': 'Ground Floor Reception Desk'},
        )

        # 4. Patients
        patient_1, created = CustomUser.objects.get_or_create(
            username='patient_tanvir',
            defaults={
                'first_name': 'Tanvir',
                'last_name': 'Ahmed',
                'email': 'tanvir.patient@example.com',
                'phone': '01755223344',
                'role': 'PATIENT',
            },
        )
        if created:
            patient_1.set_password('PatientPass2026!')
            patient_1.save()
        PatientProfile.objects.update_or_create(
            user=patient_1,
            defaults={
                'blood_group': 'B+',
                'allergies': 'Penicillin rash history',
                'medical_conditions': 'Stage 1 Hypertension',
                'emergency_contact_name': 'Samira Ahmed',
                'emergency_contact_phone': '01755223355',
            },
        )

        patient_2, created = CustomUser.objects.get_or_create(
            username='patient_maliha',
            defaults={
                'first_name': 'Maliha',
                'last_name': 'Karim',
                'email': 'maliha.patient@example.com',
                'phone': '01844112233',
                'role': 'PATIENT',
            },
        )
        if created:
            patient_2.set_password('PatientPass2026!')
            patient_2.save()

        # 5. Ready-to-Redeem Unused Access Keys
        if not AccessKey.objects.filter(status='UNUSED', target_role='DOCTOR').exists():
            AccessKey.objects.create(
                target_role='DOCTOR',
                created_by=admin_user,
                doctor_name_hint='Dr. Demo Applicant',
                specialty_hint='CARDIOLOGY',
                expires_at=timezone.now() + datetime.timedelta(days=30),
                notes='Demo single-use clinician onboarding key',
            )
        if not AccessKey.objects.filter(status='UNUSED', target_role='NURSE').exists():
            AccessKey.objects.create(
                target_role='NURSE',
                created_by=admin_user,
                doctor_name_hint='Nurse Demo Applicant',
                department_hint='EMERGENCY',
                expires_at=timezone.now() + datetime.timedelta(days=30),
                notes='Demo single-use nurse onboarding key',
            )

        # 6. Sample Appointments, Payments & Signed SOAP Prescription
        # Case A: Completed Consultation with Verified Payment, Prescription & Verified Review
        past_date = today - datetime.timedelta(days=5)
        appt_done, _ = Appointment.objects.get_or_create(
            patient=patient_1,
            doctor=doctor_objs[0],
            appointment_date=past_date,
            appointment_time=datetime.time(10, 0),
            defaults={
                'end_time': datetime.time(10, 30),
                'status': 'COMPLETED',
                'payment_status': 'PAID',
                'reason': 'Occasional morning occipital headache and home BP readings averaging 145/92 mmHg.',
                'meet_link': 'https://meet.google.com/med-card-one',
                'consultation_fee': 1200,
            },
        )
        Payment.objects.get_or_create(
            transaction_id='BK9021X7M4',
            defaults={
                'appointment': appt_done,
                'patient': patient_1,
                'amount': 1200,
                'payment_method': 'BKASH',
                'sender_number': '01755223344',
                'status': 'VERIFIED',
                'verified_by': admin_user,
                'verified_at': timezone.now() - datetime.timedelta(days=6),
            },
        )
        rec, rec_created = MedicalRecord.objects.get_or_create(
            appointment=appt_done,
            defaults={
                'patient': patient_1,
                'doctor': doctor_objs[0],
                'blood_pressure': '142/90 mmHg',
                'pulse_rate': 78,
                'temperature': 98.4,
                'spo2': 98,
                'weight_kg': 74.5,
                'chief_complaint': 'Elevated home blood pressure logs over 3 weeks with mild morning headache.',
                'examination_findings': 'Alert, no pedal edema, regular pulse, no focal neurological deficit.',
                'diagnosis': 'Essential Hypertension (Stage 1)',
                'clinical_advice': 'Restrict added dietary salt (<5g/day), brisk walk 30 mins daily, maintain morning/evening BP chart.',
                'follow_up_date': today + datetime.timedelta(days=21),
            },
        )
        if rec_created:
            Prescription.objects.create(
                medical_record=rec,
                medication_name='Tab. Amlodipine 5 mg',
                dosage='1 Tablet',
                frequency='1+0+0',
                meal_timing='AFTER_MEAL',
                duration='30 Days',
                instructions='Take at 8:00 AM daily',
            )
            Prescription.objects.create(
                medical_record=rec,
                medication_name='Tab. Losartan Potassium 50 mg',
                dosage='1 Tablet',
                frequency='0+0+1',
                meal_timing='AFTER_MEAL',
                duration='30 Days',
                instructions='Take after dinner',
            )
            LabTest.objects.create(
                medical_record=rec,
                test_name='Fasting Lipid Profile & Serum Creatinine',
                clinical_notes='10-hour overnight fast required',
                status='ORDERED',
            )

        DoctorReview.objects.get_or_create(
            appointment=appt_done,
            defaults={
                'patient': patient_1,
                'doctor': doctor_objs[0],
                'rating': 5,
                'comment': 'Clear explanation of blood pressure targets and medication schedule.',
            },
        )

        # Case B: Upcoming Confirmed & Paid Consultation (Meet-ready)
        future_paid_date = today + datetime.timedelta(days=4)
        appt_upcoming, _ = Appointment.objects.get_or_create(
            patient=patient_1,
            doctor=doctor_objs[1],
            appointment_date=future_paid_date,
            appointment_time=datetime.time(11, 0),
            defaults={
                'end_time': datetime.time(11, 30),
                'status': 'CONFIRMED',
                'payment_status': 'PAID',
                'reason': 'Recurrent unilateral throbbing headache triggered by screen glare.',
                'meet_link': 'https://meet.google.com/med-neur-two',
                'consultation_fee': 1000,
            },
        )
        Payment.objects.get_or_create(
            transaction_id='NG7741P3K8',
            defaults={
                'appointment': appt_upcoming,
                'patient': patient_1,
                'amount': 1000,
                'payment_method': 'NAGAD',
                'sender_number': '01755223344',
                'status': 'VERIFIED',
                'verified_by': admin_user,
                'verified_at': timezone.now() - datetime.timedelta(hours=4),
            },
        )

        # Case C: Pending Finance Verification in Admin Queue
        future_pending_date = today + datetime.timedelta(days=5)
        appt_pending_pay, _ = Appointment.objects.get_or_create(
            patient=patient_2,
            doctor=doctor_objs[0],
            appointment_date=future_pending_date,
            appointment_time=datetime.time(12, 0),
            defaults={
                'end_time': datetime.time(12, 30),
                'status': 'PENDING',
                'payment_status': 'PENDING_VERIFICATION',
                'reason': 'Palpitations after climbing stairs; ECG review requested.',
                'consultation_fee': 1200,
            },
        )
        Payment.objects.get_or_create(
            transaction_id='RK5519Z2W7',
            defaults={
                'appointment': appt_pending_pay,
                'patient': patient_2,
                'amount': 1200,
                'payment_method': 'ROCKET',
                'sender_number': '01844112233',
                'status': 'PENDING',
            },
        )

        # 7. Code-128 Staff ID Cards with Room-Scoped Clearance
        doc_card, _ = StaffIDCard.objects.get_or_create(
            user=doctor_objs[0],
            defaults={
                'department_label': 'Cardiology & CCU',
                'blood_group': 'O+',
                'allowed_zones': ['MAIN_ENTRANCE', 'OPD_WING', 'EMERGENCY_BAY', 'CCU_CARDIAC', 'ICU_GENERAL', 'WARD_MEDICINE'],
                'issued_by': admin_user,
            },
        )
        nurse_card, _ = StaffIDCard.objects.get_or_create(
            user=nurse_user,
            defaults={
                'department_label': 'Intensive Care Unit (ICU)',
                'blood_group': 'A+',
                'allowed_zones': ['MAIN_ENTRANCE', 'ICU_GENERAL', 'CCU_CARDIAC', 'PHARMACY_VAULT', 'BLOOD_BANK'],
                'issued_by': admin_user,
            },
        )
        staff_card, _ = StaffIDCard.objects.get_or_create(
            user=staff_user,
            defaults={
                'department_label': 'Front Desk & Reception',
                'blood_group': 'B+',
                'allowed_zones': ['MAIN_ENTRANCE', 'RECEPTION_DESK', 'OPD_WING'],
                'issued_by': admin_user,
            },
        )

        if not AccessLog.objects.exists():
            AccessLog.objects.create(
                card=doc_card,
                scanned_code=doc_card.barcode_value,
                zone_code='CCU_CARDIAC',
                result='GRANTED',
                reason_code='GRANTED',
                message='Room clearance verified.',
                scanned_by=admin_user,
            )
            AccessLog.objects.create(
                card=staff_card,
                scanned_code=staff_card.barcode_value,
                zone_code='OT_COMPLEX_A',
                result='DENIED',
                reason_code='ZONE_RESTRICTED',
                message='Badge is not authorized for Floor 2 · 14 Operation Theatre Complex A.',
                scanned_by=admin_user,
            )

        Notification.objects.get_or_create(
            recipient=patient_1,
            title='Signed Prescription Available — Rx-1',
            defaults={
                'notification_type': 'PRESCRIPTION_READY',
                'message': 'Dr. Mahmudur Rahman issued your SOAP clinical summary and printable prescription.',
                'link': f'/records/{rec.pk}/',
            },
        )

        self.stdout.write(self.style.SUCCESS('Seeded MediConnect demo data cleanly.'))
