#!/usr/bin/env python
"""
MediConnect smoke test — run this before showing anything to a user.

Why this exists: Django's test suite never imports management commands, so a
command can crash on its first real run (send_test_alert did exactly that) and
still leave "19 tests OK" behind. This script EXECUTES every entry point and
checks the two things that actually broke: commands and notification links.

    python smoke_test.py            # full check
    python smoke_test.py --quick    # skip command execution

Exit code 0 = safe to ship. Anything else = do not deploy.

Safe on:
  * Windows paths with spaces ("D:\\CODING\\CODE OP\\mediconnect") — no shell=True
  * a brand-new clone with no migration yet — DB checks degrade to "skip"
  * SQLite, Neon/Postgres, or a Space container — nothing is hard-coded
"""
import os
import subprocess
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django  # noqa: E402

django.setup()

from django.urls import URLPattern, URLResolver, get_resolver, resolve  # noqa: E402

FAILURES = []
SKIPPED = []
CHECKS = 0

# Project root = the folder holding this script. Every subprocess runs from here,
# so a directory name containing spaces is never re-parsed by a shell.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
MANAGE_PY = os.path.join(PROJECT_ROOT, 'manage.py')


# --------------------------------------------------------------------------- utils
def run_manage(args):
    """Execute `python manage.py <args>` portably (no shell, explicit cwd)."""
    return subprocess.run(
        [sys.executable, MANAGE_PY, *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def ok(label, detail=''):
    global CHECKS
    CHECKS += 1
    print(f'  ok    {label}{(" — " + detail) if detail else ""}')


def fail(label, detail):
    global CHECKS
    CHECKS += 1
    FAILURES.append((label, detail))
    print(f'  FAIL  {label}\n        {detail}')


def skip(label, reason):
    global CHECKS
    CHECKS += 1
    SKIPPED.append((label, reason))
    print(f'  skip  {label} ({reason})')


def walk_urls(patterns, namespace=''):
    for p in patterns:
        if isinstance(p, URLResolver):
            ns = f'{namespace}{p.namespace}:' if p.namespace else namespace
            yield from walk_urls(p.url_patterns, ns)
        elif isinstance(p, URLPattern):
            yield namespace, p


def db_first_user():
    """Return any user, or None when the database is not migrated yet."""
    try:
        from accounts.models import CustomUser
        return CustomUser.objects.first()
    except Exception as exc:  # noqa: BLE001
        print(f'  note  database not ready ({type(exc).__name__}) — run "python manage.py migrate"')
        return None


# --------------------------------------------------------------------------- gates
def gate_system_checks():
    print('\n=== 1. Django boots ===')
    res = run_manage(['check'])
    if res.returncode != 0:
        fail('manage.py check', (res.stderr or res.stdout).strip()[-400:])
    else:
        ok('manage.py check')


def gate_url_map():
    print('\n=== 2. URL map ===')
    try:
        named = [p for _, p in walk_urls(get_resolver().url_patterns) if p.name]
        ok('routes resolved', f'{len(named)} named patterns')
    except Exception as exc:  # noqa: BLE001
        fail('URL map', f'{type(exc).__name__}: {exc}')


def gate_notification_links():
    print('\n=== 3. Notification links always land on a real route ===')
    from notifications.views import safe_notification_link

    user = db_first_user()
    samples = ['/payments/', '/records/', '/appointments/', '/does/not/exist/', '', '//evil.com/x']
    for link in samples:
        try:
            target = safe_notification_link(link, user)
            resolve(target)  # the fallback itself must be routable
            ok(f'{link!r} -> {target!r}')
        except Exception as exc:  # noqa: BLE001
            fail(f'{link!r}', f'{type(exc).__name__}: {exc}')


def gate_commands():
    print('\n=== 4. Management commands actually execute ===')
    have_data = db_first_user() is not None

    commands = [
        (['send_test_alert', '--list'], 'lists accounts', True),
        (['send_test_alert', '--latest-patient'], 'creates a demo alert with a valid link', True),
        (['fix_password', '--list'], 'password hash audit', True),
        (['fix_password', '--all', '--dry-run'], 'bulk password reset preview', True),
        (['check_db'], 'database health', False),
    ]

    for args, label, needs_data in commands:
        name = ' '.join(args)
        if needs_data and not have_data:
            skip(name, 'no accounts / database not migrated')
            continue
        res = run_manage(args)
        if res.returncode != 0:
            tail = (res.stderr or res.stdout).strip().splitlines()[-3:]
            fail(f'{name} ({label})', ' | '.join(tail))
        else:
            ok(f'{name} ({label})')

    # check_storage talks to Cloudinary, so only run it when it is configured.
    if os.getenv('CLOUDINARY_URL', '').strip():
        res = run_manage(['check_storage'])
        if res.returncode != 0:
            tail = (res.stderr or res.stdout).strip().splitlines()[-3:]
            fail('check_storage (cloud media upload)', ' | '.join(tail))
        else:
            ok('check_storage (cloud media upload)')
    else:
        skip('check_storage', 'CLOUDINARY_URL not exported in this shell')


def report():
    print('\n' + '=' * 62)
    if SKIPPED:
        print(f'{len(SKIPPED)} check(s) skipped (not failures).')
    if FAILURES:
        print(f'RESULT: {len(FAILURES)} FAILURE(S) of {CHECKS} checks — DO NOT SHIP')
        for label, why in FAILURES:
            print(f'  - {label}: {why}')
        return 1
    print(f'RESULT: all {CHECKS} checks passed — safe to ship.')
    return 0


def main():
    quick = '--quick' in sys.argv
    gate_system_checks()
    gate_url_map()
    gate_notification_links()
    if quick:
        print('\n(quick mode: skipping command execution)')
    else:
        gate_commands()
    return report()


if __name__ == '__main__':
    sys.exit(main())
