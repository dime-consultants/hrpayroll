import os
from pathlib import Path
from datetime import timedelta
from celery.schedules import crontab
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY', default='change-me-in-production')
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config(
    'ALLOWED_HOSTS',
    default='hr.dimeapp.co.ke,localhost,127.0.0.1',
    cast=Csv()
)

AUTH_USER_MODEL = 'users.User'


DJANGO_APPS = [
    'unfold',
    'unfold.contrib.filters',
    'unfold.contrib.forms',
    'unfold.contrib.inlines',
    'unfold.contrib.import_export',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_filters',
    'axes',
    'users',
]

THIRD_PARTY_APPS = [
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    
    'drf_spectacular',
    'django_celery_beat',
    'django_celery_results',
    'import_export',
    'django_redis',
]

LOCAL_APPS = [
    'apps.base',
    'apps.organizations',
    'apps.payroll',
    'apps.repayments',
    'apps.api',
    'apps.loans',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'apps.api.exception_middleware.DisallowedHostMiddleware',  # Handle invalid hosts gracefully
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'axes.middleware.AxesMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.api.middleware.OrganizationIsolationMiddleware',
]

ROOT_URLCONF = 'hr_payroll.urls'

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

WSGI_APPLICATION = 'hr_payroll.wsgi.application'

# Database
if DEBUG:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': config('DB_NAME', default='hr_payroll'),
            'USER': config('DB_USER', default='hr_payroll_user'),
            'PASSWORD': config('DB_PASSWORD', default='securepassword'),
            'HOST': config('DB_HOST', default='db'),
            'PORT': config('DB_PORT', default='5432'),
            'CONN_MAX_AGE': 60,
            'OPTIONS': {
                'connect_timeout': 10,
            },
        }
    }
REDIS_URL = config('REDIS_URL', default='redis://redis:6379/0')

if DEBUG:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }
else:
    REDIS_URL = config('REDIS_URL', default='redis://redis:6379/0')

    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': REDIS_URL,
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            },
        }
    }

if DEBUG:
    SESSION_ENGINE = 'django.contrib.sessions.backends.db'
else:
    SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
    SESSION_CACHE_ALIAS = 'default'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Nairobi'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── REST FRAMEWORK ───────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
        'apps.api.throttles.RepaymentRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '20/minute',
        'user': '200/minute',
        'repayment': '30/minute',
        'repayment_burst': '5/second',
    },
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'apps.api.pagination.StandardResultsPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'apps.api.exceptions.custom_exception_handler',
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=8),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': config('JWT_SECRET_KEY', default=SECRET_KEY),
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# ── CELERY ───────────────────────────────────────────────────
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default=REDIS_URL)
CELERY_RESULT_BACKEND = 'django-db'
CELERY_CACHE_BACKEND = 'django-cache'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_MAX_TASKS_PER_CHILD = 100
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# Defined inline — avoids circular import from celery_beat.py
CELERY_BEAT_SCHEDULE = {
    'cleanup-expired-idempotency-keys': {
        'task': 'apps.repayments.tasks.cleanup_expired_idempotency_keys',
        'schedule': crontab(hour=2, minute=0),  # 2 AM EAT daily
        'options': {'expires': 3600},
    },
}


# ── LMS ──────────────────────────────────────────────────────
LMS_BASE_URL             = config('LMS_BASE_URL', default='https://back.dimeapp.co.ke')
LMS_CONSUMER_KEY         = config('LMS_CONSUMER_KEY', default='')#use only this
LMS_CONSUMER_SECRET      = config('LMS_CONSUMER_SECRET', default='')#use only this to fetch token from lms
LMS_TOKEN_URL            = config('LMS_TOKEN_URL', default='https://back.dimeapp.co.ke/api/partner/token/')
LMS_SERVICE_USERNAME     = config('LMS_SERVICE_USERNAME', default='')
LMS_SERVICE_PASSWORD     = config('LMS_SERVICE_PASSWORD', default='')
LMS_TIMEOUT              = config('LMS_TIMEOUT', default=60, cast=int)
LMS_RETRY_MAX            = config('LMS_RETRY_MAX', default=3, cast=int)
LMS_RETRY_BACKOFF        = config('LMS_RETRY_BACKOFF', default=2, cast=int)
LMS_REQUEST_POOL_SIZE    = config('LMS_REQUEST_POOL_SIZE', default=10, cast=int)
LMS_REQUEST_POOL_MAXSIZE = config('LMS_REQUEST_POOL_MAXSIZE', default=20, cast=int)
IDEMPOTENCY_KEY_TTL      = 60 * 60 * 24  # 24 hours

