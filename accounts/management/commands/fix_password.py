"""
Repair a login you can no longer get into — no Django admin required.

Why "1234" fails even though you typed it in:
    The account may have been created through a form/shell that never hashed the
    value, so the stored password is literally "1234" instead of "pbkdf2_sha256$...".
    Django compares hashes, so it can never match. This command re-hashes it.

Examples
--------
    python manage.py fix_password --list                     # all accounts + password-hash health
    python manage.py fix_password jahed --password "1234"    # repair that one account
    python manage.py fix_password jahed --password "1234" --make-superuser
    python manage.py fix_password jahed                      # interactive prompt (hidden input)

Bulk reset (the "everything is stale after a DB copy" case)
----------------------------------------------------------
    python manage.py fix_password --all --dry-run            # preview, changes nothing
    python manage.py fix_password --all                      # role-based demo passwords
    python manage.py fix_password --all --password "OnePass123!"
    python manage.py fix_password --all --role PATIENT       # limit to one role

`--all` writes the README passwords per role:
    ADMIN -> AdminPass2026!     DOCTOR -> DoctorPass2026!
    NURSE/STAFF -> StaffPass2026!   PATIENT -> PatientPass2026!
"""
import getpass

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError

from accounts.models import CustomUser

STRONG_HINT = (
    'Tip: "1234" is fine for local development. The moment this app is public, change it to '
    'something 12+ characters — /accounts/login/ is rate limited to 15 attempts per minute per IP.'
)


