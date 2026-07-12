"""
표준 설정 — 모든 값은 환경변수로 주입한다 (.env.example 참고).
로컬 개발은 기본값만으로 동작하고, 프로덕션은 compose 의 env 로 덮어쓴다.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

# ── 기본 ─────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-key")
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

# SERVICE_DOMAIN 하나로 호스트/CSRF 를 함께 처리한다 (예: myapp.example.com).
# Coolify 는 SERVICE_ 접두사를 예약하므로 직접 설정이 안 될 수 있다 —
# 그 경우 Coolify 가 자동 주입하는 SERVICE_FQDN_APP 을 폴백으로 사용한다.
SERVICE_DOMAIN = (
    os.environ.get("SERVICE_DOMAIN")
    or os.environ.get("SERVICE_FQDN_APP")
    or "localhost"
)
ALLOWED_HOSTS = [SERVICE_DOMAIN, "localhost", "127.0.0.1"]
CSRF_TRUSTED_ORIGINS = [f"https://{SERVICE_DOMAIN}"]

# Cloudflare Tunnel 뒤에서 TLS 는 엣지에서 종료된다
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # HSTS 헤더는 브라우저가 TLS 연결에서만 존중하므로 LAN http 접근에 무해
    SECURE_HSTS_SECONDS = 2592000  # 30일
    # SECURE_SSL_REDIRECT 는 의도적으로 끈다 — TLS 를 Cloudflare 가 종료하므로
    # 앱 레벨 https 리다이렉트는 프록시 체인에서 무한루프를 만든다 (W008 무시)

# ── 앱 ───────────────────────────────────────────────────────────
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "djust",
    "axes",  # /admin 브루트포스 방어
    "app",
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
    "axes.middleware.AxesMiddleware",
]

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# WebSocket 으로 mount 할 수 있는 LiveView 모듈 화이트리스트 (fail-closed)
LIVEVIEW_ALLOWED_MODULES = ["app.views"]

ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

# ── DB: SQLite 표준 (WAL + busy_timeout, /data 볼륨에 저장) ──────
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
        "OPTIONS": {
            # Django 5.1+
            "transaction_mode": "IMMEDIATE",
            "init_command": (
                "PRAGMA journal_mode=WAL;"
                "PRAGMA synchronous=NORMAL;"
                "PRAGMA busy_timeout=5000;"
                "PRAGMA cache_size=-20000;"
            ),
        },
    }
}

# ── Redis: djust 세션 상태 + Channels 레이어 ─────────────────────
REDIS_URL = os.environ.get("REDIS_URL", "")

if REDIS_URL:
    _redis = urlparse(REDIS_URL)
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [(_redis.hostname, _redis.port or 6379)]},
        }
    }
else:
    # 로컬 개발 전용 — 워커 1개일 때만 안전
    CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }

# djust 는 REDIS_URL 환경변수를 직접 읽어 상태 백엔드를 구성한다.
# 프록시(Cloudflare/Traefik) 뒤에서 클라이언트 IP 를 신뢰하기 위한 설정.
DJUST_TRUSTED_PROXIES = [
    p.strip()
    for p in os.environ.get("DJUST_TRUSTED_PROXIES", "").split(",")
    if p.strip()
]

# WebSocket 의 IP 별 연결 제한이 진짜 클라이언트 IP 를 보도록,
# X-Forwarded-For 에서 오른쪽부터 벗겨낼 신뢰 프록시 홉 수.
# 표준 체인: Cloudflare 엣지 → cloudflared → Traefik = 2
# (0 이면 소켓 피어(프록시 IP)를 그대로 사용 — 프록시 뒤에서는 전 사용자가
#  같은 IP 로 보여 rate limit 이 오작동한다)
DJUST_TRUSTED_PROXY_COUNT = int(
    os.environ.get("DJUST_TRUSTED_PROXY_COUNT", "0") or 0
)


def _client_ip_from_forwarded(request):
    """axes 브루트포스 방어가 프록시 IP 가 아닌 실제 클라이언트 IP 로 잠그도록
    djust 와 같은 신뢰 홉 수로 X-Forwarded-For 를 해석한다."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    chain = [p.strip() for p in xff.split(",") if p.strip()]
    n = DJUST_TRUSTED_PROXY_COUNT
    if n > 0 and len(chain) >= n:
        return chain[-n]
    return request.META.get("REMOTE_ADDR")


AXES_CLIENT_IP_CALLABLE = _client_ip_from_forwarded

# ── 정적 파일 (WhiteNoise) ──────────────────────────────────────
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# ── 기타 ─────────────────────────────────────────────────────────
LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
