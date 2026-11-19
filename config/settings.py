import os
import sys
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env explicitly from BASE_DIR (handling Windows UTF-8 BOM / UTF-16 PowerShell files and .env.txt),
# overriding any stale system/PowerShell variables.
def _load_windows_safe_dotenv():
    for candidate in (BASE_DIR / '.env', BASE_DIR / '.env.txt', BASE_DIR.parent / '.env'):
        if candidate.is_file():
            raw = candidate.read_bytes()
            if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
                enc = 'utf-16'
            else:
                enc = 'utf-8-sig'
            load_dotenv(dotenv_path=candidate, override=True, encoding=enc)
            return str(candidate)
    load_dotenv(override=True)
    return ''

LOADED_ENV_FILE = _load_windows_safe_dotenv()

SECRET_KEY = os.getenv('SECRET_KEY', 'mc-dev-only-key-change-in-production-9f8a7b6c5d4e3f2a1b0c')
DEBUG = os.getenv('DEBUG', 'True').lower() in ('true', '1', 'yes')

_raw_hosts = os.getenv(
    'ALLOWED_HOSTS',
    'localhost,127.0.0.1,.e2b.app,.onrender.com,.workers.dev,.koyeb.app,.hf.space',
)
ALLOWED_HOSTS = ['*'] if DEBUG else [h.strip() for h in _raw_hosts.split(',') if h.strip()]
RENDER_EXTERNAL_HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME')
if RENDER_EXTERNAL_HOSTNAME and RENDER_EXTERNAL_HOSTNAME not in ALLOWED_HOSTS and ALLOWED_HOSTS != ['*']:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

CSRF_TRUSTED_ORIGINS = [
    'http://127.0.0.1:8000',
    'http://localhost:8000',
    'https://*.e2b.app',
    'https://*.onrender.com',
    'https://*.workers.dev',
    'https://*.koyeb.app',
    'https://*.hf.space',
    # Hugging Face serves embedded Spaces from an iframe on hf.co -> needed for POST forms there
    'https://huggingface.co',
    'https://*.huggingface.co',
]
_extra_origins = os.getenv('CSRF_TRUSTED_ORIGINS', '')
if _extra_origins:
    CSRF_TRUSTED_ORIGINS.extend([o.strip() for o in _extra_origins.split(',') if o.strip()])

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sitemaps',

    'rest_framework',
    'corsheaders',

    'core.apps.CoreConfig',
    'accounts.apps.AccountsConfig',
    'dashboard.apps.DashboardConfig',
    'appointments.apps.AppointmentsConfig',
    'payments.apps.PaymentsConfig',
    'medical_records.apps.MedicalRecordsConfig',
    'notifications.apps.NotificationsConfig',
    'access_control.apps.AccessControlConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.gzip.GZipMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.SecurityHeadersAndRateLimitMiddleware',
    'core.middleware.AdminPathCamoMiddleware',
    'accounts.middleware.ShiftAttendanceMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'accounts.context_processors.portal_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# Database: Hardened Neon Serverless PostgreSQL (via DATABASE_URL) with automatic SQLite fallback
# - Enforces SSL/TLS (sslmode=require) in transit on PostgreSQL
# - Enables CONN_HEALTH_CHECKS so Neon serverless scale-to-zero wakeups never drop connections
# - Applies statement_timeout (15s) + connect_timeout (10s) to block runaway query floods
_raw_db_url = os.getenv('DATABASE_URL', '').strip().strip('"').strip("'")
if _raw_db_url.startswith(('postgres://', 'postgresql://')):
    DATABASES = {
        'default': dj_database_url.parse(
            _raw_db_url,
            conn_max_age=int(os.getenv('DB_CONN_MAX_AGE', '300')),
            conn_health_checks=True,
            ssl_require=os.getenv('DB_SSL_REQUIRE', 'True').lower() in ('true', '1', 'yes'),
        )
    }
    _pg_opts = DATABASES['default'].setdefault('OPTIONS', {})
    _pg_opts.setdefault('connect_timeout', 10)
    # Note: Neon pooled endpoints (-pooler) reject startup `options=-c statement_timeout`;
    # apply statement_timeout only on direct endpoints or when explicitly enabled.
    if '-pooler.' not in _raw_db_url and os.getenv('DB_ENABLE_STATEMENT_TIMEOUT', 'True').lower() in ('true', '1', 'yes'):
        _pg_opts.setdefault('options', '-c statement_timeout=15000')
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            'OPTIONS': {'timeout': 20},
        }
    }

