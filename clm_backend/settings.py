from pathlib import Path
from datetime import timedelta
import sys
import os
from urllib.parse import urlparse, parse_qs, unquote
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from this project reliably (do not depend on CWD).
load_dotenv(dotenv_path=BASE_DIR / '.env', override=False)

# Workspace fallback: some dev setups keep a single .env at the monorepo root.
# Load it only to fill missing variables.
_workspace_env = BASE_DIR.parent / '.env'
if _workspace_env.exists():
    load_dotenv(dotenv_path=_workspace_env, override=False)

# Backward-compatible fallback: some setups store secrets in contracts/.env.
# We load it only to fill missing variables (override=False).
_contracts_env = BASE_DIR / 'contracts' / '.env'
if _contracts_env.exists():
    load_dotenv(dotenv_path=_contracts_env, override=False)

# Google OAuth (used by POST /api/auth/google/)
# Normalize env vars so deployments that only set NEXT_PUBLIC_GOOGLE_CLIENT_ID still work server-side.
if not (os.getenv('GOOGLE_CLIENT_ID') or '').strip():
    fallback_client_id = (os.getenv('NEXT_PUBLIC_GOOGLE_CLIENT_ID') or os.getenv('Google_reidirect') or '').strip()
    if fallback_client_id:
        os.environ['GOOGLE_CLIENT_ID'] = fallback_client_id

GOOGLE_CLIENT_ID = (os.getenv('GOOGLE_CLIENT_ID') or '').strip() or None
GOOGLE_CLIENT_IDS = (os.getenv('GOOGLE_CLIENT_IDS') or '').strip()

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'django-insecure-dev-key-12345')

DEBUG = os.getenv('DEBUG', 'False').strip().lower() in ('1', 'true', 'yes', 'y', 'on')

# Bootstrap admin promotion (dev/staging convenience)
#
# When enabled, users whose email matches BOOTSTRAP_ADMIN_EMAILS will be promoted
# to staff (admin) automatically at login/register. Keep disabled in production
# unless you explicitly want this behavior.
ENABLE_BOOTSTRAP_ADMINS = (
    os.getenv('ENABLE_BOOTSTRAP_ADMINS', 'False').strip().lower() in ('1', 'true', 'yes', 'y', 'on')
    or DEBUG
)

_bootstrap_admin_emails_raw = os.getenv('BOOTSTRAP_ADMIN_EMAILS', '').strip()
BOOTSTRAP_ADMIN_EMAILS = {
    e.strip().lower()
    for e in _bootstrap_admin_emails_raw.split(',')
    if e.strip()
}

# Project default bootstrap admin(s) for local/dev usage.
# If you don't want any hardcoded emails, set BOOTSTRAP_ADMIN_EMAILS in env and/or disable ENABLE_BOOTSTRAP_ADMINS.
BOOTSTRAP_ADMIN_EMAILS |= {
    'rahuljha93102@gmail.com',
}

# Supabase API configuration (distinct from Postgres DB settings)
SUPABASE_URL = (os.getenv('SUPABASE_URL') or '').strip() or None
SUPABASE_ANON_KEY = (os.getenv('SUPABASE_ANON_KEY') or '').strip() or None
SUPABASE_KEY = (os.getenv('SUPABASE_KEY') or '').strip() or SUPABASE_ANON_KEY or None

# When enabled, refuse to run in production with placeholder secrets.
SECURITY_STRICT = os.getenv('SECURITY_STRICT', 'False').strip().lower() in ('1', 'true', 'yes', 'y', 'on')

if DEBUG:
    ALLOWED_HOSTS = ['*']
else:
    _hosts = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').strip()
    ALLOWED_HOSTS = [h.strip() for h in _hosts.split(',') if h.strip()]

if SECURITY_STRICT and (not DEBUG) and SECRET_KEY == 'django-insecure-dev-key-12345':
    raise RuntimeError('DJANGO_SECRET_KEY must be set when SECURITY_STRICT is enabled')

