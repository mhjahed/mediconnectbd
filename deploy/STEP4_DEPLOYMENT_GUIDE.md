# MediConnect — STEP 4 Deployment Guide
## Oracle Cloud Always Free VM (Django origin) + Cloudflare Workers Edge (100K req/day)

**Cost: $0.00/month · No Render · No 30-day sleep · No credit card at runtime**

> ⚠️ **One honest heads-up first:** Oracle Cloud's *signup* asks for a card for identity verification
> (a $0/₹1 temporary authorization hold — it is never charged on the Always Free tier). Cloudflare Workers
> asks for **nothing**. If Oracle rejects you without a card, the same `deploy/oracle_ubuntu_setup.sh` script
> runs unchanged on any Ubuntu 22.04/24.04 machine — Koyeb (no card), PythonAnywhere (no card), or a
> colleague's spare box. The Cloudflare Worker doesn't care who hosts the origin.

---

## Part 0 — Why this beats Render

| | Render Free | Oracle Always Free | Cloudflare Workers Free |
|---|---|---|---|
| Sleeps after inactivity | **Yes (15 min / 30-day cap)** | No — 24/7 | N/A (edge) |
| RAM | 512 MB shared | 1 GB (AMD) or 24 GB (ARM A1, 4 OCPU) | N/A |
| Card required | No | At signup only | **No** |
| Requests/day | Limited | Unmetered | **100,000/day** |

Your Neon DB stays in Singapore, so pick an Oracle **ap-singapore-1** region for ~3 ms query latency.

---

## Part 1 — Create the Oracle VM (15 min)

1. Sign up at `https://signup.cloud.oracle.com` → country **Bangladesh** → verify email/phone.
   *If a card is demanded and you can't provide one, skip to Part 1-B.*
2. Console → **Compute → Instances → Create instance**
   - **Image:** Canonical **Ubuntu 24.04**
   - **Shape:** `VM.Standard.A1.Flex` (ARM) → **1 OCPU / 6 GB RAM** is still free-tier and very generous.
     If ARM capacity is unavailable ("out of host capacity"), use `VM.Standard.E2.1.Micro` (AMD, 1 GB).
   - **Boot volume:** 47 GB (default) · **Assign public IPv4:** Yes
   - **SSH key:** *Generate a key pair* → **download the private key** (`ssh-key-....key`).
3. Wait ~2 min → note the **Public IP address**.
4. Open the firewall for web traffic:
   - Instance → **Primary VNIC → Subnet → Security Lists → Default Security List → Add Ingress Rules**
   - Add: Source `0.0.0.0/0`, IP Protocol **TCP**, Destination Port Range **80**
   - Repeat for **443**.
   - (Ubuntu images inside Oracle also ship with iptables rules — the setup script handles `ufw`; if you still
     can't reach port 80 later, run `sudo iptables -I INPUT 6 -p tcp --dport 80 -j ACCEPT` and
     `sudo netfilter-persistent save`.)

---

## Part 1-B — If Oracle needs a card you don't have

Use **any** Ubuntu host and the same script. Zero-card options that do **not** sleep:
- **Koyeb** — free web service, no card, Singapore region, Dockerfile or buildpack.
- **PythonAnywhere** — free tier (no card), always on, but WSGI-only (needs a small `wsgi.py` shim).
- **Oracle alternative:** ask a friend/family member with a card to create the account — the card is never billed.

---

## Part 2 — Upload the project & run the one-command installer

On your Windows PC (PowerShell, in `D:\CODING\CODE OP`):

```powershell
# 1. Copy the project + your real .env to the VM (replace IP + key path)
scp -i "$HOME\Downloads\ssh-key-2026.key" -r .\mediconnect ubuntu@<VM_PUBLIC_IP>:~/mediconnect

# 2. SSH in
ssh -i "$HOME\Downloads\ssh-key-2026.key" ubuntu@<VM_PUBLIC_IP>
```

On the VM:

```bash
cd ~/mediconnect

# IMPORTANT: edit .env for production before installing
nano .env
#   DEBUG=False
#   ALLOWED_HOSTS=mediconnect.yourdomain.com,<VM_PUBLIC_IP>
#   CSRF_TRUSTED_ORIGINS=https://mediconnect.yourdomain.com
#   SITE_URL=https://mediconnect.yourdomain.com
#   SECURE_SSL_REDIRECT=True

bash deploy/oracle_ubuntu_setup.sh mediconnect.yourdomain.com
```

The script does all of this, idempotently:
1. Installs Python 3.12, Nginx, Certbot, ufw, fail2ban
2. Creates a **2 GB swap file** (stops OOM kills on the 1 GB micro shape)
3. Builds `.venv` and installs `requirements.txt`
4. `collectstatic --clear` (Whitenoise manifest override for Nginx)
5. Registers **`mediconnect.service`** (Gunicorn, 3 workers × 2 threads, auto-restart, journald logs)
6. Writes the Nginx vhost with **Cloudflare real-IP restoration** so your Django rate limiter sees the real patient IP
7. Locks the firewall to 22/80/443 and enables fail2ban
8. Issues the free Let's Encrypt certificate and forces HTTPS

### Verify

```bash
sudo systemctl status mediconnect          # active (running)
curl -I http://127.0.0.1:8000/             # 200 / 302
sudo tail -f /var/log/nginx/access.log
```

Then browse `https://mediconnect.yourdomain.com` — the full portal should render.

---

## Part 3 — DNS + Cloudflare zone (10 min, free)

