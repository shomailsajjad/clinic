"""Windows-compatible application server. Defaults to loopback for development."""
import os
import ipaddress

from waitress import serve
from config.wsgi import application
from django.conf import settings

if __name__ == '__main__':
    proxy_options = {}
    if os.environ.get('RENDER_EXTERNAL_HOSTNAME'):
        # Render exposes the service through its managed ingress. Waitress must
        # retain its HTTPS indication to avoid an endless HTTPS redirect.
        proxy_options = {'trusted_proxy': '*', 'trusted_proxy_headers': {'x-forwarded-proto'}}
    elif os.environ.get('CLINIC_PROXY_IP'):
        trusted_ip = str(ipaddress.ip_address(os.environ['CLINIC_PROXY_IP']))
        proxy_options = {'trusted_proxy': trusted_ip, 'trusted_proxy_headers': {'x-forwarded-proto'}}
    serve(application, host=os.environ.get('CLINIC_BIND_HOST', settings.NODE_CONFIG.get('bind_host', '127.0.0.1')),
          port=int(os.environ.get('CLINIC_PORT', os.environ.get('PORT', '8000'))),
          threads=4, **proxy_options)
