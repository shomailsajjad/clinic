# Sajjad Poly Clinic and Diagnostic Center

Offline clinic software for Dr. Asfa Batool (FCPS Radiology, MBBS).

**[See screenshots of the working application](docs/SCREENSHOTS.md)** — no
installation or hosting payment needed. Images show invented patients and
illustrative prices; they are a visual walkthrough, not an interactive demo.

## Implemented clinic workflows

- Admin, operator and doctor accounts with server-enforced permissions.
- Login, POST-only logout, password validation and account deactivation.
- Patient registration, editing, detail pages and paginated search by name, phone, CNIC or patient number.
- Optional CNIC, shared family phone numbers, actual birth dates or entered age with years/months/days.
- Admin-managed services, exact PKR pricing, token prefixes and service activation.
- Admin user management and activity log recording actor, record, timestamp and changed field names.
- Responsive screens with locally bundled assets and clinic branding.

- Appointment and walk-in bookings with multi-service daily tokens, queues, arrival, cancellation and rescheduling.
- Immutable booked prices and patient/age snapshots; daily counters use the Pakistan clinic date.
- Admin-approved discounts; full payment at booking with mandatory digital reference numbers.
- 80 mm token/payment/refund print layouts; admin refunds and payment-method/reference corrections preserve the original ledger.
- Admin report templates with editable measurements, structured diagnoses, doctor-only A4 report printing and preserved report versions.
- Cash/refund/net reports by date/method/posting user, patient age/disease/referral/service analysis, and CSV exports.
- Verified database backup downloads and command-line restore into a separate database; a Windows external-drive helper.

This is a development implementation. Windows/LAN installation, PostgreSQL integration/concurrency, physical printing and automatic daily backup scheduling remain to be validated. Use synthetic patients until deployment checks are complete.

## Cloud development

Python 3.12 and uv are used in the cloud environment. Use the existing checkout; no additional Git worktree is needed.

```bash
cd /workspace/clinic
bash scripts/setup-cloud.sh
.venv/bin/python manage.py test
.venv/bin/python manage.py createsuperuser
.venv/bin/python serve.py
```

Choose a unique admin username and password interactively; there are no default accounts or passwords. Use Users & access to create the two operators and doctor. Configure each service and its real price through Services. No patient or account data is seeded during installation.

The server listens on loopback port 8000 by default. Static assets are served locally by WhiteNoise after collectstatic. The default SQLite database is for cloud/single-process development only. PostgreSQL is required for the planned multi-computer clinic deployment. A generated signing key and the development database live under ignored `.runtime/`; do not commit or disclose these files.

Dependencies are fully pinned with SHA-256 hashes in requirements.txt. The source pins are in requirements.in. Refresh the lock deliberately with `UV_CACHE_DIR=/workspace/.cache/uv uv pip compile --generate-hashes requirements.in -o requirements.txt`. Do not disable TLS or hash verification.

## Windows installation preparation

For a first-time single-computer test, extract the downloadable ZIP, read
`START HERE.txt`, and run `Setup Clinic.bat` followed by `Start Clinic.bat`.
The setup launcher creates an admin interactively if no active admin exists;
it never supplies a default password. The launchers require Python 3.12 and
internet for the initial dependency download. They are not a standalone installer
and have not been executed on Windows.

Run these commands in PowerShell from the checkout after installing Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.txt
# Configure a dedicated PostgreSQL database/user before migrations for LAN deployment.
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe serve.py
```

These Windows steps are preparation instructions, not a verified Windows installer. The final host setup must include PostgreSQL provisioning, restricted firewall rules, a restartable Windows service, LAN HTTPS, and backup/restore validation before live patient use.

## Configuration

| Variable | Purpose / default |
|---|---|
| CLINIC_DB_ENGINE | `sqlite` for development; set `postgresql` for the LAN host |
| CLINIC_DB_NAME, CLINIC_DB_USER | PostgreSQL database/user; defaults `clinic` |
| CLINIC_DB_PASSWORD | Database password, supplied securely outside source control |
| CLINIC_DB_HOST, CLINIC_DB_PORT | Database address; defaults `127.0.0.1`, `5432` |
| CLINIC_SECRET_KEY | Optional externally supplied signing key; otherwise generated once locally |
| CLINIC_ALLOWED_HOSTS | Comma-separated hostnames/IPs; defaults loopback only; never use a wildcard for deployment |
| CLINIC_BIND_HOST | Defaults `127.0.0.1`; bind to the configured LAN address for deployment |
| CLINIC_PORT | Application port; default `8000` |
| CLINIC_DEBUG | `0` by default; `1` for local troubleshooting only |
| CLINIC_HTTPS | Set `1` when serving through a configured HTTPS endpoint; enables secure cookies and HTTPS redirect |

All date displays use Asia/Karachi. Do not put credentials in scripts, chat, version control or logs. Protect the generated signing key with Windows file permissions during installation. Never expose this development server publicly. Login throttling, deployment security review and real-device testing remain part of deployment preparation.

## Validation and next stages

### Optional hosted testing demo

`render.yaml` describes a single-instance Render demo using temporary SQLite
storage. Follow `ONLINE DEMO.txt` to deploy it through your own account.
No deployment has been created by adding this configuration. Set the demo
password in Render's secure dashboard; never commit it. The demo explicitly
marks its pages as testing-only and can reset all records after a restart.
Render supplies the actual HTTPS URL after deployment. Use invented patients
only, and confirm the displayed service plan before creating it.

Login attempts are limited to five failures per username over fifteen minutes
using a process-local cache. This suits the single-instance demo; a production
deployment needs a shared limiter and broader deployment review. Windows
launchers retain their console windows on failures and are stored with CRLF
line endings for direct use after downloading a ZIP.

Run `python manage.py test` with the configured virtual environment. The foundation suite exercises role restrictions, authentication, CSRF, patient validation/search, pricing, account management and audit records using synthetic data.

Remaining deployment work: PostgreSQL host provisioning and integration checks, Windows service/firewall/LAN HTTPS setup, 80 mm/A4 printer validation, backup retention/encryption and scheduled daily external-drive backups. There is no turnkey Windows installer yet.

See [the user guide](docs/USER_GUIDE.md) for the workflow and [backup instructions](docs/BACKUPS.md) for restore and scheduling. Financial corrections currently change a payment's method/reference while preserving its amount; the operator must collect the exact approved charge. Partial payments are outside the agreed initial scope. Restore is intentionally performed into a separate database, not by overwriting live records.

PostgreSQL operation and Windows deployment have not yet been validated. Partial payments remain outside the initial full-payment-at-booking assumption.
