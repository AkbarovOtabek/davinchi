"""Настройки Django-проекта «Давинчи»."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Минимальный .env-загрузчик, чтобы не тянуть лишнюю зависимость."""
    if not path.exists():
        return
    import os

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")

import os  # noqa: E402  (после загрузки .env)


def env(key: str, default=None, cast=str):
    value = os.environ.get(key)
    if value is None or value == "":
        return default
    if cast is bool:
        return value.lower() in {"1", "true", "yes", "on"}
    if cast is list:
        return [item.strip() for item in value.split(",") if item.strip()]
    return cast(value)


SECRET_KEY = env("SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = env("DEBUG", True, bool)
ALLOWED_HOSTS = env("ALLOWED_HOSTS", ["127.0.0.1", "localhost"], list)
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS", [], list)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "matcher",
    "userbot",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

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

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "data" / "db.sqlite3",
        "OPTIONS": {"timeout": 20},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = env("TIME_ZONE", "Asia/Tashkent")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "data"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_PERMISSION_CLASSES": ["matcher.permissions.OptionalTokenPermission"],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

# Необязательный токен для защиты API: если задан, требуется заголовок X-API-Token.
API_TOKEN = env("API_TOKEN", "")

# ---------------------------------------------------------------- OpenAI ----
OPENAI_API_KEY = env("OPENAI_API_KEY", "")
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-6-luna")
OPENAI_TEMPERATURE = env("OPENAI_TEMPERATURE", 0.95, float)
OPENAI_TIMEOUT = env("OPENAI_TIMEOUT", 45, int)

# -------------------------------------------------------------- Telegram ----
TG_API_ID = env("TG_API_ID", 0, int)
TG_API_HASH = env("TG_API_HASH", "")
TG_PHONE = env("TG_PHONE", "")
TG_SESSION = str(BASE_DIR / env("TG_SESSION", "data/davinchi.session"))
TG_TARGET_BOT = env("TG_TARGET_BOT", "leomatchbot")
TG_NOTIFY_CHAT = env("TG_NOTIFY_CHAT", "me")

PHOTO_DIR = BASE_DIR / "data" / "photos"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(asctime)s %(levelname)-7s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(BASE_DIR / "logs" / "davinchi.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "encoding": "utf-8",
            "formatter": "simple",
        },
    },
    "root": {"handlers": ["console", "file"], "level": "INFO"},
    "loggers": {
        "telethon": {"level": "WARNING"},
        "httpx": {"level": "WARNING"},
    },
}
