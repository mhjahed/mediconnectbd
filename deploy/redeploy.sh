#!/usr/bin/env bash
# ============================================================================
#  MediConnect — Redeploy helper for the Oracle VM (run after copying new code)
#  Usage:  bash deploy/redeploy.sh
# ============================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${APP_DIR}"

echo "==> Pulling dependency changes"
.venv/bin/pip install -q -r requirements.txt

echo "==> Applying migrations"
.venv/bin/python manage.py migrate --noinput

echo "==> Rebuilding static assets"
.venv/bin/python manage.py collectstatic --noinput

echo "==> Restarting Gunicorn"
sudo systemctl restart mediconnect

echo "==> Health check"
sleep 2
curl -fsS -o /dev/null -w "HTTP %{http_code} in %{time_total}s\n" http://127.0.0.1:8000/ || true
sudo systemctl --no-pager --lines=0 status mediconnect | head -4