# Check if using SQLite for local development
USE_SQLITE = os.getenv('DB_ENGINE', '').strip().lower() == 'sqlite3'

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'drf_spectacular',
    'corsheaders',
    'authentication',
    'contracts',
    'workflows',
    'notifications',
    'audit_logs',
    'search',
    'repository',
    'metadata',
    'ocr',
    'redaction',
    'ai',
    'rules',
    'approvals',
    'tenants',
    'calendar_events',
    'reviews',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'clm_backend.middleware.RequestIdMiddleware',
    'clm_backend.middleware.SlowQueryLoggingMiddleware',
    'clm_backend.middleware.TenantIsolationMiddleware',
    'clm_backend.middleware.MetricsMiddleware',
    'clm_backend.middleware.AuditLoggingMiddleware',
    'clm_backend.middleware.PIIProtectionLoggingMiddleware',
    'clm_backend.middleware.SecurityHeadersMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'clm_backend.urls'

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
            ],
        },
    },
]

WSGI_APPLICATION = 'clm_backend.wsgi.application'

# Database configuration (Supabase/PostgreSQL only)
DATABASE_URL = os.getenv('DATABASE_URL', '').strip()
DB_ENGINE = 'django.db.backends.postgresql'
DB_HOST = os.getenv('DB_HOST', '')
DB_PORT = os.getenv('DB_PORT', '5432')

# Supabase pooler guidance:
# - Session pooler is typically port 5432 and has a small fixed pool_size.
# - Transaction pooler is typically port 6543 and is the recommended option for Django.
# This project defaults to transaction mode when using the pooler host, unless explicitly overridden.
DB_POOLER_MODE = (os.getenv('DB_POOLER_MODE', '') or '').strip().lower()  # 'transaction' | 'session'
DB_POOLER_TRANSACTION_PORT = os.getenv('DB_POOLER_TRANSACTION_PORT', '6543')
DB_POOLER_SESSION_PORT = os.getenv('DB_POOLER_SESSION_PORT', '5432')


def _parse_database_url(database_url: str) -> dict:
    """Parse a Postgres DATABASE_URL into Django DATABASES['default'] keys."""
    parsed = urlparse(database_url)
    scheme = (parsed.scheme or '').lower()
    if scheme not in ('postgres', 'postgresql'):
        raise ValueError('DATABASE_URL must start with postgresql://')

    name = (parsed.path or '').lstrip('/')
    if not name:
        name = 'postgres'

    user = unquote(parsed.username or '')
    password = unquote(parsed.password or '')
    host = parsed.hostname or ''
    port = str(parsed.port or 5432)

    qs = parse_qs(parsed.query or '')
    sslmode = (qs.get('sslmode', [None])[0] or os.getenv('DB_SSLMODE', 'require'))

    # Keep compatibility with Supabase pooler constraints.
    using_pooler = 'pooler.supabase.com' in (host or '')

    # If the pooler is used, prefer transaction mode unless explicitly set.
    # This prevents: "MaxClientsInSessionMode: max clients reached".
    desired_pool_mode = (os.getenv('DB_POOLER_MODE', '') or '').strip().lower()
    if using_pooler and not desired_pool_mode:
        desired_pool_mode = 'transaction'
    if using_pooler and desired_pool_mode == 'transaction' and port == DB_POOLER_SESSION_PORT:
        port = DB_POOLER_TRANSACTION_PORT

    # Supabase pooler requires the user in the form `postgres.<project_ref>`.
    # If a DATABASE_URL is accidentally set to `postgres@...pooler.supabase.com`,
    # authentication will fail (and can trigger the pooler circuit-breaker).
    if using_pooler and (not user or '.' not in user):
        env_user = (os.getenv('DB_USER', '') or '').strip()
        env_password = (os.getenv('DB_PASSWORD', '') or '').strip()
        if env_user and '.' in env_user:
            user = env_user
            if not password and env_password:
                password = env_password
    default_conn_max_age = 0 if using_pooler else 60
    conn_max_age = int(os.getenv('DB_CONN_MAX_AGE', str(default_conn_max_age)))

    return {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': name,
        'USER': user,
        'PASSWORD': password,
        'HOST': host,
        'PORT': port,
        'CONN_MAX_AGE': conn_max_age,
        'CONN_HEALTH_CHECKS': True,
        'DISABLE_SERVER_SIDE_CURSORS': True,
        'OPTIONS': {
            'sslmode': sslmode,
            'connect_timeout': int(os.getenv('DB_CONNECT_TIMEOUT', '20')),
            'keepalives': 1,
            'keepalives_idle': int(os.getenv('DB_KEEPALIVES_IDLE', '30')),
            'keepalives_interval': int(os.getenv('DB_KEEPALIVES_INTERVAL', '10')),
            'keepalives_count': int(os.getenv('DB_KEEPALIVES_COUNT', '5')),
            'options': os.getenv(
                'DB_PG_OPTIONS',
                '-c statement_timeout=120000 -c idle_in_transaction_session_timeout=120000',
            ),
        },
        'TEST': {
            'NAME': os.getenv('DB_TEST_NAME', 'test_postgres'),
            # Supabase projects commonly rely on extensions (e.g. pgvector/pg_trgm).
            # Running migrations ensures those extensions are created before tables.
            'MIGRATE': os.getenv('DB_TEST_MIGRATE', 'true').strip().lower() in ('1', 'true', 'yes', 'y', 'on'),
        },
    }

