"""Verified database backups; restoration always targets a separate database."""
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings
from django.utils import timezone


def database_environment():
    database = settings.DATABASES['default']
    env = os.environ.copy()
    for key, source in [('PGHOST', 'HOST'), ('PGPORT', 'PORT'), ('PGUSER', 'USER'), ('PGPASSWORD', 'PASSWORD')]:
        if database.get(source):
            env[key] = str(database[source])
    return env


def run_database_tool(command):
    try:
        result = subprocess.run(command, env=database_environment(), capture_output=True, check=False)
    except OSError as error:
        raise RuntimeError('PostgreSQL backup tools are not installed.') from error
    if result.returncode:
        # Never echo command output that could contain connection credentials.
        raise RuntimeError('The PostgreSQL backup/restore operation failed.')


def create_backup(output=None):
    database = settings.DATABASES['default']
    engine = 'sqlite' if database['ENGINE'].endswith('sqlite3') else 'postgresql'
    directory = settings.RUNTIME_DIR / 'backups'
    directory.mkdir(exist_ok=True, mode=0o700)
    path = Path(output) if output else directory / f'clinic-backup-{timezone.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}.zip'
    if path.exists():
        raise RuntimeError('Backup destination already exists; choose a new filename.')
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='clinic-backup-') as folder:
        payload = Path(folder) / ('database.sqlite3' if engine == 'sqlite' else 'database.dump')
        if engine == 'sqlite':
            source = Path(database['NAME'])
            if not source.is_file():
                raise RuntimeError('No database exists to back up.')
            with sqlite3.connect(str(source)) as db, sqlite3.connect(str(payload)) as destination:
                db.backup(destination)
                if destination.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Database integrity check failed.')
        else:
            run_database_tool(['pg_dump', '--format=custom', '--no-owner', '--no-acl',
                               '--file', str(payload), str(database['NAME'])])
        manifest = {'format': 1, 'engine': engine, 'payload': payload.name,
                    'sha256': hashlib.sha256(payload.read_bytes()).hexdigest(),
                    'created_at': timezone.now().isoformat(),
                    'clinic': 'Sajjad Poly Clinic and Diagnostic Center'}
        with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
            archive.write(payload, payload.name)
        path.chmod(0o600)
    verify_backup(path)
    return path


def verify_backup(path):
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip():
                raise RuntimeError('Archive integrity check failed.')
            manifest = json.loads(archive.read('manifest.json'))
            expected = {'sqlite': 'database.sqlite3', 'postgresql': 'database.dump'}
            payload_name = expected.get(manifest.get('engine'))
            if manifest.get('format') != 1 or manifest.get('payload') != payload_name or not payload_name:
                raise RuntimeError('Unsupported backup format.')
            if set(archive.namelist()) != {'manifest.json', payload_name}:
                raise RuntimeError('Unexpected files in backup.')
            data = archive.read(payload_name)
            if hashlib.sha256(data).hexdigest() != manifest.get('sha256'):
                raise RuntimeError('Backup checksum mismatch.')
    except (zipfile.BadZipFile, KeyError, ValueError) as error:
        raise RuntimeError('Not a valid clinic backup.') from error
    if manifest['engine'] == 'sqlite':
        with tempfile.TemporaryDirectory(prefix='clinic-verify-') as folder:
            payload = Path(folder) / payload_name
            payload.write_bytes(data)
            with sqlite3.connect(f'{payload.as_uri()}?mode=ro', uri=True) as db:
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise RuntimeError('SQLite integrity verification failed.')
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'django_migrations', 'clinic_user', 'clinic_patient'}.issubset(tables):
                    raise RuntimeError('Backup does not contain the clinic database.')
    return manifest


def restore_backup(path, target):
    manifest = verify_backup(path)
    with tempfile.TemporaryDirectory(prefix='clinic-restore-') as folder:
        payload = Path(folder) / manifest['payload']
        with zipfile.ZipFile(path) as archive:
            payload.write_bytes(archive.read(manifest['payload']))
        if manifest['engine'] == 'sqlite':
            destination = Path(target).resolve()
            if destination.exists():
                raise RuntimeError('Restore target already exists; use a new database filename.')
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('xb') as stream:
                stream.write(payload.read_bytes())
            destination.chmod(0o600)
        else:
            if target == settings.DATABASES['default']['NAME']:
                raise RuntimeError('Restore into a separate database, never the active database.')
            run_database_tool(['pg_restore', '--exit-on-error', '--no-owner', '--no-acl',
                               '--dbname', target, str(payload)])
    return manifest
