"""Windows-compatible application server. Defaults to loopback for development."""
import os

from waitress import serve
from config.wsgi import application

if __name__ == '__main__':
    serve(application, host=os.environ.get('CLINIC_BIND_HOST', '127.0.0.1'),
          port=int(os.environ.get('CLINIC_PORT', '8000')), threads=4)
