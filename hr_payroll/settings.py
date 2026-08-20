import os
from pathlib import Path
from datetime import timedelta
from celery.schedules import crontab

BASE_DIR=Path(__file__).resolve().parent.parent

def env_bool(name,default=False):
    value=os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("true","1","yes","y","on")

def env_int(name,default=0):
    value=os.environ.get(name)
    if value is None or value=="":
        return default
    try:
        return int(value)
    except (TypeError,ValueError):
        return default

def env_list(name,default=""):
    value=os.environ.get(name,default)
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]

SECRET_KEY=os.environ.get("SECRET_KEY","change-me-in-production")
DEBUG = os.environ.get('DEBUG', 'True') == 'True'
ALLOWED_HOSTS=env_list("ALLOWED_HOSTS","hr.dimeapp.co.ke,localhost,127.0.0.1")
AUTH_USER_MODEL="users.User"

DJANGO_APPS=[
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",
    "unfold.contrib.import_export",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_filters",
    "axes",
    "users",
]

THIRD_PARTY_APPS=[
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "drf_spectacular",
    "django_celery_beat",
    "django_celery_results",
    "import_export",
    "django_redis",
]

LOCAL_APPS=[
    "apps.base",
    "apps.organizations",
    "apps.payroll",
    "apps.repayments",
    "apps.api",
    "apps.loans",
    "apps.customers",
]

INSTALLED_APPS=DJANGO_APPS+THIRD_PARTY_APPS+LOCAL_APPS

MIDDLEWARE=[
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "apps.api.exception_middleware.DisallowedHostMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "axes.middleware.AxesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.api.middleware.OrganizationIsolationMiddleware",
]

ROOT_URLCONF="hr_payroll.urls"
WSGI_APPLICATION="hr_payroll.wsgi.application"

TEMPLATES=[{
    "BACKEND":"django.template.backends.django.DjangoTemplates",
    "DIRS":[BASE_DIR/"templates"],
    "APP_DIRS":True,
    "OPTIONS":{
        "context_processors":[
            "django.template.context_processors.debug",
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ],
    },
}]

if DEBUG:
    DATABASES={
        "default":{
            "ENGINE":"django.db.backends.sqlite3",
            "NAME":BASE_DIR/"db.sqlite3",
        }
    }
else:
    DATABASES={
        "default":{
            "ENGINE":"django.db.backends.postgresql",
            "NAME":os.environ.get("DB_NAME","hr_payroll"),
            "USER":os.environ.get("DB_USER","hr_payroll_user"),
            "PASSWORD":os.environ.get("DB_PASSWORD",""),
            "HOST":os.environ.get("DB_HOST","localhost"),
            "PORT":os.environ.get("DB_PORT","5432"),
            "CONN_MAX_AGE":60,
            "OPTIONS":{"connect_timeout":10},
        }
    }

REDIS_URL=os.environ.get("REDIS_URL","redis://redis:6379/0")

if DEBUG:
    CACHES={
        "default":{
            "BACKEND":"django.core.cache.backends.locmem.LocMemCache",
        }
    }
else:
    CACHES={
        "default":{
            "BACKEND":"django_redis.cache.RedisCache",
            "LOCATION":REDIS_URL,
            "OPTIONS":{
                "CLIENT_CLASS":"django_redis.client.DefaultClient",
            },
        }
    }

if DEBUG:
    SESSION_ENGINE="django.contrib.sessions.backends.db"
else:
    SESSION_ENGINE="django.contrib.sessions.backends.cache"
    SESSION_CACHE_ALIAS="default"