# Supabase pooler notes:
# - If you use the Supabase pooler host (contains "pooler.supabase.com"), you MUST avoid long-lived connections.
#   Session mode poolers have a small pool_size; persistent Django connections can exhaust it quickly.
# - If possible, prefer Supabase pooler in TRANSACTION mode (often port 6543 in Supabase UI), and keep CONN_MAX_AGE=0.
USING_SUPABASE_POOLER = 'pooler.supabase.com' in (DB_HOST or '')
DEFAULT_CONN_MAX_AGE = 0 if USING_SUPABASE_POOLER else 60


# If we are using Supabase pooler and the port is still set to the session pooler port, automatically
# flip to transaction pooler port unless the user explicitly requested session mode.
if USING_SUPABASE_POOLER:
    _mode = DB_POOLER_MODE or 'transaction'
    if _mode == 'transaction' and str(DB_PORT) == DB_POOLER_SESSION_PORT:
        DB_PORT = DB_POOLER_TRANSACTION_PORT

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'postgres'),
        'USER': os.getenv('DB_USER', ''),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': DB_HOST,
        'PORT': DB_PORT,
        # For Supabase pooler: keep this at 0 to avoid "max clients reached".
        'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', str(DEFAULT_CONN_MAX_AGE))),
        # Recommended for pgbouncer/poolers.
        'CONN_HEALTH_CHECKS': True,
        'DISABLE_SERVER_SIDE_CURSORS': True,
        'OPTIONS': {
            'sslmode': os.getenv('DB_SSLMODE', 'require'),
            # psycopg2/libpq connect timeout (seconds)
            'connect_timeout': int(os.getenv('DB_CONNECT_TIMEOUT', '20')),
            # libpq keepalives
            'keepalives': 1,
            'keepalives_idle': int(os.getenv('DB_KEEPALIVES_IDLE', '30')),
            'keepalives_interval': int(os.getenv('DB_KEEPALIVES_INTERVAL', '10')),
            'keepalives_count': int(os.getenv('DB_KEEPALIVES_COUNT', '5')),
            # Server-side timeouts (ms)
                'options': os.getenv('DB_PG_OPTIONS', '-c statement_timeout=120000 -c idle_in_transaction_session_timeout=120000'),
            },
            'TEST': {
                # Use a stable Supabase test DB name; run tests with --keepdb.
                'NAME': os.getenv('DB_TEST_NAME', 'test_postgres'),
                # Default to running migrations so extensions required by models (pgvector/pg_trgm)
                # are available during test database creation.
                'MIGRATE': os.getenv('DB_TEST_MIGRATE', 'true').strip().lower() in ('1', 'true', 'yes', 'y', 'on'),
            },
        }
    }

