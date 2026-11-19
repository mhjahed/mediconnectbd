# MediConnect — Free Domain Path: DuckDNS + Cloudflare Workers

**Total cost: $0.00 · No card · No domain purchase · HTTPS for visitors**

You picked "none, want free". Here is the one important thing to know before you start:

> A free `*.duckdns.org` hostname **cannot be added to Cloudflare as a zone** (Cloudflare only
> accepts apex domains you own). So you get:
> - **HTTPS directly on `https://<name>.duckdns.org`** via Let's Encrypt (real certificate, works forever, free)
> - **Cloudflare Workers as a second entry point** on `https://mediconnect-edge.<you>.workers.dev` —
>   still free, still 100K req/day, still gets edge caching + bot filtering, and the visitor still sees HTTPS.
>
> If you later buy a ~$9/yr domain, you can move it into Cloudflare and get the orange-cloud proxy,
> WAF, and edge caching on the vanity domain too. Nothing in this build has to change.

---

## Part 1 — Claim the free subdomain (2 minutes)

1. Go to **https://www.duckdns.org** → sign in with GitHub or Google (**no card**).
2. In "add domain", type something non-guessable, e.g. `mediconnect-sylhet` → **add domain**.
3. Copy the **token** shown at the top of the page.

You now own `mediconnect-sylhet.duckdns.org` (free forever).

---

## Part 2 — Point it at the Oracle VM

Copy the project to the VM (or `git clone` it) and run the new helper:

```bash
cd ~/mediconnect
bash deploy/duckdns_setup.sh mediconnect-sylhet <YOUR_DUCKDNS_TOKEN>
```

It does three things and is safe to re-run:
- sets the A record to the VM's current public IP
- installs `duckdns.timer` (systemd) which re-points the IP **every 5 minutes** — essential because
  Oracle Free Tier reassigns the ephemeral public IP after a stop/start
- prints the exact `.env` block to paste

Verify DNS before going further (wait ~60 s after the script):

```bash
dig +short mediconnect-sylhet.duckdns.org     # must equal the VM's public IP
```

---

## Part 3 — Oracle firewall (the #1 cause of "site unreachable")

Two separate doors must be open:

1. **Oracle Security List** → Instance → Primary VNIC → Subnet → Security Lists → Default Security List
   → Add Ingress Rules → Source `0.0.0.0/0`, TCP, port **80** — then again for **443**.
2. **The VM's own iptables** (Oracle's Ubuntu images ship with rules that block 80/443):

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

(`deploy/oracle_ubuntu_setup.sh` already runs `ufw allow 80,443` — but ufw sits *behind* the Oracle
Security List, so both layers matter.)

---

## Part 4 — Production `.env` on the VM

```dotenv
DEBUG=False
SECRET_KEY=<50+ random characters — regenerate, do not reuse your dev key>
ALLOWED_HOSTS=mediconnect-sylhet.duckdns.org,<VM_PUBLIC_IP>
CSRF_TRUSTED_ORIGINS=https://mediconnect-sylhet.duckdns.org,https://*.workers.dev
SITE_URL=https://mediconnect-sylhet.duckdns.org
SECURE_SSL_REDIRECT=True
HIDE_ROUTE_LIST_AFTER=2026-01-01
ADMIN_URL_PATH=django-admin/
ADMIN_CAMO_PREFIX=mc-registry-vault/
# ... keep DATABASE_URL (Neon), CLOUDINARY_URL, EMAIL_*, POSTHOG_* exactly as they are
```

Then:

```bash
cd ~/mediconnect
bash deploy/oracle_ubuntu_setup.sh mediconnect-sylhet.duckdns.org
```

That script already passes the domain to Nginx **and** Certbot, so HTTPS is configured in the same run.

---

## Part 5 — Free HTTPS certificate (Let's Encrypt)

If the setup script's Certbot step failed (usually because DNS wasn't live yet), run it directly:

```bash
sudo certbot --nginx -d mediconnect-sylhet.duckdns.org --redirect --agree-tos -m you@example.com
sudo certbot renew --dry-run          # proves auto-renewal works (systemd timer, free forever)
```

Result: `https://mediconnect-sylhet.duckdns.org` with a real, browser-trusted certificate.

> DuckDNS also offers a wildcard cert service (`*.duckdns.org`) if you prefer zero Certbot moving parts,
> but Let's Encrypt is the sturdier option and renews itself every 60 days without intervention.

---

## Part 6 — Cloudflare Workers edge (still $0, still no card)

1. **dash.cloudflare.com** → sign up (no card) → **Compute (Workers)** → **Create** → Hello World → **Deploy**.
2. **Edit Code** → paste all of `deploy/cloudflare-worker.js` → **Deploy**.
3. **Settings → Variables and Secrets**:
   - `ORIGIN_URL` = `https://mediconnect-sylhet.duckdns.org`
4. Your visitor URL is now: `https://mediconnect-edge.<your-subdomain>.workers.dev`

Smoke test:

```bash
curl -I https://mediconnect-edge.<you>.workers.dev/                       # DYNAMIC
curl -I https://mediconnect-edge.<you>.workers.dev/static/css/mediconnect.css   # MISS → HIT
curl -I https://mediconnect-edge.<you>.workers.dev/.env                   # 404 at the edge
```

Finally, add the workers.dev URL to **PostHog → Settings → Authorized Domains** (comma-separated with
the duckdns host) so Web Analytics groups both entry points correctly.

---

## What you gain vs. what you give up (honest table)

| Capability | Direct DuckDNS + LE | Via Cloudflare Worker |
|---|---|---|
| HTTPS for visitors | ✅ (Let's Encrypt) | ✅ |
| Edge caching of `/static` | ❌ | ✅ 30-day immutable |
| Bot/exploit filtering before origin | ❌ (Django middleware handles it) | ✅ at the edge |
| Orange-cloud WAF, rate limiting, analytics | ❌ | ❌ (needs a zone = needs an owned domain) |
| Custom vanity domain | ❌ | ❌ |
| Renewal work | Certbot timer (automatic) | none |

Django's own protections (`.env` probe 404s, 30/min scanner limit, 15/min login limit, branded 404,
CSP, HSTS, camo admin) all still apply on the direct path — the Worker is a *bonus* layer, not a
replacement for them.

---

## Optional: make the name nicer later, without redoing anything

Buy any cheap domain (~$1–12/yr), add it to Cloudflare, and:
- `CNAME mediconnect → mediconnect-sylhet.duckdns.org` (proxied), **or**
- `A mediconnect → <VM_IP>` (proxied)

Only `.env` changes (`ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SITE_URL`) plus
`sudo certbot --nginx -d newdomain.com --redirect`. No code touches. Because Certbot validates by HTTP,
keep port 80 open on the VM even after the switch.