1. Buy/point a domain (Cloudflare Registrar at cost, or use a free `duckdns.org` subdomain).
2. `dash.cloudflare.com` → **Add a site** → Free plan → change the nameservers at your registrar.
3. **DNS → A record:** `mediconnect` → `<VM_PUBLIC_IP>`, **Proxy status: Proxied (orange cloud)**.
4. **SSL/TLS → Overview:** mode **Full (strict)**.
5. **SSL/TLS → Edge Certificates:** enable **Always Use HTTPS**, **TLS 1.3**, **HSTS (max-age 6 months)**.
6. **Speed → Optimization:** enable **Brotli**, **Early Hints**, **HTTP/3**.
7. **Caching → Configuration:** cache level **Standard**. *Do not* add a "Cache Everything" rule —
   HTML carries CSRF tokens; the Worker caches only immutable static files.

---

## PostHog setup health: what to expect

The portal sends analytics through its own `/pulse/` relay (first party), so:

| PostHog check | Expected | Why |
|---|---|---|
| `$pageview` | green | one per page load, with session id |
| `$pageleave` | green | `sendBeacon` on tab hide/close, carries duration + scroll depth |
| Scroll depth | green | sent as `scroll_pct` on `$pageleave` |
| Session IDs | green | UUIDv7 minted per tab session (`sessionStorage`) |
| Authorized URLs | green | add both your production host and the worker host |
| `$web_vitals` | green | LCP / CLS / INP via `PerformanceObserver`, once per page |
| Reverse proxy | **stays amber on purpose** | PostHog only detects its own `/ingest` client proxy; our server-side relay is stronger (adblockers never see a third-party request) but invisible to the checker |

Clinical, payment, dashboard, account and staff-scanner routes are **excluded before any
outbound request**, so no PHI ever reaches PostHog.

## Part 4 — Deploy the Cloudflare Worker (5 min, no CLI)

1. `dash.cloudflare.com` → **Compute (Workers)** → **Create** → **Start with Hello World** → **Deploy**.
2. **Edit Code** → delete the boilerplate → paste all of `deploy/cloudflare-worker.js` → **Deploy**.
3. **Settings → Variables and Secrets → Add:**
   - Name `ORIGIN_URL` · Value `https://mediconnect.yourdomain.com`
4. **Settings → Domains & Routes → Add custom domain:**
   `edge.yourdomain.com` (or `app.yourdomain.com`) → this is now your hardened public entry point.
5. Smoke test:
   ```bash
   curl -I https://edge.yourdomain.com/                # 200, X-MediConnect-Edge: DYNAMIC
   curl -I https://edge.yourdomain.com/static/css/site.css   # X-MediConnect-Edge: MISS, then HIT
   curl -I https://edge.yourdomain.com/.env            # 404 straight from the edge
   ```
6. Add `https://edge.yourdomain.com` to **PostHog → Settings → Authorized Domains**.

> The Worker also answers `/edge-health` (useful for a free UptimeRobot monitor — 5-minute checks keep
> the origin warm and give you a real uptime record for the IMPlEMENTATION checklist).

---

## Part 5 — Post-deploy checklist (must all pass)

```bash
cd ~/mediconnect
.venv/bin/python manage.py check --deploy          # 0 issues
.venv/bin/python manage.py check_db                # Neon 18.7 OK
.venv/bin/python manage.py check_storage           # Cloudinary OK
```

- [ ] `DEBUG=False`, `SECRET_KEY` is 50+ random chars
- [ ] `ALLOWED_HOSTS` has the real domain only (no `*`)
- [ ] `/mc-registry-vault/` admin path loads, `/admin/` returns 404
- [ ] `/.env`, `/.git/config`, `/wp-admin` return 404 **from the edge** (no origin hit)
- [ ] Login, book an appointment, print a prescription, open a receipt QR pass
- [ ] Cloudinary portrait loads over `https://res.cloudinary.com/...`
- [ ] PostHog Activity shows pageviews from the live domain
- [ ] Gmail SMTP: portal → **Mailer Console → Send test** → email arrives
- [ ] Auto-restart proof: `sudo reboot` → site back up in <60 s

---

## Part 6 — Day-2 operations (all free)

| Task | Command |
|---|---|
| Deploy new code | `scp -r mediconnect ubuntu@IP:~/ && ssh ... "cd ~/mediconnect && bash deploy/redeploy.sh"` |
| Logs | `sudo journalctl -u mediconnect -f` |
| Restart app | `sudo systemctl restart mediconnect` |
| Renew TLS | automatic (certbot systemd timer) — verify with `sudo certbot renew --dry-run` |
| DB backup | Neon keeps PITR history; also `pg_dump "$DATABASE_URL" > backup.sql` weekly |
| OS patches | `unattended-upgrades` is enabled by the installer |

---

### Files this step added / changed in the workspace

| File | Status | Purpose |
|---|---|---|
| `deploy/oracle_ubuntu_setup.sh` | new | One-command Oracle/Ubuntu provisioner (swap, Gunicorn, Nginx, certbot, firewall, Cloudflare real-IP) |
| `deploy/redeploy.sh` | new | Copy-code-then-restart helper for later updates |
| `deploy/cloudflare-worker.js` | rewritten | Edge cache for immutable assets only, edge 404s for probes, hardened headers, `/edge-health`, 503 fallback page |
| `deploy/wrangler.toml` | new | Optional CLI deploy config |
| `config/settings.py` | updated | `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` accept `.workers.dev`, `.koyeb.app`; no HTTPS redirect under local `runserver` |
| `build.sh` | updated | `migrate --noinput` (used by any PaaS build, harmless on the VM) |
