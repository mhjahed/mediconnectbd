"""
Copy your local SQLite demo data into the live Neon PostgreSQL database.

Why it is safe
--------------
* It only ADDS rows (get-or-create style). It never deletes anything on Neon.
* Users are matched by username/email and skipped if they already exist, so
  running the command twice is harmless.
* Staff ID badges are re-created through the model's own save() (fresh Code-128
  barcode + 8-digit staff ID), so the target's UNIQUE constraints can never blow
  up — that is exactly the failure mode `seed_demo` hit across midnight.

Typical use (from D:\\CODING\\CODE OP\\mediconnect):

    python manage.py sync_local_data --dry-run          # preview, writes nothing
    python manage.py sync_local_data                    # import users/doctors/appointments/payments
    python manage.py sync_local_data --with-records     # + records, prescriptions, labs, notifications, email logs
    python manage.py sync_local_data --reset-passwords  # force a known password on every imported account

Logins: existing passwords are copied as-is (kept hashed). Use
`--reset-passwords --password "NewPass123!"` if a hash looks unusable.
"""
import datetime
import json
import sqlite3
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone


class Command(BaseCommand):
    help = 'Import local SQLite demo data (users, doctors, appointments, payments, badges, attendance) into Neon PostgreSQL.'

    def add_arguments(self, parser):
        parser.add_argument('--local-db', default=str(Path(settings.BASE_DIR) / 'db.sqlite3'),
                            help='Path to the source SQLite file (default: ./db.sqlite3)')
        parser.add_argument('--dry-run', action='store_true', help='Count what would be imported, write nothing.')
        parser.add_argument('--with-records', action='store_true',
                            help='Also copy medical records, prescriptions, lab tests, notifications and email logs.')
        parser.add_argument('--reset-passwords', action='store_true',
                            help='Overwrite every imported account password with --password.')
        parser.add_argument('--password', default='MediConnect@2026',
                            help='Password used together with --reset-passwords.')
        parser.add_argument('--force-passwords', action='store_true',
                            help='With --reset-passwords: ALSO re-hash the password for accounts that already '
                                 'exist on the target (use this to unlock an already-imported Neon DB).')

    # ------------------------------------------------------------------ setup
    def handle(self, *args, **opts):
        source = Path(opts['local_db'])
        if not source.is_file():
            raise CommandError(
                f'Source database not found: {source}\n'
                'If you already deleted db.sqlite3, use `python manage.py seed_demo` instead.'
            )

        target = settings.DATABASES['default']
        if 'sqlite' in target['ENGINE'] and Path(str(target['NAME'])).resolve() == source.resolve():
            raise CommandError(
                'Source and target are the same SQLite file.\n'
                'Set DATABASE_URL for your Neon database in .env first, then re-run.'
            )

        self.dry_run = opts['dry_run']
        self.with_records = opts['with_records']
        self.tmp_password = make_password(opts['password']) if opts['reset_passwords'] else None
        self.force_passwords = bool(opts['force_passwords'] and opts['reset_passwords'])
        self._appt_map = {}      # old local appointment id -> new Appointment
        self._record_map = {}    # old local record id -> new MedicalRecord

        self.con = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
        self.con.row_factory = sqlite3.Row

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\nSource : {source}\nTarget : "{target.get("NAME")}" on {target.get("HOST") or "local file"}\n'
        ))
        if self.dry_run:
            self.stdout.write(self.style.WARNING(
                'DRY RUN — rows are inserted then rolled back, so conflicts surface exactly as they would live.\n'))

        stats = {}
        try:
            with transaction.atomic():
                stats['users'] = self.copy_users()
                if self.force_passwords:
                    stats['passwords rehashed'] = self.rehash_existing_passwords()
                stats['patient profiles'] = self.copy_profiles(
                    'accounts_patientprofile', 'patient_profile', 'copy_patient_profile')
                stats['doctor profiles'] = self.copy_profiles(
                    'accounts_doctorprofile', 'doctor_profile', 'copy_doctor_profile')
                stats['staff profiles'] = self.copy_profiles(
                    'accounts_staffprofile', 'staff_profile', 'copy_staff_profile')
                stats['availability slots'] = self.copy_availability()
                stats['appointments'] = self.copy_appointments()
                stats['payments'] = self.copy_payments()
                stats['attendance days'] = self.copy_attendance()
                stats['ID badges'] = self.copy_id_cards()
                stats['access keys'] = self.copy_access_keys()
                if self.with_records:
                    stats['medical records'] = self.copy_medical_records()
                    stats['prescriptions'] = self.copy_prescriptions()
                    stats['lab tests'] = self.copy_lab_tests()
                    stats['notifications'] = self.copy_notifications()
                    stats['email logs'] = self.copy_email_logs()
                if self.dry_run:
                    transaction.set_rollback(True)
        finally:
            self.con.close()

        self.stdout.write(self.style.MIGRATE_HEADING('\nSummary'))
        for key, val in stats.items():
            if isinstance(val, tuple):
                self.stdout.write(f'  {key:<20} {val[0]} imported, {val[1]} already present/skipped')
            else:
                self.stdout.write(f'  {key:<20} {val} imported')

        if self.dry_run:
            self.stdout.write(self.style.WARNING('\nDry run finished — nothing written. Re-run without --dry-run.'))
        else:
            self.stdout.write(self.style.SUCCESS('\nImport complete.'))
            self.stdout.write('Verify with:  python manage.py shell -c '
                              '"from accounts.models import CustomUser as U; print(U.objects.count(), \'users\')"')

    # ---------------------------------------------------------------- utilities
    def rows(self, table):
        try:
            return self.con.execute(f'SELECT * FROM {table}').fetchall()
        except sqlite3.OperationalError:
            return []

    @staticmethod
    def _aware(value):
        """SQLite stores naive datetimes; make them timezone-aware, default to now."""
        if not value:
            return timezone.now()
        if isinstance(value, str):
            from django.utils.dateparse import parse_datetime
            parsed = parse_datetime(value)
            if parsed is None:
                return timezone.now()
            value = parsed
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_default_timezone())
        return value

    @staticmethod
    def col(row, name, default=None):
        try:
            keys = row.keys()
        except AttributeError:
            return default
        if name not in keys:
            return default
        value = row[name]
        # SQLite hands back naive datetimes; Django 5 with USE_TZ wants aware ones.
        if isinstance(value, datetime.datetime) and timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_default_timezone())
        return value

    def username_of(self, local_user_id):
        if not local_user_id:
            return None
        try:
            row = self.con.execute(
                'SELECT username FROM accounts_customuser WHERE id = ?', (local_user_id,)
            ).fetchone()
            return row['username'] if row else None
        except sqlite3.OperationalError:
            return None

    def user_by_local_id(self, local_user_id):
        from accounts.models import CustomUser
        username = self.username_of(local_user_id)
        return CustomUser.objects.filter(username=username).first() if username else None

    # -------------------------------------------------------------------- users
    def copy_users(self):
        from accounts.models import CustomUser

        created = skipped = 0
        for r in self.rows('accounts_customuser'):
            if CustomUser.objects.filter(username=r['username']).exists():
                skipped += 1
                continue
            if r['email'] and CustomUser.objects.filter(email__iexact=r['email']).exists():
                skipped += 1
                continue
            created += 1
            CustomUser.objects.create(
                username=r['username'],
                email=r['email'] or '',
                password=self.tmp_password or r['password'],
                first_name=r['first_name'] or '',
                last_name=r['last_name'] or '',
                role=r['role'] or 'PATIENT',
                phone=r['phone'] or '',
                address=r['address'] or '',
                gender=r['gender'] or '',
                date_of_birth=r['date_of_birth'] or None,
                is_active=bool(r['is_active']),
                is_staff=bool(r['is_staff']),
                is_superuser=bool(r['is_superuser']),
                last_login=self.col(r, 'last_login'),
            )
        return created, skipped

    def rehash_existing_passwords(self):
        """Reset the password for every account in the source dump that already exists on the target."""
        from accounts.models import CustomUser

        updated = missing = 0
        for r in self.rows('accounts_customuser'):
            user = CustomUser.objects.filter(username=r['username']).first()
            if not user:
                missing += 1
                continue
            user.password = self.tmp_password
            user.save(update_fields=['password'])
            updated += 1
        return f'{updated} reset, {missing} not found on target'

    def copy_profiles(self, table, attr, method_name):
        created = skipped = 0
        handler = getattr(self, method_name)
        for r in self.rows(table):
            user = self.user_by_local_id(r['user_id'])
            if not user or hasattr(user, attr):
                skipped += 1
                continue
            created += 1
            handler(user, r)
        return created, skipped

    def copy_patient_profile(self, user, r):
        from accounts.models import PatientProfile
        PatientProfile.objects.create(
            user=user,
            blood_group=self.col(r, 'blood_group', '') or '',
            allergies=self.col(r, 'allergies', '') or '',
            medical_conditions=self.col(r, 'medical_conditions', '') or '',
            emergency_contact_name=self.col(r, 'emergency_contact_name', '') or '',
            emergency_contact_phone=self.col(r, 'emergency_contact_phone', '') or '',
            insurance_number=self.col(r, 'insurance_number', '') or '',
        )

    def copy_doctor_profile(self, user, r):
        from accounts.models import DoctorProfile
        DoctorProfile.objects.create(
            user=user,
            specialty=self.col(r, 'specialty', 'GENERAL') or 'GENERAL',
            license_number=self.col(r, 'license_number', '') or '',
            experience_years=self.col(r, 'experience_years', 0) or 0,
            qualification=self.col(r, 'qualification', '') or '',
            bio=self.col(r, 'bio', '') or '',
            consultation_fee=self.col(r, 'consultation_fee', 0) or 0,
            available_days=self.col(r, 'available_days', '') or '',
            consultation_start_time=self.col(r, 'consultation_start_time'),
            consultation_end_time=self.col(r, 'consultation_end_time'),
            max_patients_per_day=self.col(r, 'max_patients_per_day', 20) or 20,
            is_available=bool(self.col(r, 'is_available', True)),
            department=self.col(r, 'department', '') or '',
        )

    def copy_staff_profile(self, user, r):
        from accounts.models import StaffProfile
        StaffProfile.objects.create(
            user=user,
            department=self.col(r, 'department', '') or '',
            position_title=self.col(r, 'position_title', '') or '',
            shift=self.col(r, 'shift', 'MORNING') or 'MORNING',
            employee_code=self.col(r, 'employee_code', '') or '',
            joining_date=self.col(r, 'joining_date'),
        )

    # -------------------------------------------------------------- availability
    def copy_availability(self):
        from appointments.models import Availability

        created = skipped = 0
        for r in self.rows('appointments_availability'):
            doctor = self.user_by_local_id(r['doctor_id'])
            if not doctor:
                skipped += 1
                continue
            if Availability.objects.filter(
                doctor=doctor, day_of_week=r['day_of_week'],
                start_time=r['start_time'], end_time=r['end_time'],
            ).exists():
                skipped += 1
                continue
            created += 1
            Availability.objects.create(
                doctor=doctor,
                day_of_week=r['day_of_week'],
                start_time=r['start_time'],
                end_time=r['end_time'],
                slot_duration=self.col(r, 'slot_duration', 30) or 30,
                is_active=bool(self.col(r, 'is_active', True)),
            )
        return created, skipped

    # -------------------------------------------------------------- appointments
    def copy_appointments(self):
        from appointments.models import Appointment

        created = skipped = 0
        for r in self.rows('appointments_appointment'):
            patient = self.user_by_local_id(r['patient_id'])
            doctor = self.user_by_local_id(r['doctor_id'])
            if not patient or not doctor:
                skipped += 1
                continue
            existing = Appointment.objects.filter(
                patient=patient, doctor=doctor,
                appointment_date=r['appointment_date'], appointment_time=r['appointment_time'],
            ).first()
            if existing:
                self._appt_map[r['id']] = existing
                skipped += 1
                continue
            created += 1
            appt = Appointment.objects.create(
                patient=patient,
                doctor=doctor,
                appointment_date=r['appointment_date'],
                appointment_time=r['appointment_time'],
                end_time=self.col(r, 'end_time'),
                status=r['status'] or 'PENDING',
                payment_status=self.col(r, 'payment_status', 'UNPAID') or 'UNPAID',
                reason=self.col(r, 'reason', '') or '',
                notes=self.col(r, 'notes', '') or '',
                meet_link=self.col(r, 'meet_link', '') or '',
                consultation_fee=self.col(r, 'consultation_fee', 0) or 0,
                created_at=self._aware(self.col(r, 'created_at')),
            )
            self._appt_map[r['id']] = appt
        return created, skipped

    # ------------------------------------------------------------------ payments
    def copy_payments(self):
        from payments.models import Payment

        created = skipped = 0
        for r in self.rows('payments_payment'):
            appt = self._appt_map.get(r['appointment_id'])
            patient = self.user_by_local_id(r['patient_id'])
            if not appt or not patient:
                skipped += 1
                continue
            if Payment.objects.filter(transaction_id=r['transaction_id']).exists():
                skipped += 1
                continue
            created += 1
            verifier = self.user_by_local_id(self.col(r, 'verified_by_id'))
            Payment.objects.create(
                appointment=appt,
                patient=patient,
                amount=r['amount'],
                payment_method=r['payment_method'] or 'BKASH',
                transaction_id=r['transaction_id'],
                sender_number=self.col(r, 'sender_number', '') or '',
                receipt_number=self.col(r, 'receipt_number', '') or f'RCPT-{uuid.uuid4().hex[:8].upper()}',
                qr_token=self.col(r, 'qr_token', '') or f'PASS-{uuid.uuid4().hex[:10].upper()}',
                status=r['status'] or 'PENDING',
                verified_by=verifier,
                verified_at=self.col(r, 'verified_at'),
                patient_checked_in_at=self.col(r, 'patient_checked_in_at'),
                rejection_reason=self.col(r, 'rejection_reason', '') or '',
                notes=self.col(r, 'notes', '') or '',
                created_at=self._aware(self.col(r, 'created_at')),
            )
        return created, skipped

    # ---------------------------------------------------------------- attendance
    def copy_attendance(self):
        from accounts.models import DailyAttendance

        created = skipped = 0
        for r in self.rows('accounts_dailyattendance'):
            user = self.user_by_local_id(r['user_id'])
            if not user:
                skipped += 1
                continue
            if DailyAttendance.objects.filter(user=user, date=r['date']).exists():
                skipped += 1
                continue
            created += 1
            DailyAttendance.objects.create(
                user=user,
                date=r['date'],
                check_in_time=self.col(r, 'check_in_time'),
                check_out_time=self.col(r, 'check_out_time'),
                status=r['status'] or 'PRESENT',
                shift_note=self.col(r, 'shift_note', '') or '',
            )
        return created, skipped

    # ------------------------------------------------------------------ ID cards
    def copy_id_cards(self):
        from access_control.models import StaffIDCard

        created = skipped = 0
        for r in self.rows('access_control_staffidcard'):
            user = self.user_by_local_id(r['user_id'])
            if not user or hasattr(user, 'id_card'):
                skipped += 1
                continue
            raw_zones = self.col(r, 'allowed_zones', '[]')
            try:
                zones = json.loads(raw_zones) if isinstance(raw_zones, str) else list(raw_zones or [])
            except Exception:
                zones = ['MAIN_ENTRANCE']
            created += 1
            card = StaffIDCard(
                user=user,
                department_label=self.col(r, 'department_label', 'Outpatient Department') or 'Outpatient Department',
                blood_group=self.col(r, 'blood_group', 'O+') or 'O+',
                allowed_zones=zones,
                status=self.col(r, 'status', 'ACTIVE') or 'ACTIVE',
                expiry_date=self.col(r, 'expiry_date') or (timezone.localdate() + timezone.timedelta(days=730)),
                issued_by=self.user_by_local_id(self.col(r, 'issued_by_id')),
            )
            card.save()  # generates a fresh unique barcode + staff ID -> zero UNIQUE collisions
        return created, skipped

    # ---------------------------------------------------------------- access keys
    def copy_access_keys(self):
        from accounts.models import AccessKey

        created = skipped = 0
        for r in self.rows('accounts_accesskey'):
            if AccessKey.objects.filter(key=r['key']).exists():
                skipped += 1
                continue
            created += 1
            AccessKey.objects.create(
                key=r['key'],
                target_role=self.col(r, 'target_role', 'PATIENT') or 'PATIENT',
                created_by=self.user_by_local_id(self.col(r, 'created_by_id')),
                doctor_name_hint=self.col(r, 'doctor_name_hint', '') or '',
                specialty_hint=self.col(r, 'specialty_hint', '') or '',
                department_hint=self.col(r, 'department_hint', '') or '',
                status=self.col(r, 'status', 'UNUSED') or 'UNUSED',
                used_at=self.col(r, 'used_at'),
                expires_at=self.col(r, 'expires_at'),
                notes=self.col(r, 'notes', '') or '',
            )
        return created, skipped

    # ----------------------------------------------------------- clinical extras
    def copy_medical_records(self):
        from medical_records.models import MedicalRecord

        created = skipped = 0
        for r in self.rows('medical_records_medicalrecord'):
            appt = self._appt_map.get(r['appointment_id'])
            if not appt:
                skipped += 1
                continue
            if MedicalRecord.objects.filter(appointment=appt).exists():
                skipped += 1
                continue
            created += 1
            record = MedicalRecord.objects.create(
                appointment=appt,
                patient=appt.patient,
                doctor=appt.doctor,
                blood_pressure=self.col(r, 'blood_pressure', '') or '',
                pulse_rate=self.col(r, 'pulse_rate'),
                temperature=self.col(r, 'temperature'),
                spo2=self.col(r, 'spo2'),
                weight_kg=self.col(r, 'weight_kg'),
                chief_complaint=self.col(r, 'chief_complaint', '') or '',
                examination_findings=self.col(r, 'examination_findings', '') or '',
                diagnosis=self.col(r, 'diagnosis', '') or '',
                clinical_advice=self.col(r, 'clinical_advice', '') or '',
                follow_up_date=self.col(r, 'follow_up_date'),
            )
            self._record_map[r['id']] = record
        return created, skipped

    def copy_prescriptions(self):
        from medical_records.models import Prescription

        created = skipped = 0
        for r in self.rows('medical_records_prescription'):
            record = self._record_map.get(r['medical_record_id'])
            if not record:
                skipped += 1
                continue
            created += 1
            Prescription.objects.create(
                medical_record=record,
                medication_name=self.col(r, 'medication_name', '') or '',
                dosage=self.col(r, 'dosage', '') or '',
                frequency=self.col(r, 'frequency', '') or '',
                meal_timing=self.col(r, 'meal_timing', '') or '',
                duration=self.col(r, 'duration', '') or '',
                instructions=self.col(r, 'instructions', '') or '',
            )
        return created, skipped

    def copy_lab_tests(self):
        from medical_records.models import LabTest

        created = skipped = 0
        for r in self.rows('medical_records_labtest'):
            record = self._record_map.get(r['medical_record_id'])
            if not record:
                skipped += 1
                continue
            created += 1
            LabTest.objects.create(
                medical_record=record,
                test_name=self.col(r, 'test_name', '') or '',
                clinical_notes=self.col(r, 'clinical_notes', '') or '',
                status=self.col(r, 'status', 'PENDING') or 'PENDING',
                result_summary=self.col(r, 'result_summary', '') or '',
            )
        return created, skipped

    def copy_notifications(self):
        from notifications.models import Notification

        created = skipped = 0
        for r in self.rows('notifications_notification'):
            recipient = self.user_by_local_id(self.col(r, 'recipient_id'))
            if not recipient:
                skipped += 1
                continue
            if Notification.objects.filter(
                recipient=recipient,
                title=self.col(r, 'title', '') or '',
                message=self.col(r, 'message', '') or '',
            ).exists():
                skipped += 1
                continue
            created += 1
            Notification.objects.create(
                recipient=recipient,
                notification_type=self.col(r, 'notification_type', 'SYSTEM') or 'SYSTEM',
                title=self.col(r, 'title', '') or '',
                message=self.col(r, 'message', '') or '',
                link=self.col(r, 'link', '') or '',
                is_read=bool(self.col(r, 'is_read', False)),
                email_sent=bool(self.col(r, 'email_sent', False)),
            )
        return created, skipped

    def copy_email_logs(self):
        from notifications.models import EmailDispatchLog

        created = skipped = 0
        for r in self.rows('notifications_emaildispatchlog'):
            created += 1
            EmailDispatchLog.objects.create(
                sent_by=self.user_by_local_id(self.col(r, 'sent_by_id')),
                recipient_user=self.user_by_local_id(self.col(r, 'recipient_user_id')),
                recipient_name=self.col(r, 'recipient_name', '') or '',
                recipient_email=self.col(r, 'recipient_email', '') or '',
                template_key=self.col(r, 'template_key', '') or '',
                subject=self.col(r, 'subject', '') or '',
                reference_code=self.col(r, 'reference_code', '') or '',
                status=self.col(r, 'status', 'SENT') or 'SENT',
                error_message=self.col(r, 'error_message', '') or '',
            )
        return created, skipped
