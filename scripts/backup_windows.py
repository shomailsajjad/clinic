"""Interactive first setup, then repeatable backup to a configured external folder."""
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')


def main():
    import django
    django.setup()
    from django.conf import settings
    from clinic.backup import create_backup
    config = settings.RUNTIME_DIR / 'backup-destination.txt'
    if config.exists():
        destination = Path(config.read_text(encoding='utf-8').strip())
    elif '--unattended' in sys.argv:
        print('Configure the external backup folder by running Backup Clinic.bat first.')
        return 1
    else:
        destination = Path(input('Enter an existing folder on your external drive (for example E:\\Clinic Backups): ').strip().strip('"'))
        if not destination.is_absolute() or not destination.is_dir():
            print('That backup folder is unavailable. Connect the drive and create the folder first.')
            return 1
        config.write_text(str(destination), encoding='utf-8')
    if not destination.is_dir():
        print('The configured external backup folder is unavailable. Connect the drive; backup was not completed.')
        return 1
    filename = f'clinic-backup-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}.zip'
    try:
        result = create_backup(destination / filename)
    except (OSError, RuntimeError):
        print('Backup failed. Check free disk space, permissions and database tools.')
        return 1
    print(f'Backup created and verified: {result}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
