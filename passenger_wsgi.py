"""cPanel/CloudLinux Passenger entry point. Set entry point to application."""
import os
import sys
from pathlib import Path

application_root = Path(__file__).resolve().parent
sys.path.insert(0, str(application_root))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from django.core.wsgi import get_wsgi_application
_django_application = get_wsgi_application()


def application(environ, start_response):
    # Apache can provide HTTPS=on as server metadata. HTTP_HTTPS is a client
    # header and is deliberately not used. Preserve Passenger's URL scheme.
    if str(environ.get('HTTPS', '')).lower() in ['on', '1']:
        environ['wsgi.url_scheme'] = 'https'
    return _django_application(environ, start_response)