# Admin URL Hardening (IMPLEMENTATION.txt #9: Lock down admin routes via secret slug)
ADMIN_URL_PATH = os.getenv('ADMIN_URL_PATH', 'django-admin/').strip('/') + '/'

# Camouflage prefix: the ONLY URL in the wild that reaches the admin. It is silently
# rewritten to ADMIN_URL_PATH in middleware, so /django-admin/ and /admin/ both 404.
ADMIN_CAMO_PREFIX = os.getenv('ADMIN_CAMO_PREFIX', 'mc-registry-vault/').strip('/') + '/'

# After this UTC date/time the branded 404 replaces Django's debug 404 page, so the
# urlconf can never be dumped. Set to any past date to hide it right now
# (e.g. HIDE_ROUTE_LIST_AFTER=2026-01-01), or leave empty to keep the debug page.
HIDE_ROUTE_LIST_AFTER = os.getenv('HIDE_ROUTE_LIST_AFTER', '').strip()

# Cloud Media Storage: Cloudinary (25 GB Free, ZERO Credit Card Required!)
# Accepts either a single CLOUDINARY_URL=cloudinary://API_KEY:API_SECRET@CLOUD_NAME
# OR separate CLOUDINARY_CLOUD_NAME / CLOUDINARY_API_KEY / CLOUDINARY_API_SECRET in .env.
_cloudinary_url = os.getenv('CLOUDINARY_URL', '').strip().strip('"').strip("'")
CLOUDINARY_CLOUD_NAME = os.getenv('CLOUDINARY_CLOUD_NAME', '').strip()
CLOUDINARY_API_KEY = os.getenv('CLOUDINARY_API_KEY', '').strip()
CLOUDINARY_API_SECRET = os.getenv('CLOUDINARY_API_SECRET', '').strip()
if _cloudinary_url.startswith('cloudinary://') and not (CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET):
    try:
        from urllib.parse import urlparse
        _cu = urlparse(_cloudinary_url)
        CLOUDINARY_API_KEY = _cu.username or ''
        CLOUDINARY_API_SECRET = _cu.password or ''
        CLOUDINARY_CLOUD_NAME = _cu.hostname or ''
    except Exception:
        pass
USE_CLOUDINARY = bool(CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET)
if USE_CLOUDINARY:
    INSTALLED_APPS.extend(['cloudinary_storage', 'cloudinary'])
    CLOUDINARY_STORAGE = {
        'CLOUD_NAME': CLOUDINARY_CLOUD_NAME,
        'API_KEY': CLOUDINARY_API_KEY,
        'API_SECRET': CLOUDINARY_API_SECRET,
        'SECURE': True,
        'PREFIX': 'mediconnect',
    }

# Optional S3-Compatible Media Storage fallback (Cloudflare R2 / Supabase S3 / Backblaze B2)
R2_ACCESS_KEY_ID = os.getenv('R2_ACCESS_KEY_ID', '').strip()
R2_SECRET_ACCESS_KEY = os.getenv('R2_SECRET_ACCESS_KEY', '').strip()
R2_BUCKET_NAME = os.getenv('R2_BUCKET_NAME', '').strip()
R2_ENDPOINT_URL = os.getenv('R2_ENDPOINT_URL', '').strip()
R2_CUSTOM_DOMAIN = os.getenv('R2_CUSTOM_DOMAIN', '').strip()
USE_S3_STORAGE = bool(R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET_NAME and R2_ENDPOINT_URL)

# Optional Consent-Gated PostHog Product Analytics (loads ONLY if patient/user opts in)
POSTHOG_API_KEY = os.getenv('POSTHOG_API_KEY', '').strip()
POSTHOG_HOST = os.getenv('POSTHOG_HOST', 'https://us.i.posthog.com').strip()

# Cache (used for rate limiting, idempotency locks, and public directory caching)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'mediconnect-cache',
    }
}

AUTH_USER_MODEL = 'accounts.CustomUser'
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

PASSWORD_RESET_TIMEOUT = 3600  # 1-hour expiring password reset links

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Dhaka'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
if USE_CLOUDINARY:
    _default_media_backend = 'cloudinary_storage.storage.MediaCloudinaryStorage'
