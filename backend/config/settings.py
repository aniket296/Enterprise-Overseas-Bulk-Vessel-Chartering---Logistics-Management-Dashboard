"""
Django settings for Enterprise Overseas Bulk Vessel Chartering & Logistics Management Dashboard.
Problem Statement: SIH26006
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-sih26006-enterprise-vessel-logistics-secret-key-32chars",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "corsheaders",
    # Local Apps
    "apps.authentication.apps.AuthenticationConfig",
    "apps.contracts.apps.ContractsConfig",
    "apps.voyages.apps.VoyagesConfig",
    "apps.laytime",
    "apps.inventory.apps.InventoryConfig",
    "apps.documents.apps.DocumentsConfig",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.authentication.middleware.AuditSessionContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database Configuration
# Uses PostgreSQL with search_path=shipping,public if DB_HOST is configured,
# otherwise falls back to SQLite for isolated zero-dependency unit tests.
DB_ENGINE = os.environ.get("DB_ENGINE", "django.db.backends.sqlite3")
DB_HOST = os.environ.get("POSTGRES_HOST", os.environ.get("DB_HOST", ""))

if DB_HOST:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "vessel_chartering_db"),
            "USER": os.environ.get("POSTGRES_USER", "postgres"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "postgres"),
            "HOST": DB_HOST,
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "OPTIONS": {
                "options": "-c search_path=shipping,public",
            },
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "apps.authentication.permissions.RoleBasedAccessControl",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.MultiPartParser",
    ],
}

# RBAC Security Configuration
RBAC_REQUIRE_AUTHENTICATION = os.environ.get("RBAC_REQUIRE_AUTHENTICATION", "false").lower() == "true"
RBAC_DEFAULT_ANONYMOUS_ROLE = os.environ.get("RBAC_DEFAULT_ANONYMOUS_ROLE", "SYSTEM_ADMIN")

# CORS Configuration for Next.js Frontend
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

# Document Vault & Object Storage (MinIO / S3 / Local Air-Gapped)
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
MINIO_USE_SSL = os.environ.get("MINIO_USE_SSL", "false").lower() == "true"
MINIO_DEFAULT_BUCKET = os.environ.get("MINIO_DEFAULT_BUCKET", "shipping-documents")
DOCUMENTS_VAULT_LOCAL_DIR = BASE_DIR / "media" / "documents_vault"
DOCUMENTS_MAX_FILE_SIZE = 104857600  # 100 MB

# ==============================================================================
# Celery Broker & Worker Topology Setup (Cycle 2.4.1)
# Queue Segregation: calculations, documents, alerts, default
# ==============================================================================
from kombu import Queue, Exchange

CELERY_BROKER_URL = os.environ.get(
    "REDIS_URL",
    os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0"),
)
CELERY_RESULT_BACKEND = os.environ.get(
    "CELERY_RESULT_BACKEND",
    "redis://localhost:6379/1",
)

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True

# Worker Performance & Reliability
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240

# Air-Gapped Test Mode: execute synchronously without external Redis daemon when not explicitly running in production
CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_TASK_ALWAYS_EAGER", "true").lower() == "true"
CELERY_TASK_EAGER_PROPAGATES = True

# Exchanges
calculations_exchange = Exchange("calculations", type="direct")
documents_exchange = Exchange("documents", type="direct")
alerts_exchange = Exchange("alerts", type="direct")
default_exchange = Exchange("default", type="direct")

# Queue Topology
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_DEFAULT_EXCHANGE = "default"
CELERY_TASK_DEFAULT_ROUTING_KEY = "default.general"

CELERY_TASK_QUEUES = (
    Queue("calculations", calculations_exchange, routing_key="calculations.#"),
    Queue("documents", documents_exchange, routing_key="documents.#"),
    Queue("alerts", alerts_exchange, routing_key="alerts.#"),
    Queue("default", default_exchange, routing_key="default.#"),
)

# Route Configuration
CELERY_TASK_ROUTES = {
    # Calculations Queue (heavy batch laytime math & settlement)
    "apps.laytime.tasks.*": {"queue": "calculations", "routing_key": "calculations.laytime"},
    "apps.voyages.tasks.compute_*": {"queue": "calculations", "routing_key": "calculations.voyages"},
    "apps.voyages.tasks.recalculate_*": {"queue": "calculations", "routing_key": "calculations.voyages"},
    "calculations.*": {"queue": "calculations", "routing_key": "calculations.general"},

    # Documents Queue (SHA-256 verification & MinIO sync)
    "apps.documents.tasks.*": {"queue": "documents", "routing_key": "documents.vault"},
    "documents.*": {"queue": "documents", "routing_key": "documents.general"},

    # Alerts Queue (berth collision watchdog, ETA deviation, stock runway alerts)
    "apps.inventory.tasks.*": {"queue": "alerts", "routing_key": "alerts.inventory"},
    "apps.voyages.tasks.alert_*": {"queue": "alerts", "routing_key": "alerts.voyages"},
    "alerts.*": {"queue": "alerts", "routing_key": "alerts.general"},

    # Default fallback
    "config.celery.*": {"queue": "default", "routing_key": "default.general"},
}

# Celery Beat Scheduled Periodic Tasks (Autonomous operational watchdog monitors)
CELERY_BEAT_SCHEDULE = {
    "periodic-inventory-runway-watchdog": {
        "task": "apps.inventory.tasks.check_critical_stock_alerts_async",
        "schedule": 900.0,  # every 15 minutes
        "options": {"queue": "alerts", "routing_key": "alerts.inventory"},
    },
    "periodic-berth-conflict-watchdog": {
        "task": "apps.voyages.tasks.alert_active_berth_conflicts_async",
        "schedule": 900.0,  # every 15 minutes
        "options": {"queue": "alerts", "routing_key": "alerts.voyages"},
    },
}
