# ============================================================================
#  MediConnect — Hugging Face Spaces (Docker) runtime
#  Django + Gunicorn on port 7860 (the port HF Spaces REQUIRES).
#
#  No Nginx, no Certbot, no .env file: HF terminates TLS and injects secrets as
#  environment variables, which config/settings.py already reads via os.getenv().
# ============================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860

WORKDIR /app

# libpq is needed by psycopg; the rest keeps psycopg[binary] + pillow happy.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/*

# Dependencies first so Docker layer caching survives code-only changes.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Collect static at build time (Whitenoise serves them at runtime; no CDN needed).
RUN python manage.py collectstatic --no-input

# HF runs the container as a non-root user; give it a writable home + log dir.
RUN mkdir -p /app/logs /app/media /app/staticfiles \
 && useradd -m -u 1000 appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 7860

# 3 workers x 2 threads keeps memory under the free 16 GB budget comfortably.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:7860", \
     "--workers", "3", \
     "--threads", "2", \
     "--timeout", "90", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
