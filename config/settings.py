"""Local development defaults; configure PostgreSQL and HTTPS for deployment."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = BASE_DIR / '.runtime'
RUNTIME_DIR.mkdir(exist_ok=True, mode=0o700)
SECRET_KEY = os.environ.get('CLINIC_SECRET_KEY')
if not SECRET_KEY:
    key_file = RUNTIME_DIR / 'secret.key'
    try:
        with key_file.open('x', encoding='utf-8') as stream:
            stream.write(secrets.token_urlsafe(64))
        key_file.chmod(0o600)
    except FileExistsError:
        pass
    SECRET_KEY = key_file.read_text(encoding='utf-8').strip()

DEBUG = os.environ.get('CLINIC_DEBUG', '0') == '1'
ALLOWED_HOSTS = os.environ.get('CLINIC_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1]').split(',')
RENDER_HOST = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if RENDER_HOST:
    ALLOWED_HOSTS.append(RENDER_HOST)
CSRF_TRUSTED_ORIGINS = [f'https://{RENDER_HOST}'] if RENDER_HOST else []
CLINIC_DEMO_MODE = os.environ.get('CLINIC_DEMO_MODE', '0') == '1'
INSTALLED_APPS = [
    'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions',
    'django.contrib.messages', 'django.contrib.staticfiles', 'clinic',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
        'clinic.context_processors.deployment',
    ]},
}]
WSGI_APPLICATION = 'config.wsgi.application'
if os.environ.get('CLINIC_DB_ENGINE', 'sqlite') == 'postgresql':
    DATABASES = {'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('CLINIC_DB_NAME', 'clinic'),
        'USER': os.environ.get('CLINIC_DB_USER', 'clinic'),
        'PASSWORD': os.environ.get('CLINIC_DB_PASSWORD', ''),
        'HOST': os.environ.get('CLINIC_DB_HOST', '127.0.0.1'),
        'PORT': os.environ.get('CLINIC_DB_PORT', '5432'),
    }}
else:
    # Single-process development only; the clinic LAN deployment uses PostgreSQL.
    DATABASES = {'default': {
        'ENGINE': 'django.db.backends.sqlite3', 'NAME': RUNTIME_DIR / 'development.sqlite3',
    }}
AUTH_USER_MODEL = 'clinic.User'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Karachi'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 3600
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_SECURE = os.environ.get('CLINIC_HTTPS', '0') == '1'
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE
SECURE_SSL_REDIRECT = CSRF_COOKIE_SECURE
# Enable proxy trust only on Render, whose ingress supplies this header.
if RENDER_HOST:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
