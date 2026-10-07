"""Verify a fresh cPanel-style installation and HTTPS/CSRF via Passenger's entry.
Requires the application's Python and OpenSSL. Uses only temporary invented data.
"""
import http.cookiejar
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from wsgiref.simple_server import WSGIRequestHandler, make_server

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='clinic-cpanel-') as temporary:
        root = Path(temporary) / 'clinic'
        shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns('.git','.runtime','.venv','__pycache__','staticfiles','downloads'))
        environment = {**os.environ, 'CLINIC_DB_ENGINE':'sqlite'}
        # Verify the saved website settings, without inherited deployment overrides.
        for key in ['CLINIC_HTTPS','CLINIC_DEMO_MODE','CLINIC_ALLOWED_HOSTS','CLINIC_CSRF_TRUSTED_ORIGINS','CLINIC_NODE_MODE','CLINIC_LOCAL_CODE']:
            environment.pop(key, None)
        def command(*args, extra=None):
            result = subprocess.run([sys.executable,*args],cwd=root,env={**environment,**(extra or {})},capture_output=True,text=True)
            if result.returncode:
                raise RuntimeError('Installation verification failed: ' + result.stderr)
            return result
        command('deploy/prepare_cpanel.py','clinic.example.com')
        command('manage.py','migrate','--noinput')
        command('manage.py','collectstatic','--noinput')
        password = secrets.token_urlsafe(24)
        command('manage.py','createsuperuser','--noinput',extra={
            'DJANGO_SUPERUSER_USERNAME':'website-test-admin', 'DJANGO_SUPERUSER_EMAIL':'test@example.com',
            'DJANGO_SUPERUSER_PASSWORD':password})
        cert = Path(temporary) / 'certificate.pem';key = Path(temporary) / 'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),
            '-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],
            check=True,capture_output=True)
        server_code = '''
import os,ssl
from wsgiref.simple_server import make_server,WSGIRequestHandler
from passenger_wsgi import application
class Handler(WSGIRequestHandler):
    def get_environ(self):
        result=super().get_environ();result['HTTPS']='on';return result
    def log_message(self,*args): pass
server=make_server('127.0.0.1',0,application,handler_class=Handler)
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(os.environ['TEST_CERT'],os.environ['TEST_KEY'])
server.socket=context.wrap_socket(server.socket,server_side=True)
print(server.server_port,flush=True)
server.serve_forever()
'''
        proc = subprocess.Popen([sys.executable,'-c',server_code],cwd=root,env={**environment,'TEST_CERT':str(cert),'TEST_KEY':str(key)},
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        try:
            port_line = proc.stdout.readline().strip()
            if not port_line.isdigit():raise RuntimeError('Passenger startup verification failed.')
            origin = 'https://localhost:' + port_line
            jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(cert))),
                                                 urllib.request.HTTPCookieProcessor(jar))
            def request(path, data=None, site_origin='https://clinic.example.com'):
                headers={'Host':'clinic.example.com'}
                if data is not None:headers['Origin']=site_origin
                req=urllib.request.Request(origin+path,urllib.parse.urlencode(data).encode() if data is not None else None,headers)
                return opener.open(req,timeout=10)
            class Token(HTMLParser):
                value=None
                def handle_starttag(self,tag,attrs):
                    values=dict(attrs)
                    if tag=='input' and values.get('name')=='csrfmiddlewaretoken':self.value=values['value']
            with request('/login/') as response:
                assert response.status==200
                parser=Token();parser.feed(response.read().decode());csrf=parser.value;assert csrf
            with request('/static/clinic/health-background.svg') as response:assert b'<svg' in response.read()
            with request('/login/',{'username':'website-test-admin','password':password,'csrfmiddlewaretoken':csrf}) as response:
                assert response.status==200 and response.geturl().endswith('/')
                assert b'Testing demo' in response.read()
            session=next(cookie for cookie in jar if cookie.name=='sessionid')
            assert session.secure and session.has_nonstandard_attr('HttpOnly')
            csrf=next(cookie.value for cookie in jar if cookie.name=='csrftoken')
            try:
                request('/logout/',{'csrfmiddlewaretoken':csrf},site_origin='https://untrusted.example.com')
                raise AssertionError('Cross-origin logout was accepted.')
            except urllib.error.HTTPError as error:assert error.code==403
            with request('/logout/',{'csrfmiddlewaretoken':csrf}) as response:assert response.status==200
            # A client-supplied HTTP_HTTPS header must not masquerade as server metadata.
            result=command('-c',"""
import io
from wsgiref.util import setup_testing_defaults
from passenger_wsgi import application
environ={};setup_testing_defaults(environ)
environ.update({'PATH_INFO':'/login/','HTTP_HOST':'clinic.example.com','HTTP_HTTPS':'on','wsgi.url_scheme':'http','wsgi.input':io.BytesIO(b'')})
status=[]
body=b''.join(application(environ,lambda value,headers:status.append(value)))
assert status[0].startswith('301')
""")
            print('PASS: fresh domain configuration, migrations/admin initialization, Passenger startup, verified HTTPS certificate, login/logout, secure cookies, static health background, rejected foreign CSRF origin and ignored client HTTPS header.')
        finally:
            proc.terminate();proc.wait(timeout=10)

if __name__ == '__main__':main()
