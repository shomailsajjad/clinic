"""Save private branch sync settings without displaying credentials."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.conf import settings
from clinic.sync import validate_sync_url

def main():
    if settings.CLINIC_NODE_MODE != 'branch':
        raise RuntimeError('Configure the branch before setting up synchronization.')
    url = validate_sync_url(input('Central server HTTPS address: ').strip())
    key_path = Path(input('Path to the private key file supplied for this branch: ').strip().strip('"'))
    key = key_path.read_text(encoding='utf-8').strip()
    if not 32 <= len(key) <= 200 or any(character.isspace() for character in key):
        raise RuntimeError('Invalid key file.')
    path = settings.RUNTIME_DIR / 'sync-config.json'
    path.write_text(json.dumps({'url':url,'key':key}), encoding='utf-8')
    path.chmod(0o600)
    print('Synchronization configured. Keep this file private. Run Start Sync.bat on the branch server.')

if __name__ == '__main__':
    try: main()
    except (OSError, RuntimeError, ValueError):
        print('Configuration failed. Check the branch setup, HTTPS address and private key file.');sys.exit(1)
