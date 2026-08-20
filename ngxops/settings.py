"""
Django settings for ngxops project.
"""

import os

from ngxops.runtime_paths import data_dir, is_frozen, resource_dir
from ngxops.secret_key import env_flag_true, load_or_create_secret_key, parse_csv_env

# 只读资源（模板等）与可写数据（库/media）分离，兼容 PyInstaller
RESOURCE_DIR = resource_dir()
DATA_DIR = data_dir()
# 兼容旧代码：BASE_DIR 指向可写数据根（db/media）；模板 DIRS 用 RESOURCE_DIR
BASE_DIR = DATA_DIR

SECRET_KEY = load_or_create_secret_key(DATA_DIR)
# 冻结交付默认关闭 DEBUG；可用环境变量打开
_debug_env = (os.environ.get("MNGXOPS_DEBUG") or "").strip().lower()
if _debug_env in ("1", "true", "yes", "on"):
    DEBUG = True
elif _debug_env in ("0", "false", "no", "off"):
    DEBUG = False
else:
    DEBUG = not is_frozen()

_allowed = parse_csv_env("MNGXOPS_ALLOWED_HOSTS")
if _allowed:
    ALLOWED_HOSTS = list(_allowed)
    for _host in ("127.0.0.1", "localhost"):
        if _host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(_host)
else:
    ALLOWED_HOSTS = ["*"]

CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1",
    "http://localhost",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "https://127.0.0.1",
    "https://localhost",
]
CSRF_TRUSTED_ORIGINS.extend(parse_csv_env("MNGXOPS_CSRF_TRUSTED_ORIGINS"))

_use_https = env_flag_true("MNGXOPS_HTTPS")
SESSION_COOKIE_SECURE = _use_https
CSRF_COOKIE_SECURE = _use_https
if _use_https:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.accounts",
    "apps.dashboard",
    "apps.users",
    "apps.credentials",
    "apps.nodes",
    "apps.configs",
    "apps.releases",
    "apps.audit",
    "apps.settings.apps.SettingsConfig",
    "apps.upgrade",
    "apps.nginx_service",
    "apps.nginx_install",
    "apps.nginx_uninstall",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.audit.middleware.CurrentUserMiddleware",
    "apps.audit.middleware.AjaxErrorMiddleware",
    "apps.settings.middleware.PartialResponseMiddleware",
    "apps.settings.middleware.DataRetentionMiddleware",
]

ROOT_URLCONF = "ngxops.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [RESOURCE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.settings.context_processors.system_runtime_settings",
                "apps.users.context_processors.perm_denied_alert",
            ],
        },
    },
]

WSGI_APPLICATION = "ngxops.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "apps.users.password_validation.CombinedSimilarityAndLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = DATA_DIR / "staticfiles"
STATICFILES_DIRS = [RESOURCE_DIR / "static"]
MEDIA_URL = "media/"
MEDIA_ROOT = DATA_DIR / "media"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:index"
LOGOUT_REDIRECT_URL = "accounts:login"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
