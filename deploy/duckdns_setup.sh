#!/usr/bin/env bash
# ============================================================================
#  DuckDNS keep-alive for the MediConnect Oracle VM (free HTTPS domain, $0)
#
#  Usage (on the VM):
#      bash deploy/duckdns_setup.sh mediconnect-sylhet <YOUR_DUCKDNS_TOKEN>
#
#  What it does
#   1. Points mediconnect-sylhet.duckdns.org at this VM's current public IP
#   2. Installs a systemd timer that re-points it every 5 minutes (survives
#      Oracle reassigning the ephemeral IP after a stop/start)
#   3. Prints the exact .env lines and the certbot command to run next
#
#  Get the token at https://www.duckdns.org  ->  sign in with GitHub/Google
#  (no card, no purchase; subdomains are free forever).
# ============================================================================
set -euo pipefail

SUB="${1:-}"
TOKEN="${2:-}"

if [ -z "${SUB}" ] || [ -z "${TOKEN}" ]; then
  echo "Usage: bash deploy/duckdns_setup.sh <subdomain-without-.duckdns.org> <duckdns-token>"
  exit 1
fi

FQDN="${SUB}.duckdns.org"
log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
log "[1/4] Updating ${FQDN} to this machine's public IP"
PUBLIC_IP="$(curl -fsS --max-time 8 https://api.ipify.org || true)"
echo "    detected public IP: ${PUBLIC_IP:-unknown}"
RESP="$(curl -fsS "https://www.duckdns.org/update?domains=${SUB}&token=${TOKEN}&ip=${PUBLIC_IP}")"
echo "    duckdns replied: ${RESP}"
if [ "${RESP}" != "OK" ]; then
  echo "!! DuckDNS rejected the update. Check the subdomain name and token."
  exit 1
fi

# ---------------------------------------------------------------------------
log "[2/4] Writing /etc/duckdns.env (root-only)"
sudo tee /etc/duckdns.env > /dev/null <<EOF
DUCKDNS_SUBDOMAIN=${SUB}
DUCKDNS_TOKEN=${TOKEN}
EOF
sudo chmod 600 /etc/duckdns.env

# ---------------------------------------------------------------------------
log "[3/4] Installing systemd service + 5-minute timer"
sudo tee /usr/local/bin/duckdns-update > /dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
set -a; . /etc/duckdns.env; set +a
IP="$(curl -fsS --max-time 8 https://api.ipify.org || true)"
[ -z "$IP" ] && exit 0
curl -fsS --max-time 10 "https://www.duckdns.org/update?domains=${DUCKDNS_SUBDOMAIN}&token=${DUCKDNS_TOKEN}&ip=${IP}" > /dev/null
EOF
sudo chmod 700 /usr/local/bin/duckdns-update

sudo tee /etc/systemd/system/duckdns.service > /dev/null <<'EOF'
[Unit]
Description=DuckDNS dynamic IP updater (MediConnect)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/duckdns-update
EOF

sudo tee /etc/systemd/system/duckdns.timer > /dev/null <<'EOF'
[Unit]
Description=Refresh DuckDNS every 5 minutes

[Timer]
OnBootSec=45s
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now duckdns.timer

# ---------------------------------------------------------------------------
log "[4/4] Done — DNS + timer active"
echo
echo "Timer status:"
systemctl --no-pager --lines=0 status duckdns.timer | head -4 || true
echo
echo "── Next: put these lines in ${HOME}/mediconnect/.env ────────────────────"
cat <<EOF
DEBUG=False
ALLOWED_HOSTS=${FQDN},<VM_PUBLIC_IP>
CSRF_TRUSTED_ORIGINS=https://${FQDN},https://*.workers.dev
SITE_URL=https://${FQDN}
SECURE_SSL_REDIRECT=True
HIDE_ROUTE_LIST_AFTER=2026-01-01
EOF
echo "────────────────────────────────────────────────────────────────────────"
echo
echo "Then issue the free HTTPS certificate (port 80 must be open in the Oracle"
echo "Security List AND reachable from the internet):"
echo "    sudo certbot --nginx -d ${FQDN} --redirect --agree-tos -m you@example.com"
echo
echo "Test:  curl -I https://${FQDN}/"