# Tests against Supabase should not keep long-lived connections open.
# Persistent connections can prevent the test database from being dropped.
if any(arg == 'test' for arg in sys.argv):
    try:
        DATABASES['default']['CONN_MAX_AGE'] = 0
    except Exception:
        pass

    # Supabase environments can keep background/pooled connections open.
    # Dropping the test DB can become flaky; keepdb avoids noisy failures.
    TEST_RUNNER = 'clm_backend.test_runner.SupabaseKeepdbTestRunner'

# If a single DATABASE_URL is provided, it takes precedence.
if DATABASE_URL:
    DATABASES['default'] = _parse_database_url(DATABASE_URL)


def _is_supabase_db_host(host: str) -> bool:
    h = (host or '').strip().lower()
    if not h:
        return False
    return h.endswith('.supabase.co') or h.endswith('.supabase.com') or 'pooler.supabase.com' in h


# Enforce "Supabase-only" DB connectivity.
# This project is intended to run against Supabase Postgres (direct or pooler),
# not a generic/local Postgres instance.
SUPABASE_ONLY = os.getenv('SUPABASE_ONLY', 'True').strip().lower() in ('1', 'true', 'yes', 'y', 'on')
if SUPABASE_ONLY:
    _db = DATABASES.get('default', {})
    _engine = (_db.get('ENGINE') or '').strip().lower()
    _host = (_db.get('HOST') or '').strip()
    _sslmode = ((_db.get('OPTIONS') or {}).get('sslmode') or '').strip().lower()

    if _engine and _engine != 'django.db.backends.postgresql':
        raise RuntimeError('SUPABASE_ONLY is enabled but DATABASE engine is not PostgreSQL')
    if not _is_supabase_db_host(_host):
        raise RuntimeError(
            'SUPABASE_ONLY is enabled but DB host is not a Supabase host. '
            'Set DB_HOST to your Supabase host (e.g. db.<ref>.supabase.co) or pooler (..pooler.supabase.com).'
        )
    if _sslmode and _sslmode not in ('require', 'verify-ca', 'verify-full'):
        raise RuntimeError('SUPABASE_ONLY is enabled but DB sslmode is not secure (expected require/verify-*)')

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'

STATIC_ROOT = BASE_DIR / 'staticfiles'

STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'authentication.User'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'authentication.jwt_auth.StatelessJWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_SCHEMA_CLASS': 'clm_backend.schema.FeatureAutoSchema',
    'DEFAULT_THROTTLE_CLASSES': [] if (
        DEBUG and os.getenv('DISABLE_THROTTLING_IN_DEBUG', 'True').strip().lower() in ('1', 'true', 'yes', 'y', 'on')
    ) else [
        'clm_backend.throttling.TenantUserRateThrottle',
        'clm_backend.throttling.SafeAnonRateThrottle',
        'clm_backend.throttling.SafeScopedRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        # Applies to AnonRateThrottle
        'anon': os.getenv('THROTTLE_ANON', '60/min'),
        # Applies to TenantUserRateThrottle
        'tenant_user': os.getenv('THROTTLE_TENANT_USER', '600/min'),
        # Scoped throttles (set `throttle_scope = ...` on views)
        'auth': os.getenv('THROTTLE_AUTH', '10/min'),
        'ai': os.getenv('THROTTLE_AI', '30/min'),
        'uploads': os.getenv('THROTTLE_UPLOADS', '20/min'),
        'firma': os.getenv('THROTTLE_FIRMA', '120/min'),
    },
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=24),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'user_id',
    'USER_ID_CLAIM': 'user_id',
}

