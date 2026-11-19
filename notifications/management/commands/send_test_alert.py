"""
Fire a real portal alert (popup + chime + e-mail) so you can watch the live
notification system work — no shell typing, no guessing usernames.

Examples
--------
    # Who am I? (lists usernames with roles)
    python manage.py send_test_alert --list

    # Ping the newest patient account (no e-mail side effects)
    python manage.py send_test_alert --latest-patient

    # Ping an exact account
    python manage.py send_test_alert --username patient_maliha

    # Ping every patient (what a broadcast feels like)
    python manage.py send_test_alert --all-patients

    # Include the branded Gmail/HTML e-mail as well
    python manage.py send_test_alert --username patient_maliha --email
"""
from django.core.management.base import BaseCommand, CommandError

from accounts.models import CustomUser
from notifications.models import create_notification
from notifications.views import safe_notification_link


DEMO_TITLE = 'Payment verified — Receipt QR active'
DEMO_BODY = (
    'Your bKash payment of ৳1200 for Dr. Jahed Rahman (Cardiology) is confirmed. '
    'Your reception QR entry pass is now valid for today.'
)


class Command(BaseCommand):
    help = 'Send a demo portal alert (popup + chime, optional e-mail) to one or more accounts.'

    def add_arguments(self, parser):
        parser.add_argument('--list', action='store_true', help='List accounts and exit.')
        parser.add_argument('--username', help='Target username exactly as stored.')
        parser.add_argument('--latest-patient', action='store_true', help='Target the most recently created PATIENT account.')
        parser.add_argument('--all-patients', action='store_true', help='Target every PATIENT account.')
        parser.add_argument('--title', default=DEMO_TITLE, help='Alert title.')
        parser.add_argument('--message', default=DEMO_BODY, help='Alert body text.')
        parser.add_argument('--link', default='', help='In-portal link (must start with /). Default: the target\'s newest receipt, else the inbox.')
        parser.add_argument('--email', action='store_true', help='Also dispatch the branded HTML e-mail via Gmail SMTP.')

    def handle(self, *args, **opts):
        if opts['list']:
            rows = CustomUser.objects.order_by('role', 'username').values_list('username', 'role', 'email')
            if not rows:
                self.stdout.write(self.style.WARNING('No accounts exist yet. Run: python manage.py seed_demo'))
                return
            self.stdout.write(self.style.MIGRATE_HEADING('\nAccounts on the current database:\n'))
            for username, role, email in rows:
                self.stdout.write(f'  {username:<22} {role:<9} {email}')
            self.stdout.write('\nSend one with:  python manage.py send_test_alert --username <name>\n')
            return

        targets = CustomUser.objects.none()
        if opts['username']:
            user = CustomUser.objects.filter(username=opts['username']).first()
            if not user:
                hint = ', '.join(
                    CustomUser.objects.order_by('-id').values_list('username', flat=True)[:8]
                )
                raise CommandError(
                    f'No account named "{opts["username"]}".\n'
                    f'Existing usernames include: {hint or "(none)"}\n'
                    'Run: python manage.py send_test_alert --list'
                )
            targets = CustomUser.objects.filter(pk=user.pk)
        elif opts['latest_patient']:
            user = (
                CustomUser.objects.filter(role='PATIENT').order_by('-id').first()
                or CustomUser.objects.order_by('-id').first()
            )
            if not user:
                raise CommandError('No accounts exist yet. Run: python manage.py seed_demo')
            targets = CustomUser.objects.filter(pk=user.pk)
        elif opts['all_patients']:
            targets = CustomUser.objects.filter(role='PATIENT')
            if not targets.exists():
                raise CommandError('No PATIENT accounts exist yet.')
        else:
            raise CommandError(
                'Pick a target: --username <name> | --latest-patient | --all-patients | --list'
            )

        sent = 0
        for user in targets:
            requested = (opts.get('link') or '').strip()
            raw_link = requested if requested.startswith('/') else self._best_link(user)
            # Final safety net: never store a link that does not resolve.
            link = safe_notification_link(raw_link, user)
            create_notification(
                user,
                'PAYMENT_VERIFIED',
                opts['title'],
                opts['message'],
                link,
                send_email=bool(opts['email']),
            )
            sent += 1
            self.stdout.write(f'  queued for {user.username} ({user.get_role_display()})')

        self.stdout.write(self.style.SUCCESS(f'\n{sent} alert(s) created.'))
        self.stdout.write(
            'Keep the portal tab open: the popup + chime appear within ~5 s on the next poll,\n'
            'or immediately when you switch back to the tab (visibilitychange triggers a poll).'
        )

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _best_link(user):
        """Point the demo alert at a page that actually exists for this user."""
        from payments.models import Payment

        payment = (
            Payment.objects.filter(patient=user).order_by('-created_at').first()
            or Payment.objects.filter(appointment__doctor=user).order_by('-created_at').first()
        )
        if payment:
            return f'/payments/{payment.pk}/receipt/'
        if user.role == 'ADMIN':
            return '/payments/queue/'
        if user.role == 'DOCTOR':
            return '/appointments/doctor/'
        return '/notifications/'