# ── CORS ─────────────────────────────────────────────────────
CORS_ALLOWED_ORIGINS = config(
    'CORS_ALLOWED_ORIGINS',
    default='https://hr.dimeapp.co.ke,http://localhost:5001',
    cast=Csv()
)
CORS_ALLOW_CREDENTIALS = True

# ── AXES ─────────────────────────────────────────────────────
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = ['username', 'ip_address']
AXES_RESET_ON_SUCCESS = True
AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# ── UNFOLD ADMIN ─────────────────────────────────────────────
UNFOLD = {
    'SITE_TITLE': 'Dime HR Payroll',
    'SITE_HEADER': 'Dime HR Payroll',
    'SITE_URL': '/',
    'SITE_SYMBOL': 'account_balance',
    'SHOW_HISTORY': True,
    'SHOW_VIEW_ON_SITE': False,
    'COLORS': {
        'primary': {
            '50': '240 249 255',
            '100': '224 242 254',
            '200': '186 230 253',
            '300': '125 211 252',
            '400': '56 189 248',
            '500': '14 165 233',
            '600': '2 132 199',
            '700': '3 105 161',
            '800': '7 89 133',
            '900': '12 74 110',
            '950': '8 47 73',
        },
    },
    'SIDEBAR': {
        'show_search': True,
        'show_all_applications': True,
        'navigation': [
            {
                'title': 'Organizations',
                'separator': True,
                'items': [
                    {
                        'title': 'Checkoff Organizations',
                        'icon': 'business',
                        'link': '/admin/organizations/checkofforganizationmirror/',
                    },
                    {
                        'title': 'HR Users',
                        'icon': 'manage_accounts',
                        'link': '/admin/organizations/hruser/',
                    },
                ],
            },
            {
                'title': 'Payroll',
                'separator': True,
                'items': [
                    {
                        'title': 'Payroll Uploads',
                        'icon': 'upload_file',
                        'link': '/admin/payroll/payrollupload/',
                    },
                    {
                        'title': 'Salary Deductions',
                        'icon': 'payments',
                        'link': '/admin/payroll/salarydeduction/',
                    },
                ],
            },
            {
                'title': 'Repayments',
                'separator': True,
                'items': [
                    {
                        'title': 'Repayment Batches',
                        'icon': 'receipt_long',
                        'link': '/admin/repayments/repaymentbatch/',
                    },
                    {
                        'title': 'Repayment Records',
                        'icon': 'paid',
                        'link': '/admin/repayments/repaymentrecord/',
                    },
                    {
                        'title': 'Idempotency Keys',
                        'icon': 'key',
                        'link': '/admin/repayments/idempotencykey/',
                    },
                ],
            },
            {
                'title': 'Loan Requests',
                'separator': True,
                'items': [
                    {
                        'title': 'Loan Uploads',
                        'icon': 'upload_file',
                        'link': '/admin/loans/loanrequestupload/',
                    },
                    {
                        'title': 'Loan Batches',
                        'icon': 'batch_prediction',
                        'link': '/admin/loans/loanrequestbatch/',
                    },
                    {
                        'title': 'Loan Requests',
                        'icon': 'request_quote',
                        'link': '/admin/loans/loanrequest/',
                    },
                ],
            },
        ],
    },
}

# ── SPECTACULAR ──────────────────────────────────────────────
SPECTACULAR_SETTINGS = {
    'TITLE': 'Dime HR Payroll API',
    'DESCRIPTION': 'HR Payroll integration service for Dime Loans LMS.',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
}

# ── LOGGING ──────────────────────────────────────────────────
os.makedirs(BASE_DIR / 'logs', exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'verbose'},
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': BASE_DIR / 'logs' / 'hr_payroll.log',
            'maxBytes': 1024 * 1024 * 10,
            'backupCount': 5,
            'formatter': 'verbose',
        },
    },
    'root': {'handlers': ['console'], 'level': 'INFO'},
    'loggers': {
        'django': {'handlers': ['console', 'file'], 'level': 'WARNING', 'propagate': False},
        'apps': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'celery': {'handlers': ['console', 'file'], 'level': 'INFO', 'propagate': False},
    },
}

# ── SECURITY (production only) ───────────────────────────────
if not DEBUG:
    # Nginx handles HTTP to HTTPS redirect at the edge, so we don't need it here
    # SECURE_SSL_REDIRECT = False  (handled by nginx)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    X_FRAME_OPTIONS = 'DENY'

# Trust the X-Forwarded-Proto header from nginx (since we're behind a proxy)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')