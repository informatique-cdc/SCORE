"""
Django settings for SCORE.

Reads .env file and config.yaml for configuration layering.
"""

import os
from pathlib import Path

import environ
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Environment ---
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    LLM_PROVIDER=(str, "openai"),
    CELERY_BROKER_BACKEND=(str, "redis"),
    CELERY_BROKER_URL=(str, "redis://localhost:6379/0"),
)
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

# --- YAML config ---
_config_path = BASE_DIR / "config.yaml"
APP_CONFIG: dict = {}
if _config_path.exists():
    with open(_config_path) as f:
        APP_CONFIG = yaml.safe_load(f) or {}

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# --- Security: reject weak SECRET_KEY in production ---
_INSECURE_SECRET_KEYS = {"change-me-in-production", "changeme", ""}
if not DEBUG and SECRET_KEY in _INSECURE_SECRET_KEYS:
    raise ValueError(
        "SECRET_KEY is insecure. Generate a proper key with: "
        'python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"'
    )

# --- Apps ---
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # Auth
    "allauth",
    "allauth.account",
    # "allauth.socialaccount",
    # "allauth.socialaccount.providers.microsoft",
    # Celery
    "django_celery_results",
    "django_celery_beat",
    # SCORE apps
    "tenants",
    "connectors",
    "ingestion",
    "vectorstore",
    "analysis",
    "reports",
    "dashboard",
    "chat",
]

SITE_ID = 1

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "score.middleware.ContentSecurityPolicyMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "tenants.middleware.TenantMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "score.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "dashboard" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "tenants.context_processors.tenant_context",
            ],
        },
    },
]

WSGI_APPLICATION = "score.wsgi.application"

# --- Runtime data directory ---
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# --- Database ---
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
    }
}
# For PostgreSQL in production, set CONN_MAX_AGE to enable persistent connections:
# CONN_MAX_AGE = 600  # seconds