class Command(BaseCommand):
    help = 'Reset the password for an existing account (works even if the stored value was never hashed).'

    def add_arguments(self, parser):
        parser.add_argument('username', nargs='?', help='Account username to repair.')
        parser.add_argument('--password', help='New plain-text password to set.')
        parser.add_argument('--list', action='store_true', help='List accounts and whether their hash looks valid.')
        parser.add_argument('--make-superuser', action='store_true', help='Also grant is_superuser + is_staff + role=ADMIN.')
        parser.add_argument('--debug-report', action='store_true',
                            help='Print every possible reason a login could be refused for this account.')
        parser.add_argument('--all', action='store_true',
                            help='Reset passwords for EVERY account (role-based demo passwords unless --password).')
        parser.add_argument('--role', choices=['ADMIN', 'DOCTOR', 'NURSE', 'STAFF', 'PATIENT'],
                            help='With --all: limit the reset to one role.')
        parser.add_argument('--dry-run', action='store_true',
                            help='With --all: show what would change without writing.')

    def handle(self, *args, **opts):
        if opts['list']:
            self._list_accounts()
            return

        if opts['all']:
            self._reset_all(opts)
            return

        username = (opts.get('username') or '').strip()
        if not username:
            raise CommandError('Give a username, e.g.:  python manage.py fix_password jahed --password "1234"')

        user = CustomUser.objects.filter(username__iexact=username).first()
        if not user:
            hint = ', '.join(CustomUser.objects.order_by('-id').values_list('username', flat=True)[:10])
            raise CommandError(
                f'No account named "{username}".\nExisting usernames: {hint or "(none)"}\n'
                'Create it instead:  python manage.py createsuperuser'
            )

        if opts['debug_report']:
            self._debug_report(user)
            return

        raw_password = opts.get('password')
        if not raw_password:
            raw_password = getpass.getpass(f'New password for {user.username}: ')
            confirm = getpass.getpass('Repeat it: ')
            if raw_password != confirm:
                raise CommandError('Passwords did not match. Nothing changed.')

        old_hash_kind = self._hash_kind(user.password)

        user.password = make_password(raw_password)
        update_fields = ['password']
        if opts['make_superuser']:
            user.is_superuser = True
            user.is_staff = True
            user.role = 'ADMIN'
            update_fields += ['is_superuser', 'is_staff', 'role']
        if not user.is_active:
            user.is_active = True
            update_fields.append('is_active')
        user.save(update_fields=update_fields)

        self.stdout.write(self.style.SUCCESS(
            f'\nPassword reset for "{user.username}" '
            f'(was {old_hash_kind} -> now a fresh {self._hash_kind(user.password)}).'
        ))
        self.stdout.write(f'  role          : {user.role}   superuser: {user.is_superuser}   staff: {user.is_staff}')
        self.stdout.write(f'  is_active     : {user.is_active}')
        self.stdout.write(f'  email         : {user.email or "(empty — password reset by e-mail will not work)"}')

        # Prove it right now, using Django's own authentication backend.
        from django.contrib.auth import authenticate

        verified = authenticate(username=user.username, password=raw_password)
        if verified is None:
            self.stdout.write(self.style.ERROR(
                '\n!! authenticate() still failed. Run with --debug-report to see why.'
            ))
            return
        self.stdout.write(self.style.SUCCESS('  verified      : authenticate() accepted the new password\n'))
        self.stdout.write(
            'Sign in at:  http://localhost:8000/accounts/login/\n'
            'Admin   at:  http://localhost:8000/mc-registry-vault/  (only if is_staff/is_superuser)\n'
        )
        self.stdout.write(STRONG_HINT)

    # -------------------------------------------------------------- bulk reset
    ROLE_PASSWORDS = {
        'ADMIN': 'AdminPass2026!',
        'DOCTOR': 'DoctorPass2026!',
        'NURSE': 'StaffPass2026!',
        'STAFF': 'StaffPass2026!',
        'PATIENT': 'PatientPass2026!',
    }

    def _reset_all(self, opts):
        """Reset every account's password (the fix for a stale/duplicated database)."""
        from django.contrib.auth import authenticate

        qs = CustomUser.objects.all().order_by('role', 'username')
        if opts.get('role'):
            qs = qs.filter(role=opts['role'])

        rows = list(qs)
        if not rows:
            self.stdout.write(self.style.WARNING('No accounts found — run seed_demo or register a user first.'))
            return

        fixed_password = opts.get('password')
        dry = opts['dry_run']

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n{"Preview" if dry else "Reset"} — {len(rows)} account(s)'
            f'{" (role: " + opts["role"] + ")" if opts.get("role") else ""}\n'
        ))
        self.stdout.write(f'  {"username":<22} {"role":<9} {"password":<18} flags')
        self.stdout.write('  ' + '-' * 62)

        changed = 0
        failures = 0
        for user in rows:
            new_password = fixed_password or self.ROLE_PASSWORDS.get(user.role, 'PatientPass2026!')
            flags = []
            if user.is_superuser:
                flags.append('superuser')
            elif user.is_staff:
                flags.append('staff')
            if not user.is_active:
                flags.append('INACTIVE')

            self.stdout.write(f'  {user.username:<22} {user.role:<9} {new_password:<18} {" ".join(flags)}')

            if dry:
                continue

            user.password = make_password(new_password)
            update_fields = ['password']
            if not user.is_active:
                user.is_active = True
                update_fields.append('is_active')
            user.save(update_fields=update_fields)
            changed += 1

            if authenticate(username=user.username, password=new_password) is None:
                failures += 1
                self.stdout.write(self.style.ERROR(f'      !! authenticate() failed for {user.username}'))

        if dry:
            self.stdout.write(self.style.WARNING('\nDry run — nothing written. Re-run without --dry-run.'))
            return

        self.stdout.write(self.style.SUCCESS(f'\n{changed} password(s) reset, {failures} auth failure(s).'))
        self.stdout.write('\nSign in at  : http://localhost:8000/accounts/login/')
        self.stdout.write('Django admin: http://localhost:8000/mc-registry-vault/  (ADMIN role only)')
        self.stdout.write(self.style.WARNING(
            '\nSecurity: these are README demo passwords. Change them before this app is public.\n'
            'Set a single strong password for everyone instead: --all --password "…"'
        ))

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _hash_kind(stored):
        if not stored:
            return 'EMPTY password field'
        if stored.startswith('pbkdf2_') or stored.startswith('argon2') or stored.startswith('bcrypt'):
            return 'hashed password'
        if stored.startswith('!') or stored.startswith('*'):
            return 'UNUSABLE/locked password'
        return f'PLAIN TEXT password ("{stored}") — this is why login fails'

    def _list_accounts(self):
        try:
            rows = CustomUser.objects.order_by('role', 'username')
        except Exception as exc:  # database not migrated yet
            self.stdout.write(self.style.WARNING(
                f'Database not ready ({type(exc).__name__}). Run: python manage.py migrate'
            ))
            return
        if not rows:
            self.stdout.write(self.style.WARNING('No accounts yet. Seed demo data: python manage.py seed_demo'))
            return
        self.stdout.write(self.style.MIGRATE_HEADING('\nAccounts and password-hash health:\n'))
        for u in rows:
            flag = '' if self._hash_kind(u.password) == 'hashed password' else '  <-- NEEDS FIX'
            self.stdout.write(
                f'  {u.username:<22} {u.role:<9} active={str(u.is_active):<5} '
                f'super={str(u.is_superuser):<5} {self._hash_kind(u.password)}{flag}'
            )
        self.stdout.write('\nRepair one with:  python manage.py fix_password <username> --password "newpass"\n')

    def _debug_report(self, user):
        from django.contrib.auth.hashers import identify_hasher

        self.stdout.write(self.style.MIGRATE_HEADING(f'\nLogin diagnostic for "{user.username}"\n'))
        self.stdout.write(f'  is_active     : {user.is_active}   (must be True)')
        self.stdout.write(f'  is_staff      : {user.is_staff}')
        self.stdout.write(f'  is_superuser  : {user.is_superuser}')
        self.stdout.write(f'  role          : {user.role}')
        self.stdout.write(f'  password hash : {self._hash_kind(user.password)}')
        try:
            identify_hasher(user.password)
            self.stdout.write('  hash format   : recognised by Django')
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'  hash format   : NOT recognised ({exc})'))
            self.stdout.write(self.style.WARNING(
                '  => the stored value was never hashed; reset it with --password.'
            ))
        self.stdout.write(f'  last_login    : {user.last_login or "(never)"}')
        self.stdout.write('\n  Common non-password causes of "invalid credentials":')
        self.stdout.write('    - typing the e-mail into the username box for an account with no matching email')
        self.stdout.write('    - a stale browser autofill overwriting the password field')
        self.stdout.write('    - rate limiting after 15 failed attempts/minute (wait 60 s)')
        self.stdout.write('    - running the server against a different database than you think\n')