AUTH_PASSWORD_VALIDATORS=[
    {
        "NAME":"django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME":"django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS":{"min_length":8},
    },
    {
        "NAME":"django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME":"django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE="en-us"
TIME_ZONE="Africa/Nairobi"
USE_I18N=True
USE_TZ=True

STATIC_URL="/static/"
STATIC_ROOT=BASE_DIR/"staticfiles"
STATICFILES_STORAGE="whitenoise.storage.CompressedManifestStaticFilesStorage"
MEDIA_URL="/media/"
MEDIA_ROOT=BASE_DIR/"media"
DEFAULT_AUTO_FIELD="django.db.models.BigAutoField"

REST_FRAMEWORK={
    "DEFAULT_AUTHENTICATION_CLASSES":[
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES":[
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES":[
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "apps.api.throttles.RepaymentRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES":{
        "anon":"20/minute",
        "user":"200/minute",
        "repayment":"30/minute",
        "repayment_burst":"5/second",
    },
    "DEFAULT_FILTER_BACKENDS":[
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS":"apps.api.pagination.StandardResultsPagination",
    "PAGE_SIZE":50,
    "DEFAULT_SCHEMA_CLASS":"drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER":"apps.api.exceptions.custom_exception_handler",
}

SIMPLE_JWT={
    "ACCESS_TOKEN_LIFETIME":timedelta(hours=8),
    "REFRESH_TOKEN_LIFETIME":timedelta(days=1),
    "ROTATE_REFRESH_TOKENS":True,
    "BLACKLIST_AFTER_ROTATION":True,
    "UPDATE_LAST_LOGIN":True,
    "ALGORITHM":"HS256",
    "SIGNING_KEY":os.environ.get("JWT_SECRET_KEY",SECRET_KEY),
    "AUTH_HEADER_TYPES":("Bearer",),
}

CELERY_BROKER_URL=os.environ.get("CELERY_BROKER_URL",REDIS_URL)
CELERY_RESULT_BACKEND="django-db"
CELERY_CACHE_BACKEND="django-cache"
CELERY_ACCEPT_CONTENT=["json"]
CELERY_TASK_SERIALIZER="json"
CELERY_RESULT_SERIALIZER="json"
CELERY_TIMEZONE=TIME_ZONE
CELERY_TASK_TRACK_STARTED=True
CELERY_TASK_TIME_LIMIT=30*60
CELERY_TASK_SOFT_TIME_LIMIT=25*60
CELERY_WORKER_PREFETCH_MULTIPLIER=1
CELERY_TASK_ACKS_LATE=True
CELERY_WORKER_MAX_TASKS_PER_CHILD=100
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP=True

CELERY_BEAT_SCHEDULE={
    "cleanup-expired-idempotency-keys":{
        "task":"apps.repayments.tasks.cleanup_expired_idempotency_keys",
        "schedule":crontab(hour=2,minute=0),
        "options":{"expires":3600},
    },
}

LMS_BASE_URL=os.environ.get("LMS_BASE_URL","https://back.dimeapp.co.ke")
LMS_CONSUMER_KEY=os.environ.get("LMS_CONSUMER_KEY","")
LMS_CONSUMER_SECRET=os.environ.get("LMS_CONSUMER_SECRET","")
LMS_TOKEN_URL=os.environ.get("LMS_TOKEN_URL","https://back.dimeapp.co.ke/api/partner/token/")
LMS_SERVICE_USERNAME=os.environ.get("LMS_SERVICE_USERNAME","")
LMS_SERVICE_PASSWORD=os.environ.get("LMS_SERVICE_PASSWORD","")
LMS_TIMEOUT=env_int("LMS_TIMEOUT",60)
LMS_RETRY_MAX=env_int("LMS_RETRY_MAX",3)
LMS_RETRY_BACKOFF=env_int("LMS_RETRY_BACKOFF",2)
LMS_REQUEST_POOL_SIZE=env_int("LMS_REQUEST_POOL_SIZE",10)
LMS_REQUEST_POOL_MAXSIZE=env_int("LMS_REQUEST_POOL_MAXSIZE",20)
IDEMPOTENCY_KEY_TTL=60*60*24

CORS_ALLOWED_ORIGINS=env_list(
    "CORS_ALLOWED_ORIGINS",
    "https://hr.dimeapp.co.ke,http://localhost:5001,https://payroll.dimeapp.co.ke"
)
CORS_ALLOW_CREDENTIALS=True

AXES_FAILURE_LIMIT=5
AXES_COOLOFF_TIME=timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS=["username","ip_address"]
AXES_RESET_ON_SUCCESS=True

AUTHENTICATION_BACKENDS=[
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

UNFOLD={
    "SITE_TITLE":"Dime HR Payroll",
    "SITE_HEADER":"Dime HR Payroll",
    "SITE_URL":"/",
    "SITE_SYMBOL":"account_balance",
    "SHOW_HISTORY":True,
    "SHOW_VIEW_ON_SITE":False,
    "COLORS":{
        "primary":{
            "50":"240 249 255",
            "100":"224 242 254",
            "200":"186 230 253",
            "300":"125 211 252",
            "400":"56 189 248",
            "500":"14 165 233",
            "600":"2 132 199",
            "700":"3 105 161",
            "800":"7 89 133",
            "900":"12 74 110",
            "950":"8 47 73",
        },
    },
    "SIDEBAR":{
        "show_search":True,
        "show_all_applications":True,
        "navigation":[
            {
                "title":"Organizations",
                "separator":True,
                "items":[
                    {
                        "title":"Checkoff Organizations",
                        "icon":"business",
                        "link":"/admin/organizations/checkofforganizationmirror/",
                    },
                    {
                        "title":"HR Users",
                        "icon":"manage_accounts",
                        "link":"/admin/organizations/hruser/",
                    },
                ],
            },
            {
                "title":"Payroll",
                "separator":True,
                "items":[
                    {
                        "title":"Payroll Uploads",
                        "icon":"upload_file",
                        "link":"/admin/payroll/payrollupload/",
                    },
                    {
                        "title":"Salary Deductions",
                        "icon":"payments",
                        "link":"/admin/payroll/salarydeduction/",
                    },
                ],
            },
            {
                "title":"Repayments",
                "separator":True,
                "items":[
                    {
                        "title":"Repayment Batches",
                        "icon":"receipt_long",
                        "link":"/admin/repayments/repaymentbatch/",
                    },
                    {
                        "title":"Repayment Records",
                        "icon":"paid",
                        "link":"/admin/repayments/repaymentrecord/",
                    },
                    {
                        "title":"Idempotency Keys",
                        "icon":"key",
                        "link":"/admin/repayments/idempotencykey/",
                    },
                ],
            },
            {
                "title":"Loan Requests",
                "separator":True,
                "items":[
                    {
                        "title":"Loan Uploads",
                        "icon":"upload_file",
                        "link":"/admin/loans/loanrequestupload/",
                    },
                    {
                        "title":"Loan Batches",
                        "icon":"batch_prediction",
                        "link":"/admin/loans/loanrequestbatch/",
                    },
                    {
                        "title":"Loan Requests",
                        "icon":"request_quote",
                        "link":"/admin/loans/loanrequest/",
                    },
                ],
            },
            {
                "title":"Customer Registrations",
                "separator":True,
                "items":[
                    {
                        "title":"Customer Registrations",
                        "icon":"badge",
                        "link":"/admin/customers/customerregistration/",
                    },
                ],
            },
        ],
    },
}

SPECTACULAR_SETTINGS={
    "TITLE":"Dime HR Payroll API",
    "DESCRIPTION":"HR Payroll integration service for Dime Loans LMS.",
    "VERSION":"1.0.0",
    "SERVE_INCLUDE_SCHEMA":False,
    "COMPONENT_SPLIT_REQUEST":True,
}

os.makedirs(BASE_DIR/"logs",exist_ok=True)

LOGGING={
    "version":1,
    "disable_existing_loggers":False,
    "formatters":{
        "verbose":{
            "format":"{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style":"{",
        },
    },
    "handlers":{
        "console":{
            "class":"logging.StreamHandler",
            "formatter":"verbose",
        },
        "file":{
            "class":"logging.handlers.RotatingFileHandler",
            "filename":BASE_DIR/"logs"/"hr_payroll.log",
            "maxBytes":1024*1024*10,
            "backupCount":5,
            "formatter":"verbose",
        },
    },
    "root":{
        "handlers":["console"],
        "level":"INFO",
    },
    "loggers":{
        "django":{
            "handlers":["console","file"],
            "level":"WARNING",
            "propagate":False,
        },
        "apps":{
            "handlers":["console","file"],
            "level":"DEBUG" if DEBUG else "INFO",
            "propagate":False,
        },
        "celery":{
            "handlers":["console","file"],
            "level":"INFO",
            "propagate":False,
        },
    },
}

if not DEBUG:
    SESSION_COOKIE_SECURE=True
    CSRF_COOKIE_SECURE=True
    SECURE_BROWSER_XSS_FILTER=True
    SECURE_CONTENT_TYPE_NOSNIFF=True
    SECURE_HSTS_SECONDS=31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS=True
    SECURE_HSTS_PRELOAD=True
    X_FRAME_OPTIONS="DENY"

SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO","https")