# OpenAPI / Swagger (drf-spectacular)
SPECTACULAR_SETTINGS = {
    'TITLE': os.getenv('OPENAPI_TITLE', 'CLM Backend API'),
    'DESCRIPTION': os.getenv(
        'OPENAPI_DESCRIPTION',
        'Contract lifecycle management backend (Django REST Framework).'
    ),
    'VERSION': os.getenv('OPENAPI_VERSION', '1.0.0'),
    # Expose Bearer auth in Swagger UI.
    'SECURITY': [{'bearerAuth': []}],
    'COMPONENT_SPLIT_REQUEST': True,
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENTS': {
        'securitySchemes': {
            'bearerAuth': {
                'type': 'http',
                'scheme': 'bearer',
                'bearerFormat': 'JWT',
            }
        }
    },
    # Makes Swagger UI less frustrating during manual testing.
    'SWAGGER_UI_SETTINGS': {
        'persistAuthorization': True,
    },
}

# Cloudflare R2 settings (used by authentication.r2_service.R2StorageService)
R2_ACCOUNT_ID = os.getenv('R2_ACCOUNT_ID', '')
R2_ACCESS_KEY_ID = os.getenv('R2_ACCESS_KEY_ID', '')
R2_SECRET_ACCESS_KEY = os.getenv('R2_SECRET_ACCESS_KEY', '')
R2_BUCKET_NAME = os.getenv('R2_BUCKET_NAME', '')
R2_ENDPOINT_URL = os.getenv('R2_ENDPOINT_URL', '')
R2_CONNECT_TIMEOUT = int(os.getenv('R2_CONNECT_TIMEOUT', '5'))
R2_READ_TIMEOUT = int(os.getenv('R2_READ_TIMEOUT', '30'))

if not R2_ENDPOINT_URL and R2_ACCOUNT_ID:
    R2_ENDPOINT_URL = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

R2_PUBLIC_URL = os.getenv('R2_PUBLIC_URL', '')

CORS_ALLOWED_ORIGINS = [
    # Local Development
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://localhost:4000",
    "http://127.0.0.1:4000",
    "https://lawflow-267708864896.asia-south1.run.app",
    "https://verdant-douhua-1148be.netlify.app",
    "http://127.0.0.1:8000",
    "https://lawflow.lawflow-dev.workers.dev",
    "http://127.0.0.1:8000",
    "http://localhost",
    "http://127.0.0.1",
]


def _normalize_cors_origin(origin: str) -> str | None:
    """Normalize an origin to scheme://host[:port] (no path/query/fragment).

    django-cors-headers rejects origins that include paths.
    """

    raw = (origin or '').strip()
    if not raw:
        return None

    # If it's a full URL, reduce to origin.
    try:
        parsed = urlparse(raw)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass

    # Fallback: keep string but strip trailing slashes.
    return raw.rstrip('/') or None


# Normalize hard-coded origins (defensive; avoids accidental paths).
_normalized = []
for _o in CORS_ALLOWED_ORIGINS:
    _n = _normalize_cors_origin(_o)
    if _n and _n not in _normalized:
        _normalized.append(_n)
CORS_ALLOWED_ORIGINS = _normalized

_cors_extra = os.getenv('CORS_ALLOWED_ORIGINS_EXTRA', '').strip()
if _cors_extra:
    for _origin in [o.strip() for o in _cors_extra.split(',') if o.strip()]:
        _normalized_origin = _normalize_cors_origin(_origin)
        if _normalized_origin and _normalized_origin not in CORS_ALLOWED_ORIGINS:
            CORS_ALLOWED_ORIGINS.append(_normalized_origin)


CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = ['DELETE', 'GET', 'OPTIONS', 'PATCH', 'POST', 'PUT']
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'baggage',
    'content-type',
    'dnt',
    'origin',
    'sentry-trace',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
    'x-api-key',
    'x-device-id',
    'x-tenant-id',
    'x-tenant',
]

# Optional: allow all origins (useful for temporary debugging only).
# With CORS_ALLOW_CREDENTIALS=True, django-cors-headers will echo the request Origin.
CORS_ALLOW_ALL_ORIGINS = os.getenv('CORS_ALLOW_ALL_ORIGINS', 'False').strip().lower() in (
    '1', 'true', 'yes', 'y', 'on'
)

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
VOYAGE_API_KEY = os.getenv('VOYAGE_API_KEY', '')
VOYAGE_CONTEXT = os.getenv('VOYAGE_CONTEXT', '')

# Use gemini-2.5-flash-lite by default (better availability for many dev setups).
# Override via env: GEMINI_REVIEW_MODEL=...
GEMINI_REVIEW_MODEL = os.getenv('GEMINI_REVIEW_MODEL', 'gemini-2.5-flash-lite')
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'False').strip().lower() in ('1', 'true', 'yes', 'y', 'on')
SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'False').strip().lower() in ('1', 'true', 'yes', 'y', 'on')
CSRF_COOKIE_SECURE = os.getenv('CSRF_COOKIE_SECURE', 'False').strip().lower() in ('1', 'true', 'yes', 'y', 'on')
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = os.getenv('SECURE_REFERRER_POLICY', 'same-origin')

SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '0' if DEBUG else '31536000'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv('SECURE_HSTS_INCLUDE_SUBDOMAINS', 'True').strip().lower() in ('1', 'true', 'yes', 'y', 'on')
SECURE_HSTS_PRELOAD = os.getenv('SECURE_HSTS_PRELOAD', 'True').strip().lower() in ('1', 'true', 'yes', 'y', 'on')

_csrf_trusted = os.getenv('CSRF_TRUSTED_ORIGINS', '').strip()
if _csrf_trusted:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_trusted.split(',') if o.strip()]

# ---------------------------------------------------------------------------
# Cache (used by DRF throttling) — Redis recommended in production
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# DB bottleneck visibility
# ---------------------------------------------------------------------------

# Logs any individual DB query slower than this threshold (milliseconds).
# Use in staging/load-test runs, not as a permanent production default.
DB_SLOW_QUERY_MS = int(os.getenv('DB_SLOW_QUERY_MS', '0') or '0')

REDIS_URL = (os.getenv('REDIS_URL', '') or os.getenv('CACHE_REDIS_URL', '')).strip()
# Prefer in-memory cache during local DEBUG runs unless explicitly overridden.
CACHE_BACKEND = (os.getenv('CACHE_BACKEND', 'locmem' if DEBUG else 'redis') or '').strip().lower()

if CACHE_BACKEND == 'redis' and REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
            'TIMEOUT': int(os.getenv('CACHE_DEFAULT_TIMEOUT', '300')),
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'clm-backend',
        }
    }

# Email Configuration - Google SMTP with App Password
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.getenv('GMAIL', '')
EMAIL_HOST_PASSWORD = os.getenv('APP_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER)
SERVER_EMAIL = os.getenv('SERVER_EMAIL', EMAIL_HOST_USER)

if SECURITY_STRICT and (not DEBUG) and (not EMAIL_HOST_USER or not EMAIL_HOST_PASSWORD):
    raise RuntimeError('Email credentials must be set when SECURITY_STRICT is enabled')

# Frontend base URL (used for emails and signer magic links)
FRONTEND_BASE_URL = (os.getenv('FRONTEND_BASE_URL') or os.getenv('APP_URL') or 'http://localhost:3000').strip().rstrip('/')

# Celery Configuration
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes max
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60  # 25 minutes soft limit
