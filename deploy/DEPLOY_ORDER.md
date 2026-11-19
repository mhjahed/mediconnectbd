# MediConnect — Deployment Order (GitHub → Oracle VM → Cloudflare)

Read this **before** pushing. Two of these steps are unforgiving if done out of order.

---

## 0. The order that works

```
1. Git init + first commit (verify secrets are NOT staged)
2. Push to a PRIVATE GitHub repo
3. Oracle Cloud Always Free VM  →  clone the repo, add .env, run deploy/oracle_ubuntu_setup.sh
4. Buy/point a domain  →  Cloudflare DNS (proxied, Full strict)
5. Cloudflare Worker at the edge  →  edge.yourdomain.com
6. Post-deploy verification checklist
```

---

## 1. GitHub — prepare the repo

`.gitignore` already excludes `.env`, `*.sqlite3`, `staticfiles/`, `media/`, `logs/`, `*.key`, `*.pem`.
`.env.example` holds **placeholders only** (confirmed: no `npg_`, `phc_`, `API_SECRET`, or app-password strings).

```powershell
cd "D:\CODING\CODE OP\mediconnect"

git init
git add -A

# ⚠️ MANDATORY GATE — must print nothing. If it prints a path, STOP and fix .gitignore.
git status --porcelain | Select-String -Pattern "\.env$|\.sqlite3|\.key$|\.pem$|staticfiles/|logs/"

git commit -m "MediConnect: Neon PostgreSQL + Cloudinary + PostHog relay + Oracle/Cloudflare deploy kit"
```

Create the remote as **Private** (github.com → New repository → *Private* → do **not** add a README):

```powershell
git branch -M main
git remote add origin https://github.com/<your-username>/mediconnect.git
git push -u origin main
```

> If you already committed `.env` by mistake at any point, rotating every secret is mandatory —
> deleting the file in a later commit does **not** remove it from git history.

---

## 2. Oracle VM (the origin server)

```powershell
scp -i "$HOME\Downloads\ssh-key-2026.key" -r .\mediconnect ubuntu@<VM_IP>:~/mediconnect
ssh -i "$HOME\Downloads\ssh-key-2026.key" ubuntu@<VM_IP>
```

On the VM, create `.env` (copy values from your local `.env`, then change these):

```dotenv
DEBUG=False
ALLOWED_HOSTS=mediconnect.yourdomain.com,<VM_PUBLIC_IP>
CSRF_TRUSTED_ORIGINS=https://mediconnect.yourdomain.com
SITE_URL=https://mediconnect.yourdomain.com
SECURE_SSL_REDIRECT=True
HIDE_ROUTE_LIST_AFTER=2026-01-01
```

```bash
cd ~/mediconnect
bash deploy/oracle_ubuntu_setup.sh mediconnect.yourdomain.com
.venv/bin/python manage.py check --deploy      # expect 0 issues
```

Git-based workflow for later updates (nicer than scp):

```bash
# on the VM, once:
git clone https://github.com/<you>/mediconnect.git ~/mediconnect-src
cp ~/mediconnect/.env ~/mediconnect-src/.env

# forever after, from your PC:  git push
# then on the VM:
cd ~/mediconnect-src && git pull && bash deploy/redeploy.sh
```

---

## 3. Cloudflare DNS + Worker (the edge)

1. `dash.cloudflare.com` → **Add a site** → Free → point the registrar's nameservers at Cloudflare.
2. **DNS → A record** `mediconnect` → `<VM_IP>`, **Proxied (orange cloud)**.
3. **SSL/TLS → Overview:** **Full (strict)**. **Edge Certificates:** Always Use HTTPS + HSTS.
4. **Compute (Workers) → Create → paste `deploy/cloudflare-worker.js`** → set variable
   `ORIGIN_URL = https://mediconnect.yourdomain.com` → **Deploy**.
5. **Worker → Settings → Domains & Routes → Add custom domain:** `edge.yourdomain.com`.
6. Smoke test:
   ```bash
   curl -I https://edge.yourdomain.com/                        # 200, X-MediConnect-Edge: DYNAMIC
   curl -I https://edge.yourdomain.com/static/css/mediconnect.css   # MISS then HIT
   curl -I https://edge.yourdomain.com/.env                    # 404 straight from the edge
   curl -I https://edge.yourdomain.com/edge-health             # {"ok":true,...}
   ```

⚠️ **Free-plan note:** Workers' 100,000 requests/day is generous for a clinic portal. But Cloudflare's
free **Cache/SSL** works fine for any domain; if you want *unlimited* edge caching without a Worker,
you can skip step 4 and just proxy the A record — you lose the edge bot-filtering and `/static` pinning.
Also: **do not** create a "Cache Everything" rule — HTML carries CSRF tokens; the Worker only caches
fingerprinted static assets.

7. Add `https://edge.yourdomain.com` (and the apex domain) to **PostHog → Settings → Authorized Domains**.

---

## 4. Post-deploy verification (all must pass)

```bash
cd ~/mediconnect
.venv/bin/python manage.py check_db        # Neon 18.7, 27 tables
.venv/bin/python manage.py check_storage   # Cloudinary upload/CDN/delete
.venv/bin/python manage.py check --deploy  # 0 issues
```

- [ ] `DEBUG=False`, fresh 50+ char `SECRET_KEY`, `ALLOWED_HOSTS` contains no `*`
- [ ] `/.env`, `/.git/config`, `/wp-admin`, `/admin/`, `/django-admin/` → 404 **from the edge**
- [ ] `/dawd` → branded 404, **no urlconf dump** (`HIDE_ROUTE_LIST_AFTER` set)
- [ ] `/mc-registry-vault/` → admin loads; page source contains no `django-admin`
- [ ] Login as patient, book, pay, print receipt; login as staff, door scanner opens
- [ ] Patient account on `/access/scanner/` → **403**
- [ ] Cloudinary portrait loads over `https://res.cloudinary.com/...`
- [ ] Alerts: `python manage.py send_test_alert --latest-patient` → popup + chime + red badge
- [ ] PostHog Activity shows live pageviews; Brave Shields ON still works (first-party `/pulse/`)
- [ ] Gmail SMTP: Mailer Console → send test → mail arrives
- [ ] `sudo reboot` → site returns in <60 s (systemd restart proof)

---

## 5. Secrets hygiene, permanently

| Secret | Where it lives | Never |
|---|---|---|
| `.env` | VM filesystem + your PC | in git, in a screenshot, in chat |
| Neon `DATABASE_URL` | `.env` | in `settings.py` |
| Cloudinary URL | `.env` | in templates |
| Gmail app password | `.env` (or portal SMTP Settings page, encrypted) | in git |
| PostHog project key | `.env` (public-ish, but keep it tidy) | — |
