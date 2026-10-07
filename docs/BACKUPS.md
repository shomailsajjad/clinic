# Backup and restore

The database includes patients, user password hashes, bookings, tokens, financial
entries, report templates, diagnoses and report versions. Backups contain sensitive
records. Keep them on a protected external drive; archives are not encrypted by
this implementation. Application source, the signing key and environment secrets
are not included. Retain the installed source separately. Restored sessions may
require users to sign in again.

## Manual backup

Admin can use **Backups → Create & download backup** and save the ZIP on the
external drive. Downloading requires admin access and a POST request.

From the Windows checkout, a technician can use:

```powershell
.\.venv\Scripts\python.exe manage.py backup_clinic --output "E:\Clinic Backups\clinic-backup.zip"
```

The target filename must be new. SQLite backup uses the database backup API,
not a copy of the live database file. PostgreSQL uses `pg_dump --format=custom`;
install PostgreSQL client tools and make them available on PATH. The generated
ZIP includes a manifest and payload SHA-256 hash and is verified after creation.
SQLite also undergoes an integrity check.

## External-drive helper and daily scheduling

Create a folder on the external drive and run **Backup Clinic.bat** once. It asks
for that existing folder and remembers the destination locally, outside Git.
Subsequent runs use it. Missing drives are reported as failures.

A technician can create a daily Windows Task Scheduler job using:

- Program: the full path to `clinic\.venv\Scripts\python.exe`.
- Arguments: the full quoted path to `clinic\scripts\backup_windows.py`, followed
  by `--unattended`.
- Start in: the clinic checkout directory.
- Account: the Windows service/setup account with database and external-folder
  access. PostgreSQL runs must receive the same securely configured database
  environment as the application.

Verify the scheduled job's result and periodically inspect the drive. The task
has not been registered or tested on Windows by this cloud session. Retention,
external-drive encryption and missing-drive alerts beyond the task's failure
status need deployment configuration. Do not rely only on a local backup.

## Restore into a separate target

For SQLite:

```powershell
.\.venv\Scripts\python.exe manage.py restore_clinic "E:\Clinic Backups\clinic-backup.zip" --target ".runtime\restored.sqlite3"
```

The target file must not exist. Existing files are never overwritten. Inspect
the restored database and run any required migrations before switching the
application, with its server stopped. Keep the previous database recoverable.

For PostgreSQL, create a separate empty database first, then:

```powershell
.\.venv\Scripts\python.exe manage.py restore_clinic "E:\Clinic Backups\clinic-backup.zip" --target clinic_restore_check
```

The active database name is refused. PostgreSQL requires `pg_restore` on PATH
and permissions for the separate target. Verification on SQLite has been tested;
PostgreSQL backup/restore integration still needs validation on the host.

SHA-256 checks detect accidental corruption; they do not authenticate an archive
from an untrusted person. Restore only your own trusted clinic backups.
