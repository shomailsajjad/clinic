"""Build a cPanel upload ZIP using an explicit public-source allowlist."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / 'downloads' / 'clinic-cpanel.zip'
    output.parent.mkdir(exist_ok=True)
    files = [ROOT / name for name in ['manage.py','passenger_wsgi.py','requirements.txt','README.md','START HERE WEBSITE.txt']]
    for directory in ['clinic','config','templates','docs']:
        files.extend(path for path in (ROOT / directory).rglob('*') if path.is_file()
                     and '__pycache__' not in path.parts and path.suffix not in ['.pyc','.sqlite3'] and not path.is_symlink())
    files.extend(ROOT / name for name in ['deploy/CPANEL.md','deploy/prepare_cpanel.py','scripts/verify_cpanel.py'])
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            archive.write(path,Path('clinic') / path.relative_to(ROOT))
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        names=set(archive.namelist())
        assert all(not any(part in ['.runtime','.venv','.git','.env'] for part in Path(name).parts) for name in names)
        assert all(not name.endswith(('.sqlite3','.pyc','.private')) for name in names)
        assert {'clinic/manage.py','clinic/passenger_wsgi.py','clinic/deploy/CPANEL.md'} <= names
    print(f'Built {output.name}: {len(files)} public-source files, {output.stat().st_size} bytes. Private runtime data excluded.')

if __name__ == '__main__':main()
