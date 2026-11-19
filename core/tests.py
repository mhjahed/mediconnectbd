import datetime
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, Client, override_settings
from django.utils import timezone

from accounts.models import CustomUser, DoctorProfile, AccessKey, DailyAttendance
from appointments.models import Appointment
from payments.models import Payment
from medical_records.models import MedicalRecord
from access_control.models import StaffIDCard, AccessLog
from notifications.models import EmailDispatchLog


class MediConnectSecurityAndWorkflowTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.today = timezone.localdate()

        self.admin = CustomUser.objects.create_user(
            username='sec_admin', password='StrongPassword123!', role='ADMIN', email='a@mc.bd'
        )
        self.doc = CustomUser.objects.create_user(
            username='sec_doc', first_name='Nadia', last_name='Haque',
            password='StrongPassword123!', role='DOCTOR', email='d@mc.bd'
        )
        DoctorProfile.objects.create(
            user=self.doc,
            specialty='CARDIOLOGY',
            license_number='BMDC-SEC-001',
            qualification='MBBS, FCPS',
            consultation_fee=1000,
            available_days='Mon,Tue,Wed,Thu,Fri,Sat,Sun',
        )

        self.doc_other = CustomUser.objects.create_user(
            username='sec_doc2', first_name='Imran', last_name='Ali',
            password='StrongPassword123!', role='DOCTOR', email='d2@mc.bd'
        )
        DoctorProfile.objects.create(
            user=self.doc_other,
            specialty='GENERAL',
            license_number='BMDC-SEC-002',
            qualification='MBBS',
            consultation_fee=600,
        )

        self.patient_a = CustomUser.objects.create_user(
            username='pat_a', first_name='Ayesha', last_name='Siddiqua',
            password='StrongPassword123!', role='PATIENT', email='pa@mc.bd'
        )
        self.patient_b = CustomUser.objects.create_user(
            username='pat_b', first_name='Karim', last_name='Ullah',
            password='StrongPassword123!', role='PATIENT', email='pb@mc.bd'
        )
        self.staff = CustomUser.objects.create_user(
            username='sec_staff', first_name='Babu', last_name='Mia',
            password='StrongPassword123!', role='STAFF', email='s@mc.bd'
        )

    def test_01_public_and_seo_routes_render_cleanly(self):
        for url in ['/', '/appointments/search/', '/legal/privacy/', '/legal/terms/', '/legal/refunds/', '/robots.txt', '/llms.txt', '/sitemap.xml']:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, f"Expected 200 on {url}")

    def test_02_doctor_attendance_gatekeeper_enforced(self):
        self.client.login(username='sec_doc', password='StrongPassword123!')
        resp = self.client.get('/dashboard/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/attendance/', resp['Location'])

        # Check in and verify access unlocks
        self.client.post('/accounts/attendance/', {'action': 'checkin', 'shift_note': 'Morning OPD'})
        resp_after = self.client.get('/dashboard/')
        self.assertEqual(resp_after.status_code, 200)

    def test_03_advance_72_hour_booking_rule_and_idempotency(self):
        self.client.login(username='pat_a', password='StrongPassword123!')
        too_soon = self.today + datetime.timedelta(days=1)
        resp_soon = self.client.get(f'/appointments/api/slots/{self.doc.id}/?date={too_soon.isoformat()}')
        self.assertEqual(resp_soon.json()['slots'], [])

        valid_date = self.today + datetime.timedelta(days=4)
        idem = 'idem_test_unique_booking_token_01'
        post_data = {
            'idempotency_key': idem,
            'appointment_date': valid_date.isoformat(),
            'appointment_time': '10:00',
            'reason': 'Chest tightness evaluation',
            'telemedicine_consent': '1',
        }
        r1 = self.client.post(f'/appointments/book/{self.doc.id}/', post_data)
        self.assertEqual(r1.status_code, 302)
        self.assertEqual(Appointment.objects.filter(patient=self.patient_a).count(), 1)

        # Replay identical idempotency key -> must NOT create a second appointment
        r2 = self.client.post(f'/appointments/book/{self.doc.id}/', post_data)
        self.assertEqual(r2.status_code, 302)
        self.assertEqual(Appointment.objects.filter(patient=self.patient_a).count(), 1)

    def test_04_payment_idempotency_and_doctor_paid_queue_isolation(self):
        DailyAttendance.objects.create(user=self.doc, date=self.today, status='PRESENT')
        appt = Appointment.objects.create(
            patient=self.patient_a,
            doctor=self.doc,
            appointment_date=self.today + datetime.timedelta(days=4),
            appointment_time=datetime.time(11, 0),
            reason='Routine cardiac check',
            consultation_fee=1000,
        )

        # Unpaid appointment must NOT appear in doctor's paid queue
        self.client.login(username='sec_doc', password='StrongPassword123!')
        q_resp = self.client.get('/appointments/doctor/')
        self.assertNotContains(q_resp, 'Ayesha Siddiqua')

        # Patient submits payment
        self.client.login(username='pat_a', password='StrongPassword123!')
        pay_payload = {
            'idempotency_key': 'idem_pay_token_99',
            'payment_method': 'BKASH',
            'sender_number': '01700000000',
            'transaction_id': 'BKUNIQUE7788',
            'notes': 'Paid from personal wallet',
            'payment_consent': '1',
        }
        self.client.post(f'/payments/submit/{appt.pk}/', pay_payload)
        self.client.post(f'/payments/submit/{appt.pk}/', pay_payload)  # duplicate replay
        self.assertEqual(Payment.objects.filter(appointment=appt).count(), 1)

        # Admin verifies payment -> unlocks doctor paid queue
        payment = Payment.objects.get(appointment=appt)
        self.client.login(username='sec_admin', password='StrongPassword123!')
        self.client.post(f'/payments/{payment.pk}/verify/', {'decision': 'VERIFY'})

        appt.refresh_from_db()
        self.assertEqual(appt.payment_status, 'PAID')
        self.assertEqual(appt.status, 'CONFIRMED')

        self.client.login(username='sec_doc', password='StrongPassword123!')
        q_resp_after = self.client.get('/appointments/doctor/')
        self.assertContains(q_resp_after, 'Ayesha Siddiqua')

    def test_05_row_level_idor_protection_on_medical_records_and_receipts(self):
        appt = Appointment.objects.create(
            patient=self.patient_a,
            doctor=self.doc,
            appointment_date=self.today - datetime.timedelta(days=2),
            appointment_time=datetime.time(10, 0),
            status='COMPLETED',
            payment_status='PAID',
            reason='Follow up',
            consultation_fee=1000,
        )
        pay = Payment.objects.create(
            appointment=appt,
            patient=self.patient_a,
            amount=1000,
            payment_method='NAGAD',
            transaction_id='NGIDORTEST1',
            sender_number='01711111111',
            status='VERIFIED',
        )
        rec = MedicalRecord.objects.create(
            appointment=appt,
            patient=self.patient_a,
            doctor=self.doc,
            chief_complaint='Headache',
            diagnosis='Tension Headache',
        )

        # Patient B tries to read Patient A's medical record, print Rx, appointment, and receipt -> 403 Forbidden
        self.client.login(username='pat_b', password='StrongPassword123!')
        for protected_url in [
            f'/records/{rec.pk}/',
            f'/records/{rec.pk}/print/',
            f'/appointments/{appt.pk}/',
            f'/payments/{pay.pk}/receipt/',
        ]:
            res = self.client.get(protected_url)
            self.assertEqual(res.status_code, 403, f"IDOR leak on {protected_url}")

        # Unrelated Doctor 2 tries to read Patient A's medical record -> 403 Forbidden
        DailyAttendance.objects.create(user=self.doc_other, date=self.today, status='PRESENT')
        self.client.login(username='sec_doc2', password='StrongPassword123!')
        self.assertEqual(self.client.get(f'/records/{rec.pk}/').status_code, 403)

    def test_06_barcode_scanner_enforces_room_level_zone_authorization(self):
        # The scanner API is staff-only, so authenticate as the issuing staff member first.
        self.client.login(username='sec_staff', password='StrongPassword123!')
        card = StaffIDCard.objects.create(
            user=self.staff,
            department_label='Reception',
            allowed_zones=['MAIN_ENTRANCE', 'RECEPTION_DESK'],
            issued_by=self.admin,
        )

        # Authorized room -> GRANTED
        r_ok = self.client.post(
            '/access/scan/',
            data=json.dumps({'barcode': card.barcode_value, 'zone': 'MAIN_ENTRANCE'}),
            content_type='application/json',
        )
        self.assertEqual(r_ok.status_code, 200)
        self.assertEqual(r_ok.json()['result'], 'GRANTED')

        # Restricted Operation Theatre -> DENIED (ZONE_RESTRICTED)
        r_deny = self.client.post(
            '/access/scan/',
            data=json.dumps({'barcode': card.barcode_value, 'zone': 'OT_COMPLEX_A'}),
            content_type='application/json',
        )
        self.assertEqual(r_deny.status_code, 200)
        self.assertEqual(r_deny.json()['result'], 'DENIED')
        self.assertEqual(r_deny.json()['reason_code'], 'ZONE_RESTRICTED')

        # Suspended card -> DENIED even on allowed zone
        card.status = 'SUSPENDED'
        card.save()
        r_susp = self.client.post(
            '/access/scan/',
            data=json.dumps({'barcode': card.barcode_value, 'zone': 'MAIN_ENTRANCE'}),
            content_type='application/json',
        )
        self.assertEqual(r_susp.json()['result'], 'DENIED')
        self.assertEqual(AccessLog.objects.count(), 3)

    def test_07_single_use_access_key_cannot_be_reused(self):
        key = AccessKey.objects.create(target_role='DOCTOR', created_by=self.admin, specialty_hint='NEUROLOGY')
        self.assertTrue(key.is_valid)
        key.mark_used(self.doc)
        key.refresh_from_db()
        self.assertFalse(key.is_valid)
        self.assertEqual(key.status, 'USED')

    def test_08_patient_receipt_qr_entry_verification_by_staff(self):
        DailyAttendance.objects.create(user=self.staff, date=self.today, status='PRESENT')
        appt = Appointment.objects.create(
            patient=self.patient_a,
            doctor=self.doc,
            appointment_date=self.today + datetime.timedelta(days=4),
            appointment_time=datetime.time(10, 30),
            status='PENDING',
            payment_status='PENDING_VERIFICATION',
            reason='Outpatient cardiology check',
            consultation_fee=1000,
        )
        pay = Payment.objects.create(
            appointment=appt,
            patient=self.patient_a,
            amount=1000,
            payment_method='BKASH',
            transaction_id='BKQRPASS99',
            sender_number='01799998888',
            status='PENDING',
        )
        self.assertTrue(pay.qr_token.startswith('PASS-'))

        # Staff scans unverified receipt QR -> DENIED
        self.client.login(username='sec_staff', password='StrongPassword123!')
        r_unverified = self.client.post(
            '/access/patient-entry/verify/',
            data=json.dumps({'qr_code': pay.qr_payload}),
            content_type='application/json',
        )
        self.assertEqual(r_unverified.status_code, 200)
        self.assertEqual(r_unverified.json()['result'], 'DENIED')

        # Admin verifies payment -> Staff scans same QR -> GRANTED + records arrival timestamp
        pay.status = 'VERIFIED'
        pay.save()
        appt.payment_status = 'PAID'
        appt.status = 'CONFIRMED'
        appt.save()

        r_verified = self.client.post(
            '/access/patient-entry/verify/',
            data=json.dumps({'qr_code': pay.qr_payload}),
            content_type='application/json',
        )
        self.assertEqual(r_verified.status_code, 200)
        self.assertEqual(r_verified.json()['result'], 'GRANTED')
        pay.refresh_from_db()
        self.assertIsNotNone(pay.patient_checked_in_at)
        self.assertEqual(pay.checked_in_by, self.staff)

    def test_09_gmail_mailer_console_previews_and_dispatch_audit(self):
        # Non-admin users cannot access Mailer Console
        self.client.login(username='pat_a', password='StrongPassword123!')
        r_forbid = self.client.get('/notifications/mailer/compose/')
        self.assertEqual(r_forbid.status_code, 403)

        # Admin previews all 8 templates (verifying 3-part top color bar + MediConnect branding)
        self.client.login(username='sec_admin', password='StrongPassword123!')
        for tpl in (
            'consultation_scheduled', 'consultation_reminder', 'payment_receipt',
            'outpatient_checkin', 'prescription_ready', 'credentials_delivery',
            'staff_onboarding', 'general_update'
        ):
            prev = self.client.get(f'/notifications/mailer/preview/{tpl}/?name=Jahed')
            self.assertEqual(prev.status_code, 200)
            self.assertContains(prev, '#FFC93C')
            self.assertContains(prev, 'MediConnect')

        # Admin dispatches a Credentials Delivery email via Compose Console
        r_send = self.client.post('/notifications/mailer/compose/', {
            'template_key': 'credentials_delivery',
            'subject': 'MediConnect — Your Portal Credentials',
            'target_mode': 'custom',
            'custom_name': 'Jahed Rahman',
            'custom_email': 'jahed@example.com',
            'portal_username': 'jahed.rahman',
            'portal_password': 'StudyMed2026!',
            'reference_code': 'MC-2026-BD-0001',
        })
        self.assertEqual(r_send.status_code, 302)
        self.assertTrue(EmailDispatchLog.objects.filter(
            recipient_email='jahed@example.com',
            template_key='credentials_delivery',
        ).exists())


    def test_10_door_scanner_is_staff_only_and_blocks_patient_bruteforce(self):
        """Ward terminal + badge API must reject anonymous and patient accounts (403)."""
        url_page = '/access/scanner/'
        url_api = '/access/scan/'
        payload = json.dumps({'barcode': 'MEDI-00000000', 'zone': 'MAIN_ENTRANCE'})

        # 1. Anonymous visitor -> redirected to login (never sees the terminal)
        anon = self.client.get(url_page)
        self.assertIn(anon.status_code, (301, 302))
        self.assertIn('/accounts/login/', anon.headers.get('Location', ''))

        anon_api = self.client.post(url_api, data=payload, content_type='application/json')
        self.assertIn(anon_api.status_code, (301, 302, 403))
        self.assertEqual(
            AccessLog.objects.count(), 0,
            'Anonymous scan attempts must never reach the database.',
        )

        # 2. Authenticated PATIENT -> 403 on both page and API
        self.client.login(username='pat_a', password='StrongPassword123!')
        self.assertEqual(self.client.get(url_page).status_code, 403)
        self.assertEqual(
            self.client.post(url_api, data=payload, content_type='application/json').status_code,
            403,
        )
        self.assertEqual(AccessLog.objects.count(), 0)

        # 3. Authorized STAFF account -> terminal opens and scans are evaluated
        self.client.login(username='sec_staff', password='StrongPassword123!')
        StaffIDCard.objects.create(
            user=self.staff,
            department_label='Reception',
            allowed_zones=['MAIN_ENTRANCE'],
            issued_by=self.admin,
        )
        self.assertEqual(self.client.get(url_page).status_code, 200)
        ok = self.client.post(url_api, data=payload, content_type='application/json')
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(AccessLog.objects.count(), 1)

        # 4. The terminal link is hidden from patients in the public navigation
        self.client.login(username='pat_a', password='StrongPassword123!')
        home = self.client.get('/')
        self.assertEqual(home.status_code, 200)
        self.assertNotContains(home, '/access/scanner/')

    def test_11_live_alert_api_is_row_scoped_and_feeds_popups(self):
        """The popup endpoint only ever returns the caller's own alerts."""
        from notifications.models import Notification

        mine = Notification.objects.create(
            recipient=self.patient_a, notification_type='PAYMENT_VERIFIED',
            title='Payment verified', message='Your bKash payment is confirmed.', link='/payments/1/receipt/',
        )
        Notification.objects.create(
            recipient=self.patient_b, notification_type='GENERAL',
            title='Someone else', message='Must never leak.', link='/notifications/',
        )

        # Anonymous -> redirected to login
        self.assertIn(self.client.get('/notifications/live/').status_code, (301, 302))

        self.client.login(username='pat_a', password='StrongPassword123!')

        # Unread counter reflects only my row
        res = self.client.get('/notifications/live/?after=0')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['unread_count'], 1)
        self.assertEqual(len(data['notifications']), 1)
        self.assertEqual(data['notifications'][0]['title'], 'Payment verified')
        self.assertEqual(data['notifications'][0]['id'], mine.pk)

        # prime=1 is silent (no historical popup burst) but still reports the newest id
        prime = self.client.get('/notifications/live/?after=0&prime=1').json()
        self.assertEqual(prime['notifications'], [])
        self.assertEqual(prime['latest_id'], mine.pk)

        # After the id is seen, polling returns nothing new
        after = self.client.get(f'/notifications/live/?after={mine.pk}').json()
        self.assertEqual(after['notifications'], [])

        # AJAX mark-as-read returns JSON and decrements the counter
        mark = self.client.post(f'/notifications/{mine.pk}/read/', {'ajax': '1'})
        self.assertEqual(mark.status_code, 200)
        self.assertEqual(mark.json()['unread_count'], 0)
        mine.refresh_from_db()
        self.assertTrue(mine.is_read)

    def test_12_unknown_urls_never_dump_the_route_map(self):
        """Random probes must return the branded 404, never Django's urlconf dump."""
        from django.test import override_settings

        for probe in ('/dawd', '/some/unknown/deep/path', '/admin/', '/django-admin/'):
            res = self.client.get(probe)
            self.assertEqual(res.status_code, 404, probe)
            body = res.content.decode('utf-8', errors='ignore')
            self.assertNotIn('Using the URLconf defined in', body, f'urlconf dumped on {probe}')
            self.assertNotIn('Django tried these URL patterns', body, probe)
            self.assertNotIn("name='telemetry_pulse'", body, probe)

        # Admin camouflage: the camo prefix rewrites to the real admin route
        with override_settings(ADMIN_CAMO_PREFIX='mc-registry-vault/', ADMIN_URL_PATH='django-admin/'):
            self.client.login(username='sec_admin', password='StrongPassword123!')
            camo = self.client.get('/mc-registry-vault/', follow=False)
            self.assertIn(camo.status_code, (200, 302))
            self.assertNotIn('/django-admin/', camo.content.decode('utf-8', errors='ignore'))
            self.assertEqual(self.client.get('/admin/').status_code, 404)

    def test_13_route_map_is_hidden_after_cutoff(self):
        """HIDE_ROUTE_LIST_AFTER in the past forces the branded 404 even for odd shapes."""
        from django.test import override_settings

        with override_settings(HIDE_ROUTE_LIST_AFTER='2020-01-01'):
            for probe in ('/dawd', '/dawd/', '/nope.html', '/a/b/c'):
                res = self.client.get(probe)
                self.assertEqual(res.status_code, 404, probe)
                body = res.content.decode('utf-8', errors='ignore')
                self.assertNotIn('URL patterns', body)
                self.assertNotIn('urlconf', body.lower())
            # JSON clients get JSON, never HTML debug output
            js = self.client.get('/dawd', HTTP_ACCEPT='application/json')
            self.assertEqual(js.status_code, 404)
            self.assertEqual(js.json(), {'detail': 'Not found.'})

    def test_14_pulse_relay_forwards_session_id_and_pageleave(self):
        """The relay must pass a UUIDv7 session id + leave metrics to PostHog."""
        captured = {}

        class FakeResp:
            status_code = 200

        def fake_post(url, json=None, timeout=None):
            captured['url'] = url
            captured['body'] = json
            return FakeResp()

        with override_settings(POSTHOG_API_KEY='phc_test_key'), \
                patch('core.views.requests.post', side_effect=fake_post):
            res = self.client.post(
                '/pulse/',
                data=json.dumps({
                    'event': '$pageleave',
                    'anon_id': 'mc_anon_1',
                    'session_id': '019930b4-7c2a-7b3e-8a4f-1d2c3b4a5e6f',
                    'path': '/appointments/search/',
                    'url': 'http://testserver/appointments/search/',
                    'title': 'Find Specialists',
                    'props': {'duration_ms': 4200, 'scroll_pct': 64, 'bogus_key': 'nope'},
                }),
                content_type='application/json',
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['ok'])
        body = captured['body']
        self.assertEqual(body['event'], '$pageleave')
        self.assertEqual(body['properties']['$session_id'], '019930b4-7c2a-7b3e-8a4f-1d2c3b4a5e6f')
        self.assertEqual(body['properties']['$window_id'], '019930b4-7c2a-7b3e-8a4f-1d2c3b4a5e6f')
        self.assertEqual(body['properties']['$prev_pageview_pathname'], '/appointments/search/')
        self.assertEqual(body['properties']['duration_ms'], 4200)
        self.assertEqual(body['properties']['scroll_pct'], 64)
        self.assertNotIn('bogus_key', body['properties'])

    def test_15_pulse_relay_rejects_unknown_events(self):
        """Only the three allow-listed events may be relayed; nothing else hits the network."""
        with override_settings(POSTHOG_API_KEY='phc_test_key'), \
                patch('core.views.requests.post') as mocked:
            res = self.client.post(
                '/pulse/',
                data=json.dumps({'event': '$identify', 'path': '/', 'props': {'email': 'x@y.z'}}),
                content_type='application/json',
            )
        self.assertEqual(res.status_code, 400)
        mocked.assert_not_called()

    def test_16_pulse_relay_never_touches_clinical_routes(self):
        """PHI routes are dropped before any outbound request is attempted."""
        with override_settings(POSTHOG_API_KEY='phc_test_key'), \
                patch('core.views.requests.post') as mocked:
            for path in ('/records/5/', '/payments/9/', '/notifications/mailer/compose/',
                         '/dashboard/', '/accounts/profile/', '/access/scanner/'):
                res = self.client.post(
                    '/pulse/',
                    data=json.dumps({'event': '$pageview', 'path': path, 'session_id': 'abc'}),
                    content_type='application/json',
                )
                self.assertEqual(res.status_code, 200, path)
                self.assertTrue(res.json().get('skipped'), path)
        mocked.assert_not_called()

    def test_17_pulse_relay_web_vitals_are_numeric_and_clamped(self):
        captured = {}

        class FakeResp:
            status_code = 200

        def fake_post(url, json=None, timeout=None):
            captured['body'] = json
            return FakeResp()

        with override_settings(POSTHOG_API_KEY='phc_test_key'), \
                patch('core.views.requests.post', side_effect=fake_post):
            self.client.post(
                '/pulse/',
                data=json.dumps({
                    'event': '$web_vitals',
                    'path': '/',
                    'session_id': '019930b4-7c2a-7b3e-8a4f-1d2c3b4a5e6f',
                    'props': {'lcp_ms': 1830, 'cls': 0.0421, 'inp_ms': 96, 'ttfb_ms': -5, 'lcp_note': 'x'},
                }),
                content_type='application/json',
            )
        props = captured['body']['properties']
        self.assertEqual(props['lcp_ms'], 1830)
        self.assertEqual(props['cls'], 0.0421)
        self.assertEqual(props['inp_ms'], 96)
        self.assertNotIn('ttfb_ms', props)      # negative value rejected
        self.assertNotIn('lcp_note', props)     # unknown key rejected

    def test_18_pulse_relay_is_rate_limited(self):
        """A flood of beacons is capped per IP (120/min)."""
        cache.clear()
        with override_settings(POSTHOG_API_KEY='phc_test_key'), \
                patch('core.views.requests.post', side_effect=lambda *a, **k: type('R', (), {'status_code': 200})()):
            codes = []
            for _ in range(125):
                r = self.client.post(
                    '/pulse/',
                    data=json.dumps({'event': '$pageview', 'path': '/'}),
                    content_type='application/json',
                    HTTP_ACCEPT='application/json',
                )
                codes.append(r.status_code)
        self.assertIn(429, codes, 'rate limiter never engaged on /pulse/')
        self.assertEqual(codes[0], 200)

    def test_19_notification_links_never_404(self):
        """Stored links that are not real routes must resolve to a safe fallback."""
        from notifications.models import Notification
        from notifications.views import safe_notification_link

        # A bare prefix (the old demo link) is not a route -> fallback, not 404
        self.assertEqual(safe_notification_link('/payments/', self.patient_a), '/payments/my/')
        self.assertEqual(safe_notification_link('/records/', self.patient_a), '/records/')
        self.assertEqual(safe_notification_link('/appointments/', self.patient_a), '/appointments/my/')
        # Garbage / empty / protocol-relative -> role dashboard
        self.assertEqual(safe_notification_link('/does/not/exist/', self.patient_a), '/dashboard/')
        self.assertEqual(safe_notification_link('', self.patient_a), '/dashboard/')
        self.assertEqual(safe_notification_link('//evil.example.com/x', self.patient_a), '/dashboard/')
        # Real routes survive unchanged
        self.assertEqual(safe_notification_link('/notifications/', self.patient_a), '/notifications/')

        # End to end: clicking a bad alert redirects to a 200 page, not the 404 template
        bad = Notification.objects.create(
            recipient=self.patient_a, notification_type='PAYMENT_VERIFIED',
            title='Old link', message='Should still work', link='/payments/',
        )
        self.client.login(username='pat_a', password='StrongPassword123!')
        res = self.client.post(f'/notifications/{bad.pk}/read/')
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.headers['Location'], '/payments/my/')
        landing = self.client.get(res.headers['Location'])
        self.assertEqual(landing.status_code, 200)

    def test_20_free_follow_up_booking_skips_payment(self):
        """A follow-up reuses the previous consultation, costs nothing and needs no receipt."""
        from appointments.models import Availability

        # 1. A completed first consultation with a SOAP record
        first = Appointment.objects.create(
            patient=self.patient_a, doctor=self.doc,
            appointment_date=self.today, appointment_time=datetime.time(10, 0),
            status='COMPLETED', payment_status='PAID', reason='Hypertension review',
            consultation_fee=1000,
        )
        record = MedicalRecord.objects.create(
            appointment=first, patient=self.patient_a, doctor=self.doc,
            chief_complaint='Headache', diagnosis='Stage 1 hypertension',
            clinical_advice='Low-salt diet', follow_up_date=self.today + datetime.timedelta(days=21),
        )
        # Doctor availability so slots exist inside the 24-hour follow-up window
        tomorrow = self.today + datetime.timedelta(days=1)
        Availability.objects.create(
            doctor=self.doc, day_of_week=tomorrow.weekday(),
            start_time=datetime.time(9, 0), end_time=datetime.time(12, 0),
            slot_duration=30, is_active=True,
        )

        self.client.login(username='pat_a', password='StrongPassword123!')

        # 2. The booking page recognises the follow-up and offers a free slot
        page = self.client.get(f'/appointments/book/{self.doc.pk}/?follow_up_of={first.pk}')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Free Follow-Up')
        self.assertNotContains(page, 'Proceed to Payment')

        # 3. Booking it creates a CONFIRMED, zero-fee, payment-exempt follow-up
        res = self.client.post(f'/appointments/book/{self.doc.pk}/', {
            'appointment_date': tomorrow.isoformat(),
            'appointment_time': '09:00',
            'reason': 'Blood pressure recheck after medication',
            'telemedicine_consent': 'on',
            'idempotency_key': 'followup-test-1',
            'follow_up_of': str(first.pk),
        })
        self.assertEqual(res.status_code, 302)
        follow_up = Appointment.objects.filter(patient=self.patient_a, visit_type='FOLLOW_UP').first()
        self.assertIsNotNone(follow_up, 'no follow-up appointment was created')
        self.assertEqual(follow_up.parent_appointment_id, first.pk)
        self.assertEqual(follow_up.status, 'CONFIRMED')
        self.assertEqual(follow_up.payment_status, 'NOT_REQUIRED')
        self.assertEqual(follow_up.consultation_fee, 0)
        self.assertTrue(follow_up.is_free_follow_up)
        self.assertTrue(follow_up.payment_exempt)
        self.assertEqual(follow_up.follow_up_reference, record.follow_up_date)

        # 4. The payment desk refuses to charge for it
        pay = self.client.get(f'/payments/submit/{follow_up.pk}/')
        self.assertEqual(pay.status_code, 302)
        self.assertFalse(Payment.objects.filter(appointment=follow_up).exists())

        # 5. Detail page shows continuity-of-care links
        detail = self.client.get(f'/appointments/{follow_up.pk}/')
        self.assertContains(detail, 'Free Follow-Up')
        self.assertContains(detail, f'/appointments/{first.pk}/')

    def test_21_follow_up_cannot_be_booked_with_someone_elses_consultation(self):
        """The ?follow_up_of parameter must be row-scoped to the requesting patient."""
        other = Appointment.objects.create(
            patient=self.patient_b, doctor=self.doc,
            appointment_date=self.today, appointment_time=datetime.time(11, 0),
            status='COMPLETED', payment_status='PAID', reason='Migraine', consultation_fee=1000,
        )
        self.client.login(username='pat_a', password='StrongPassword123!')
        page = self.client.get(f'/appointments/book/{self.doc.pk}/?follow_up_of={other.pk}')
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'Free Follow-Up')   # silently downgraded to a paid booking

    # ------------------------------------------------------------------
    # 22-24: mobile "Stat Stack" tables (Variant A)
    # Guards the three ways this silently rots: a template loses stack-mobile,
    # a cell loses its data-label, or the print/email sheets get swept in.
    # ------------------------------------------------------------------
    def test_22_every_onscreen_data_table_opts_into_mobile_stacking(self):
        """Any screen .data-table must carry stack-mobile, or it scrolls sideways again."""
        import pathlib, re
        from django.conf import settings
        root = pathlib.Path(settings.BASE_DIR)
        offenders = []
        for path in root.glob('templates/*/*.html'):
            if path.parent.name == 'emails':
                continue                      # Outlook cannot render cards
            text = path.read_text(encoding='utf-8')
            if 'a4-sheet' in text:
                continue                      # borderless print documents
            for cls in re.findall(r'<table[^>]*class="([^"]*data-table[^"]*)"', text):
                if 'stack-mobile' not in cls:
                    offenders.append(f'{path.name}: {cls}')
        self.assertEqual(offenders, [], f'tables not opted in: {offenders}')

    def test_23_stacked_tables_label_every_cell(self):
        """A stacked cell without data-label renders with a blank, confusing label."""
        import pathlib, re
        from django.conf import settings
        root = pathlib.Path(settings.BASE_DIR)
        offenders = []
        for path in root.glob('templates/*/*.html'):
            text = path.read_text(encoding='utf-8')
            if 'stack-mobile' not in text:
                continue
            for tbody in re.findall(r'<tbody>(.*?)</tbody>', text, re.S):
                for row in re.findall(r'<tr>(.*?)</tr>', tbody, re.S):
                    if 'colspan' in row:
                        continue              # empty-state row, no label needed
                    for td in re.findall(r'<td\b[^>]*>', row):
                        if 'data-label=' not in td:
                            offenders.append(f'{path.name}: {td[:60]}')
        self.assertEqual(offenders, [], f'cells without data-label: {offenders}')

    def test_24_print_and_email_tables_stay_plain(self):
        """The A4 receipt/prescription and every email template must not stack."""
        import pathlib
        from django.conf import settings
        root = pathlib.Path(settings.BASE_DIR)
        for name in ('payments/receipt.html', 'medical_records/print_rx.html'):
            text = (root / 'templates' / name).read_text(encoding='utf-8')
            self.assertNotIn('stack-mobile', text, f'{name} must stay a plain print table')
        for path in root.glob('templates/emails/*.html'):
            self.assertNotIn('stack-mobile', path.read_text(encoding='utf-8'),
                             f'{path.name}: email tables must stay tables')
