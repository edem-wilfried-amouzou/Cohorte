import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> None:
    """Mini-chargeur de .env (évite une dépendance supplémentaire)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY est obligatoire quand DJANGO_DEBUG=0.")

render_hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
if render_hostname and render_hostname not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(render_hostname)
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
if render_hostname and f"https://{render_hostname}" not in CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS.append(f"https://{render_hostname}")

# URL de l'admin configurable (évite /admin/ par défaut). Ex : DJANGO_ADMIN_URL=gestion-x7k
ADMIN_URL = os.environ.get("DJANGO_ADMIN_URL", "admin").strip("/") + "/"

# Code d'invitation facultatif : si défini, l'inscription le demande.
SIGNUP_INVITE_CODE = os.environ.get("SIGNUP_INVITE_CODE", "").strip()

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.accounts",
    "apps.groupes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.groupes.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


if os.environ.get("DATABASE_URL"):
    database_url = os.environ["DATABASE_URL"]
    db_conn_max_age = max(0, int(os.environ.get("DB_CONN_MAX_AGE", "60")))
    DATABASES = {
        "default": dj_database_url.parse(
            database_url,
            conn_max_age=db_conn_max_age,
            ssl_require=not DEBUG and database_url.lower().startswith(("postgres://", "postgresql://")),
        )
    }
    DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
elif os.environ.get("POSTGRES_DB"):
    db_conn_max_age = max(0, int(os.environ.get("DB_CONN_MAX_AGE", "60")))
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB"),
            "USER": os.environ.get("POSTGRES_USER", "postgres"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": db_conn_max_age,
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {"sslmode": os.environ.get("POSTGRES_SSLMODE", "require")},
        }
    }
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fr"
TIME_ZONE = "Africa/Lome"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STATIC_ROOT.mkdir(exist_ok=True)
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))
USE_R2_STORAGE = env_bool("USE_R2_STORAGE", False)

if USE_R2_STORAGE:
    r2_settings = {
        "R2_ACCOUNT_ID": os.environ.get("R2_ACCOUNT_ID", "").strip(),
        "R2_BUCKET_NAME": os.environ.get("R2_BUCKET_NAME", "").strip(),
        "R2_ACCESS_KEY_ID": os.environ.get("R2_ACCESS_KEY_ID", "").strip(),
        "R2_SECRET_ACCESS_KEY": os.environ.get("R2_SECRET_ACCESS_KEY", "").strip(),
    }
    missing_r2_settings = [name for name, value in r2_settings.items() if not value]
    if missing_r2_settings:
        raise ImproperlyConfigured(
            "USE_R2_STORAGE=1 demande les variables : " + ", ".join(missing_r2_settings)
        )

    default_file_storage = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": r2_settings["R2_BUCKET_NAME"],
            "endpoint_url": f"https://{r2_settings['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            "region_name": "auto",
            "access_key": r2_settings["R2_ACCESS_KEY_ID"],
            "secret_key": r2_settings["R2_SECRET_ACCESS_KEY"],
            "addressing_style": "path",
            "signature_version": "s3v4",
            "default_acl": None,
            "querystring_auth": True,
            "querystring_expire": 600,
            # document_upload_path génère un UUID : le HEAD préalable à chaque dépôt est inutile.
            "file_overwrite": True,
            "object_parameters": {"CacheControl": "private, no-store"},
        },
    }
else:
    default_file_storage = {"BACKEND": "django.core.files.storage.FileSystemStorage"}

STORAGES = {
    "default": default_file_storage,
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# Cache partagé entre workers (limiteur de requêtes). Table créée par `manage.py createcachetable`.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}

# Nombre de proxys de confiance devant l'app (Render = 1). 0 = utiliser REMOTE_ADDR.
TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "1" if os.environ.get("DJANGO_BEHIND_PROXY") == "1" else "0"))
# N'activer qu'en cas de trafic réellement relayé par Cloudflare (proxy orange).
TRUST_CLOUDFLARE_HEADERS = env_bool("TRUST_CLOUDFLARE_HEADERS", False)

# E-mail (réinitialisation du mot de passe). Sans EMAIL_HOST : affichage console.
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "465"))
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", True)
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "Cohorte <contact@django-admin.com>")
PASSWORD_RESET_TIMEOUT = 60 * 60  # 1 h

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "groupes:dashboard"
LOGOUT_REDIRECT_URL = "landing"

# Taille max de la requête (document 25 Mo + champs multipart) et seuil de mise sur disque temporaire.
DATA_UPLOAD_MAX_MEMORY_SIZE = 32 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# --- Sécurité -------------------------------------------------------------

if os.environ.get("DJANGO_BEHIND_PROXY") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if not DEBUG:
    SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "1") == "1"
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1") == "1"
    CSRF_COOKIE_SECURE = os.environ.get("CSRF_COOKIE_SECURE", "1") == "1"
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get("SECURE_HSTS_INCLUDE_SUBDOMAINS", "1") == "1"
    SECURE_HSTS_PRELOAD = os.environ.get("SECURE_HSTS_PRELOAD", "0") == "1"
    SECURE_CONTENT_TYPE_NOSNIFF = os.environ.get("SECURE_CONTENT_TYPE_NOSNIFF", "1") == "1"
X_FRAME_OPTIONS = "DENY"  # seuls les aperçus de fichiers autorisent explicitement SAMEORIGIN
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7  # 7 jours
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"django.security": {"handlers": ["console"], "level": "WARNING"}},
}


# --- Supervision (facultatif) ---------------------------------------------
SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()
if SENTRY_DSN:
    try:
        import sentry_sdk

        sentry_sdk.init(dsn=SENTRY_DSN, send_default_pii=False, traces_sample_rate=0.0)
    except ImportError:  # dépendance absente : on ignore
        pass

# Type MIME du manifeste PWA (sinon servi en application/octet-stream)
WHITENOISE_MIMETYPES = {".webmanifest": "application/manifest+json"}