# --- Auth ---
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/auth/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/auth/login/"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# --- Allauth ---
ACCOUNT_LOGIN_METHODS = {"email", "username"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "username*", "password1*", "password2*"]
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_EMAIL_VERIFICATION = "optional"
ACCOUNT_LOGIN_BY_CODE_ENABLED = False
ACCOUNT_LOGOUT_REDIRECT_URL = "/auth/login/"
ACCOUNT_ADAPTER = "tenants.adapters.ScoreAccountAdapter"

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# --- Session security ---
SESSION_COOKIE_AGE = 3600 * 8  # 8 hours
SESSION_SAVE_EVERY_REQUEST = True  # Reset expiry on each request
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# --- Production security hardening ---
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# --- i18n ---
LANGUAGE_CODE = "fr"
LANGUAGES = [
    ("fr", "Français"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static ---
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "dashboard" / "static"]
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Media ---
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"

# --- Celery ---
CELERY_BROKER_BACKEND = env("CELERY_BROKER_BACKEND")

if CELERY_BROKER_BACKEND == "database":
    # No-Redis dev mode: use SQLite as broker via SQLAlchemy transport
    CELERY_BROKER_URL = f"sqla+sqlite:///{DATA_DIR / 'celery_broker.sqlite3'}"
    CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
    # Override env var so Celery doesn't pick up the redis URL directly
    os.environ["CELERY_BROKER_URL"] = CELERY_BROKER_URL
else:
    CELERY_BROKER_URL = env("CELERY_BROKER_URL")

CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "django-cache"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 3600  # 1 hour hard limit
CELERY_TASK_SOFT_TIME_LIMIT = 3000
CELERY_WORKER_POOL = "threads"  # Avoid fork() on macOS ARM64 (breaks scipy/numpy longdouble)

# --- LLM Config ---
# .env provides only LLM_PROVIDER and API keys.
# All model/config settings come from config.yaml → llm:
LLM_PROVIDER = env("LLM_PROVIDER")
_llm_yaml = APP_CONFIG.get("llm", {})

LLM_CONFIG = {
    "provider": LLM_PROVIDER,
    "openai": {
        "api_key": env("OPENAI_API_KEY", default=""),
        "chat_model": _llm_yaml.get("chat_model", "gpt-4o"),
        "embedding_model": _llm_yaml.get("embedding_model", "text-embedding-3-small"),
        "embedding_dimensions": _llm_yaml.get("embedding_dimensions", 1536),
    },
    "azure": {
        "api_key": env("AZURE_OPENAI_API_KEY", default=""),
        "endpoint": env("AZURE_OPENAI_ENDPOINT", default=""),
        "api_version": env("AZURE_OPENAI_API_VERSION", default="2024-06-01"),
        "chat_deployment": _llm_yaml.get("chat_model", "gpt-4o"),
        "embedding_deployment": _llm_yaml.get("embedding_model", "text-embedding-3-small"),
        "embedding_endpoint": env("AZURE_OPENAI_EMBEDDING_ENDPOINT", default=""),
        "embedding_api_key": env("AZURE_OPENAI_EMBEDDING_API_KEY", default=""),
        "embedding_dimensions": _llm_yaml.get("embedding_dimensions", 1536),
    },
    "azure_mistral": {
        "api_key": env("AZURE_MISTRAL_API_KEY", default=""),
        "endpoint": env("AZURE_MISTRAL_ENDPOINT", default=""),
        "api_version": env("AZURE_MISTRAL_API_VERSION", default="2024-05-01-preview"),
        "deployment_name": env("AZURE_MISTRAL_DEPLOYMENT_NAME", default=""),
        "chat_model": _llm_yaml.get("chat_model", "mistral-large-latest"),
    },
    "requests_per_minute": _llm_yaml.get("requests_per_minute", 60),
    "embedding_batch_size": _llm_yaml.get("embedding_batch_size", 100),
    "fallback_models": _llm_yaml.get("fallback_models", []),
    "fallback_retries_per_model": _llm_yaml.get("fallback_retries_per_model", 2),
    "batch_model": _llm_yaml.get("batch_model", ""),
    "batch_poll_interval_seconds": _llm_yaml.get("batch_poll_interval_seconds", 30),
    "batch_max_wait_seconds": _llm_yaml.get("batch_max_wait_seconds", 1800),
}

# --- Analysis thresholds (merge env overrides into YAML) ---
ANALYSIS_CONFIG = APP_CONFIG.get("analysis", {})
CHUNKING_CONFIG = APP_CONFIG.get("chunking", {})
AUTHORITY_RULES = APP_CONFIG.get("authority_rules", {})

# Env overrides for key thresholds
_dup = ANALYSIS_CONFIG.get("duplicate", {})
_dup["semantic_threshold"] = env.float(
    "DUPLICATE_SEMANTIC_THRESHOLD", default=_dup.get("semantic_threshold", 0.92)
)
_dup["combined_threshold"] = env.float(
    "DUPLICATE_COMBINED_THRESHOLD", default=_dup.get("combined_threshold", 0.80)
)
ANALYSIS_CONFIG["duplicate"] = _dup

_contra = ANALYSIS_CONFIG.get("contradiction", {})
_contra["confidence_threshold"] = env.float(
    "CONTRADICTION_CONFIDENCE_THRESHOLD", default=_contra.get("confidence_threshold", 0.75)
)
_contra["similarity_threshold"] = env.float(
    "CONTRADICTION_SIMILARITY_THRESHOLD", default=_contra.get("similarity_threshold", 0.70)
)
_contra["max_neighbors"] = env.int(
    "CONTRADICTION_MAX_NEIGHBORS", default=_contra.get("max_neighbors", 10)
)
ANALYSIS_CONFIG["contradiction"] = _contra

ANALYSIS_CONFIG["use_batch_api"] = env.bool(
    "ANALYSIS_USE_BATCH_API", default=ANALYSIS_CONFIG.get("use_batch_api", False)
)

# --- Semantic graph config ---
SEMANTIC_GRAPH_CONFIG = APP_CONFIG.get("semantic_graph", {})

# --- Audit config ---
AUDIT_CONFIG = APP_CONFIG.get("audit", {})

# --- Vector dimensions ---
EMBEDDING_DIMENSIONS = _llm_yaml.get("embedding_dimensions", 1536)

# --- Logging ---
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "score": {"level": "DEBUG" if DEBUG else "INFO"},
        "celery": {"level": "INFO"},
        # Never log document content
        "ingestion.extraction": {"level": "WARNING"},
    },
}
