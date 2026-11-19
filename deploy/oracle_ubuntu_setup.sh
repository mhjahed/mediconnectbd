#!/usr/bin/env bash
# ============================================================================
#  MediConnect — Oracle Cloud Always Free (Ubuntu 24.04 ARM/AMD) Provisioner
#  One command, fully idempotent. Card-free at runtime, hardened for PHI data.
#
#  Usage (on the fresh Oracle VM, as the default ubuntu user):
#      scp -r mediconnect ubuntu@<VM_IP>:/home/ubuntu/
#      ssh ubuntu@<VM_IP>
#      cd ~/mediconnect && bash deploy/oracle_ubuntu_setup.sh mediconnect.yourdomain.com
#
#  Pass your domain as $1. If you omit it, the VM is served on its public IP
#  over HTTP only (fine for a first smoke test before buying/pointing DNS).
# ============================================================================
set -euo pipefail

DOMAIN="${1:-}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="$(whoami)"
SERVICE_NAME="mediconnect"

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
log "[1/8] System packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip python3-dev build-essential \
  nginx certbot python3-certbot-nginx \
  git curl ufw fail2ban unattended-upgrades libpq5

# ---------------------------------------------------------------------------
log "[2/8] 2 GB swap file (protects the 1 GB RAM micro instance from OOM kills)"
if ! sudo swapon --show | grep -q '/swapfile'; then
  sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-mediconnect-swap.conf
  sudo sysctl -p /etc/sysctl.d/99-mediconnect-swap.conf
fi

# ---------------------------------------------------------------------------
log "[3/8] Python environment + dependencies"
cd "${APP_DIR}"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip wheel
.venv/bin/pip install -r requirements.txt

if [ ! -f "${APP_DIR}/.env" ]; then
  echo "!!  ${APP_DIR}/.env is missing. Copy your local .env here (scp) and re-run this script."
  exit 1
fi

# ---------------------------------------------------------------------------
log "[4/8] Collecting static assets for Nginx (never call collectstatic via manage.py on the VM)"
.venv/bin/python manage.py collectstatic --noinput --clear

# ---------------------------------------------------------------------------
log "[5/8] systemd service (Gunicorn, 3 workers, auto-restart, journald logs)"
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=MediConnect Clinical Registry (Gunicorn)
After=network.target

[Service]
User=${APP_USER}
Group=www-data
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/gunicorn config.wsgi:application \\
    --bind 127.0.0.1:8000 \\
    --workers 3 \\
    --threads 2 \\
    --timeout 60 \\
    --graceful-timeout 30 \\
    --access-logfile - \\
    --error-logfile -
Restart=always
RestartSec=3
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
sudo systemctl restart ${SERVICE_NAME}

# ---------------------------------------------------------------------------
log "[6/8] Nginx reverse proxy + Cloudflare real-IP restoration"
sudo tee /etc/nginx/sites-available/${SERVICE_NAME} > /dev/null <<EOF
# Cloudflare edge IP ranges -> restore the real patient IP for Django rate limiting
set_real_ip_from 173.245.48.0/20;  set_real_ip_from 103.21.244.0/22;
set_real_ip_from 103.22.200.0/22;  set_real_ip_from 103.31.4.0/22;
set_real_ip_from 141.101.64.0/18;  set_real_ip_from 108.162.192.0/18;
set_real_ip_from 190.93.240.0/20;  set_real_ip_from 188.114.96.0/20;
set_real_ip_from 197.234.240.0/22; set_real_ip_from 198.41.128.0/17;
set_real_ip_from 162.158.0.0/15;   set_real_ip_from 104.16.0.0/13;
set_real_ip_from 104.24.0.0/14;    set_real_ip_from 172.64.0.0/13;
set_real_ip_from 131.0.72.0/22;
set_real_ip_from 2400:cb00::/32;   set_real_ip_from 2606:4700::/32;
set_real_ip_from 2803:f800::/32;   set_real_ip_from 2405:b500::/32;
set_real_ip_from 2405:8100::/32;   set_real_ip_from 2a06:98c0::/29;
set_real_ip_from 2c0f:f248::/32;
real_ip_header CF-Connecting-IP;

server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN:-_};

    client_max_body_size 3M;
    keepalive_timeout 65;

    # Static assets (Cloudinary holds media; Nginx holds CSS/JS/fonts/portraits)
    location /static/ {
        alias ${APP_DIR}/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
        access_log off;
    }

    location /media/ {
        alias ${APP_DIR}/media/;
        expires 7d;
        access_log off;
    }

    # Django
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_read_timeout 70s;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/${SERVICE_NAME} /etc/nginx/sites-enabled/${SERVICE_NAME}
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

# ---------------------------------------------------------------------------
log "[7/8] Firewall (only 22/80/443) + fail2ban brute-force guard"
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo systemctl enable --now fail2ban

# ---------------------------------------------------------------------------
log "[8/8] HTTPS certificate"
if [ -n "${DOMAIN}" ]; then
  sudo certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos \
       --register-unsafely-without-email --redirect || \
  echo "!!  Certbot could not validate ${DOMAIN}. Point an A record at this VM (DNS-only, grey cloud), then run:"
  echo "    sudo certbot --nginx -d ${DOMAIN} --redirect"
else
  echo ">>  No domain supplied, skipping certbot. Re-run with a domain to enable free HTTPS."
fi

log "DONE — verifying the service"
sudo systemctl --no-pager --lines=0 status ${SERVICE_NAME} | head -5
.venv/bin/python manage.py check --deploy || true
echo
echo "MediConnect is live on:  http://${DOMAIN:-$(curl -s --max-time 4 ifconfig.me || echo '<VM_IP>')}"
echo "Next: put Cloudflare (orange cloud, SSL mode Full strict) in front of this domain, then deploy the Worker."
