---
title: MediConnect Clinical Registry
emoji: 🏥
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---
# MediConnect

Outpatient telemedicine scheduling, regional mobile-banking payment verification (`bKash`, `Nagad`, `Rocket`, `Upay`, `Bank Transfer`), printable A4 SOAP prescriptions, and room-scoped Code-128 hospital ward access control.

## Design & Architecture

- **Palette #06 + Crisp White Body**: Brushed Silver (`#D9DADC`) neumorphic header & footer chrome, clean clinical White (`#FFFFFF`) body canvas, Dark Slate (`#282D31`) typography, and Warm Yellow (`#FFC93C`) primary call-to-action buttons (`Liberation Serif` headings).
- **8 Modular Django Apps**: `core`, `accounts`, `dashboard`, `appointments`, `payments`, `medical_records`, `notifications`, `access_control`.
- **Cloud-Ready Stack**: Neon PostgreSQL (`DATABASE_URL` with SQLite local fallback), Resend HTTP email dispatch (`RESEND_API_KEY` with console/SMTP fallback), WhiteNoise compressed static serving, and Render `render.yaml` + `build.sh`.

## Quick Start

```bash
pip install -r requirements.txt
py manage.py migrate
py manage.py seed_demo
py manage.py runserver 0.0.0.0:8000
```

## Accounts, passwords & the Django admin

> **Read this first.** The table below only exists if you ran `python manage.py seed_demo`.
> If you populated the database with `sync_local_data` (your own local data), your usernames
> are the ones from your own database — see what you actually have with:

```powershell
python manage.py fix_password --list      # every account: role, active, hash health
python manage.py fix_password --all --dry-run   # what a bulk reset would change
```

**Django admin** lives at the *camouflaged* URL, not `/admin/`:

| URL                               | Result                                                         |
| --------------------------------- | -------------------------------------------------------------- |
| `/mc-registry-vault/`           | ✅ admin login (the only working path)                         |
| `/admin/` · `/django-admin/` | ❌ 404 by design (`core.middleware.AdminPathCamoMiddleware`) |

Admin access requires `is_staff` **and** `is_superuser`:

```powershell
python manage.py fix_password jahed --password "YourStrongPass" --make-superuser
```

### Bulk password reset (after copying a database, or if everyone's login is stale)

```powershell
python manage.py fix_password --all --dry-run                    # preview, changes nothing
python manage.py fix_password --all                              # README passwords per role
python manage.py fix_password --all --password "OnePass123!"      # one password for everyone
python manage.py fix_password --all --role PATIENT --password "…" # limit to one role
```

`--all` restores these per-role defaults, then **verifies every login** with `authenticate()`:
`ADMIN → AdminPass2026!` · `DOCTOR → DoctorPass2026!` · `NURSE`/`STAFF → StaffPass2026!` · `PATIENT → PatientPass2026!`

## Demo Credentials (Seeded by `seed_demo`)

| Role            | Username           | Password             | Notes                                                   |
| --------------- | ------------------ | -------------------- | ------------------------------------------------------- |
| Hospital Admin  | `admin`          | `AdminPass2026!`   | Finance queue, key generator, 22-zone badge control     |
| Cardiologist    | `dr_rahman`      | `DoctorPass2026!`  | Checked in, paid docket, SOAP Rx author, Code-128 badge |
| Neurologist     | `dr_nasrin`      | `DoctorPass2026!`  | Checked in, upcoming Google Meet consultation           |
| ICU Nurse       | `nurse_afsana`   | `StaffPass2026!`   | ICU/CCU/Pharmacy badge clearance                        |
| Reception Staff | `staff_rafiq`    | `StaffPass2026!`   | Ground-floor reception badge (denied at OT rooms)       |
| Patient         | `patient_tanvir` | `PatientPass2026!` | Completed Rx-1 + upcoming paid consultation             |
| Patient         | `patient_maliha` | `PatientPass2026!` | Pending Rocket payment in Finance queue                 |
