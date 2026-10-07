"""Configure a fresh cPanel website for testing. Run using its selected Python."""
import argparse
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('domain', help='Clinic domain/subdomain, without https:// or a path')
    args = parser.parse_args()
    domain = args.domain.strip().lower().encode('idna').decode('ascii')
    if len(domain) > 253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', domain):
        parser.error('Enter a domain/subdomain without a protocol, port or path.')
    root = Path(__file__).resolve().parents[1]
    runtime = root / '.runtime'
    runtime.mkdir(exist_ok=True, mode=0o700)
    runtime.chmod(0o700)
    destination = runtime / 'node.json'
    if destination.exists():
        parser.error('Existing configuration retained. Ask your installer to review it before changing this installation.')
    settings = {'mode':'standalone','allowed_hosts':[domain,'localhost','127.0.0.1'],
                'https':True,'demo':True,'csrf_trusted_origins':['https://' + domain]}
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(settings, stream, indent=2)
    destination.chmod(0o600)
    print('Website configured for HTTPS testing. No default login was created.')
    print('Next: install requirements, migrate, collect static files, create your admin, then restart the Python app.')

if __name__ == '__main__':
    main()