elif USE_S3_STORAGE:
    _default_media_backend = 'storages.backends.s3.S3Storage'
else:
    _default_media_backend = 'django.core.files.storage.FileSystemStorage'

STORAGES = {
    'default': {
        'BACKEND': _default_media_backend,
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

if USE_S3_STORAGE and not USE_CLOUDINARY:
    AWS_ACCESS_KEY_ID = R2_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY = R2_SECRET_ACCESS_KEY
    AWS_STORAGE_BUCKET_NAME = R2_BUCKET_NAME
    AWS_S3_ENDPOINT_URL = R2_ENDPOINT_URL
    AWS_S3_REGION_NAME = 'auto'
    AWS_S3_SIGNATURE_VERSION = 's3v4'
    AWS_S3_ADDRESSING_STYLE = 'path'  # Required for Cloudflare R2 S3 endpoint compatibility
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True  # Signed expiring URLs so medical files are never public!
    AWS_QUERYSTRING_EXPIRE = 900  # 15-minute signed URL expiry
    if R2_CUSTOM_DOMAIN:
        AWS_S3_CUSTOM_DOMAIN = R2_CUSTOM_DOMAIN

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

LOGS_DIR = BASE_DIR / 'logs'
for _path in (LOGS_DIR, MEDIA_ROOT / 'profile_pics', MEDIA_ROOT / 'barcodes', MEDIA_ROOT / 'qr_codes'):
    _path.mkdir(parents=True, exist_ok=True)

# Upload limits (2.5 MB max request/upload size)
DATA_UPLOAD_MAX_MEMORY_SIZE = 2621440
FILE_UPLOAD_MAX_MEMORY_SIZE = 2621440

# Session & CSRF settings (compatible with Arena live preview iframe + production HTTPS)
SESSION_COOKIE_AGE = 86400
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = 'Lax'
X_FRAME_OPTIONS = 'ALLOWALL' if DEBUG else 'SAMEORIGIN'

# Never force HTTPS / HSTS redirects when running local `manage.py runserver` (which only speaks plain HTTP!)
_is_local_runserver = 'runserver' in sys.argv
if not DEBUG and not _is_local_runserver:
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'True').lower() == 'true'
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [
    'http://localhost:8000',
    'http://127.0.0.1:8000',
]

# Gmail SMTP (App Password) + Optional Resend HTTP API Support
# Automatically uses Gmail SMTP when EMAIL_HOST_USER + EMAIL_HOST_PASSWORD are set in .env
# (or saved directly inside the Portal Mailer -> SMTP Settings screen).
_gmail_user = os.getenv('EMAIL_HOST_USER', '').strip()
_gmail_pass = os.getenv('EMAIL_HOST_PASSWORD', '').replace(' ', '').strip()
_explicit_backend = os.getenv('EMAIL_BACKEND', '').strip()
if _gmail_user and _gmail_pass:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
elif _explicit_backend:
    EMAIL_BACKEND = _explicit_backend
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com').strip() or 'smtp.gmail.com'
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', 'False').lower() in ('true', '1', 'yes')
EMAIL_TIMEOUT = int(os.getenv('EMAIL_TIMEOUT', '15'))
EMAIL_HOST_USER = _gmail_user
EMAIL_HOST_PASSWORD = _gmail_pass
DEFAULT_FROM_EMAIL = os.getenv(
    'DEFAULT_FROM_EMAIL',
    f'MediConnect Registry <{_gmail_user}>' if _gmail_user else 'MediConnect Registry <noreply@mediconnect.bd>',
)
SUPPORT_CONTACT_EMAIL = os.getenv('SUPPORT_CONTACT_EMAIL', _gmail_user or 'support@mediconnect.bd')
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')

SITE_URL = os.getenv('SITE_URL', 'http://127.0.0.1:8000')
ACCESS_KEY_PREFIX = os.getenv('ACCESS_KEY_PREFIX', 'MEDI')
GOOGLE_SITE_VERIFICATION = os.getenv('GOOGLE_SITE_VERIFICATION', '')
BING_SITE_VERIFICATION = os.getenv('BING_SITE_VERIFICATION', '')

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '[{levelname}] {asctime} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': str(LOGS_DIR / 'mediconnect.log'),
            'formatter': 'standard',
            'encoding': 'utf-8',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'mediconnect': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
