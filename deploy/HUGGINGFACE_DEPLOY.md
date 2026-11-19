# MediConnect — Deploy on Hugging Face Spaces (Docker)

**Cost: $0 · No card · No VM · No SSH · No Nginx · No Certbot · No DuckDNS**

HF terminates TLS for you at `https://<user>-<space>.hf.space`, injects your secrets as environment
variables, and builds straight from GitHub. That replaces steps 2–6 of the old Oracle runbook entirely.

---

## What changes vs. the Oracle path

| Oracle VM | Hugging Face Spaces |
|---|---|
| `scp` the code, `ssh` in | push to GitHub, HF builds the image |
| Nginx reverse proxy | not needed (HF proxies) |
| Certbot + DuckDNS | not needed (HF gives HTTPS on `*.hf.space`) |
| `.env` file on disk | **Space → Settings → Variables and secrets** |
| `systemctl status mediconnect` | **Logs** tab |
| port 8000 | **port 7860** (fixed by HF, already in the Dockerfile) |
| `deploy/oracle_ubuntu_setup.sh` | **not used at all** |
| always on, 1–6 GB RAM | free CPU: 2 vCPU / 16 GB RAM, but **sleeps after inactivity** |

⚠️ **The sleep caveat, stated honestly:** a free Space goes to sleep after a period with no HTTP
traffic and takes ~30–60 s to wake. For a demo/portfolio build that is fine. If you later need
always-on, either add a free uptime pinger (UptimeRobot, 5-min checks — keeps it awake and also gives
you an uptime record) or switch providers later without changing any code.

---

## Step 1 — Push to GitHub (private repo)

```powershell
cd "D:\CODING\CODE OP\mediconnect"
git init
git add -A

# MANDATORY GATE — must print nothing
git status --porcelain | Select-String -Pattern "\.env$|\.sqlite3|\.key$|\.pem$|staticfiles/|logs/"

git commit -m "MediConnect + Hugging Face Spaces Docker deployment"
git branch -M main
git remote add origin https://github.com/<you>/mediconnect.git
git push -u origin main
```

Files that make the Space work are already in the repo root:

| File | Purpose |
|---|---|
| `Dockerfile` | Python 3.12, deps, `collectstatic`, Gunicorn on **7860**, non-root user |
| `.dockerignore` | keeps `.env`, `db.sqlite3`, `.git`, `media/`, logs out of the image |
| `README.md` | Space metadata is at the **top** of your existing README (`sdk: docker`, `app_port: 7860`). Do **not** create a `HF_README.md` — Hugging Face only reads the file literally named `README.md`. |

---

## Step 2 — Create the Space

1. **https://huggingface.co/join** — sign up (email + password, **no card**).
2. **https://huggingface.co/new-space**
   - **Space name:** `mediconnect`
   - **License:** MIT (or "Other")
   - **SDK:** **Docker** → **Blank**
   - **Hardware:** **CPU basic · 2 vCPU · 16 GB · FREE**
   - **Visibility:** **Public** (private Spaces need a paid plan — so the code becomes readable by
     anyone; make sure no secrets are in the repo, which the `git status` gate guarantees).
3. HF creates an empty Space repo. Now connect your GitHub project:
   - Easiest reliable method (no CLI): **clone the empty Space repo, copy the project in, push.**

```powershell
cd "$HOME\Desktop"
git clone https://huggingface.co/spaces/<you>/mediconnect hf-space
# copy your project files in (PowerShell)
robocopy "D:\CODING\CODE OP\mediconnect" "hf-space" /E /XD .git .venv __pycache__ staticfiles media logs /XF .env db.sqlite3
cd hf-space
git add -A
git commit -m "MediConnect Django app"
git push
```

When git asks for a password, use a **HF access token** (Settings → Access Tokens → *Write*) — not
your account password.

> The Space repo's `README.md` **must** contain the YAML front matter block shown at the top of this
> project's README (`sdk: docker`, `app_port: 7860`). Without it HF treats the Space as a plain app and
> the build fails. That is why the metadata lives in `README.md` and not in a separate file.

---

## Step 3 — Add the secrets (replaces `.env`)

Space → **Settings → Variables and secrets → New secret**. Add each one as **Secret** (not Variable):

| Name | Value |
|---|---|
| `SECRET_KEY` | **new** 50+ random characters (do not reuse your dev key) |
| `DEBUG` | `False` |
| `DATABASE_URL` | your Neon pooled string (`...-pooler...sslmode=require`) |
| `CLOUDINARY_URL` | `cloudinary://KEY:SECRET@CLOUD` |
| `EMAIL_HOST_USER` | `infogicuofficial@gmail.com` |
| `EMAIL_HOST_PASSWORD` | the 16-character Gmail app password |
| `DEFAULT_FROM_EMAIL` | `MediConnect Clinical Registry <infogicuofficial@gmail.com>` |
| `SUPPORT_CONTACT_EMAIL` | `infogicuofficial@gmail.com` |
| `POSTHOG_API_KEY` | `phc_...` |
| `POSTHOG_HOST` | `https://us.i.posthog.com` |
| `ALLOWED_HOSTS` | `<you>-mediconnect.hf.space` |
| `CSRF_TRUSTED_ORIGINS` | `https://<you>-mediconnect.hf.space` |
| `SITE_URL` | `https://<you>-mediconnect.hf.space` |
| `SECURE_SSL_REDIRECT` | `True` |
| `HIDE_ROUTE_LIST_AFTER` | `2026-01-01` |
| `ADMIN_URL_PATH` | `django-admin/` |
| `ADMIN_CAMO_PREFIX` | `mc-registry-vault/` |

`ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` already default to accepting `*.hf.space`, so even if you
skip them the Space will boot — but set them explicitly anyway; it's cheaper than debugging CSRF 403s.

---

## Step 4 — First boot & migrate

The container starts, but the **database migrations are not run automatically** (deliberate — a build
step should never touch production data). Run them once from the Space's terminal:

Free CPU Spaces have **no interactive terminal**. Two options:

- **Simplest (recommended):** run migrations **from your Windows PC against Neon** — your `.env`
  already points there, so this is the live database:


```powershell
cd "D:\CODING\CODE OP\mediconnect"
# .env already points at Neon, so this migrates the live DB
python manage.py migrate
python manage.py check_db
python manage.py check_storage
```

That's it — the Space container needs no migration step because the DB is external (Neon).

If you prefer doing it from the Space shell:
```bash
python manage.py migrate && python manage.py check
```

---

## Step 5 — Verify

Open `https://<you>-mediconnect.hf.space` and check:

- [ ] Home page renders with CSS/fonts (Whitenoise serving `/static/`)
- [ ] Log in with your Neon account (`fix_password` / `send_test_alert` still work from your PC)
- [ ] Doctor portraits load from `res.cloudinary.com`
- [ ] `/dawd` → branded 404, **no urlconf dump**
- [ ] `/mc-registry-vault/` → admin; `/admin/` and `/django-admin/` → 404
- [ ] Patient account → `/access/scanner/` returns **403**
- [ ] `python manage.py send_test_alert --latest-patient` → popup + chime + red bell badge
- [ ] Mailer Console → send test → Gmail arrives
- [ ] PostHog Activity shows the `hf.space` pageviews

---

## Step 6 — Optional Cloudflare Worker in front (still $0, no card)

Now that the origin has real HTTPS, the Worker works exactly as written:

1. **dash.cloudflare.com** → Workers → Create → paste `deploy/cloudflare-worker.js` → Deploy
2. **Settings → Variables:** `ORIGIN_URL` = `https://<you>-mediconnect.hf.space`
3. Visitor URL: `https://mediconnect-edge.<you>.workers.dev`

Gain: 30-day immutable edge cache for `/static` (helps a lot, since a cold Space wake is slow),
edge-level 404s for `/.env` / `/.git` / `/wp-admin`, real-client IPs, and a friendly 503 page while
the Space is waking up instead of HF's default error.

```bash
curl -I https://mediconnect-edge.<you>.workers.dev/static/css/mediconnect.css   # MISS → HIT
curl -I https://mediconnect-edge.<you>.workers.dev/.env                         # 404 at edge
```

Then add the workers.dev host to **PostHog → Authorized Domains**.

---

## Troubleshooting map

| Symptom | Cause | Fix |
|---|---|---|
| Build fails: `permission denied` writing static | non-root user + wrong order | already handled: `/app` chowned in the Dockerfile |
| Space builds, then "Runtime error" | missing `SECRET_KEY` or bad `DATABASE_URL` | Logs tab → fix the secret |
| `DisallowedHost` | `ALLOWED_HOSTS` missing the hf.space host | set it explicitly |
| `CSRF verification failed` on POST | `CSRF_TRUSTED_ORIGINS` missing `https://<space>.hf.space` | set it |
| 502 / slow first load | free Space was asleep | wait ~60 s, or add an UptimeRobot ping |
| Static files 404 | `collectstatic` didn't run at build | check the build log for the `collectstatic` line |
| DB errors on boot | Neon woke up / connection limit | Neon free tier handles this; `conn_max_age=300` already set |
