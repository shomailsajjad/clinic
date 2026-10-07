# Upload the clinic application to cPanel

You need **Setup Python App / Python Selector**, Python **3.12**, and SSL for the
clinic domain. If your cPanel has only PHP/MySQL website tools, ask the hosting
provider to enable Python application hosting before uploading this application.
A domain or a PHP-only hosting plan cannot run Django.

Use a subdomain such as **clinic.yourdomain.com** so your main website remains
separate. Replace that example with your actual domain in the steps below.
This package opens the complete application for online testing. It uses a
persistent SQLite database for a small test; use PostgreSQL for the eventual
multi-user deployment. Do not enter real patients during this initial test.

## 1. Download and upload

1. Download **[clinic-cpanel.zip](../downloads/clinic-cpanel.zip)**. It contains a
   ready-to-upload `clinic` folder, including the startup file and this guide.
2. In cPanel **File Manager**, upload and extract this ZIP in your account’s home
   directory, alongside `public_html`. It creates the **clinic** folder.
3. Ensure `manage.py`, `passenger_wsgi.py`, `requirements.txt`, `clinic/`,
   `config/`, `templates/`, and `deploy/` are directly inside the **clinic** folder.
   If you instead download the entire GitHub repository ZIP, move the contents
   of its extra `clinic-main` folder into `clinic`.
4. Keep this application folder outside `public_html`. Private settings and the
   test database must not be publicly downloadable.

## 2. Create the Python application

First add your clinic subdomain in cPanel **Domains** (or **Subdomains**) and
point its DNS to your hosting account. It should appear in the Application URL
choices.

In **Setup Python App**, choose **Create Application**:

| Field | Value |
| --- | --- |
| Python version | 3.12 |
| Application mode | Production |
| Application root | clinic |
| Application URL | your clinic subdomain, with no additional path |
| Application startup file | passenger_wsgi.py |
| Application entry point | application |

Save/create the application. Wait to restart it until the next step is finished.
The menu names can vary between hosting providers. If **Application Manager** is
the only option, your provider must confirm that its Passenger installation
supports Python and select the correct interpreter for you.

## 3. Run installation commands

Open cPanel **Terminal**. At the top of the Python application page, cPanel shows
an activation command for this application’s virtual environment. **Copy and run
that exact command first**; do not use `.venv/bin/python` from the earlier Windows
instructions. Then run these commands, replacing the example domain:

```bash
cd ~/clinic
python --version
python deploy/prepare_cpanel.py clinic.yourdomain.com
python -m pip install --require-hashes -r requirements.txt
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

`python --version` should show the selected Python 3.12 environment. Choose your
own admin username and password when prompted. Password characters will not
appear while typing; this is normal. No default username/password is supplied.

If cPanel Terminal is unavailable, ask the hosting support team to run the
commands in your selected Python environment. These commands, database setup and
admin creation are required; uploading the files alone does not start Django.

For testing multiple clinics, also run:

```bash
python manage.py create_organization_admin organization-admin
```

Choose a separate password for the organization administrator.

## 4. Enable HTTPS and restart

1. In cPanel **SSL/TLS Status**, enable or run **AutoSSL** for the clinic subdomain.
2. Return to **Setup Python App** and click **Restart**.
3. Open **https://clinic.yourdomain.com** and sign in with the admin you created.

The application stores domain settings in `.runtime/node.json`, its private
signing key in `.runtime/secret.key`, and test records in
`.runtime/development.sqlite3`. Keep these files across updates. The setup helper
refuses to replace existing configuration, so it cannot accidentally rebind an
existing branch installation.

## 5. Try the workflows

- As clinic admin, add the doctor and operators, prices/services, diagnoses and
  report templates. Mark a consultation service as **Doctor consultation / OPD**.
- As operator, register an invented patient, book an appointment or walk-in,
  collect a cash/digital payment and print an 80 mm receipt.
- As doctor, save an examination, finalize its prescription and print on the
  clinic’s letterhead. Try a revision and check the earlier version remains.
- As admin, test discounts, refunds and cash/diagnosis/referral reports.
- As organization admin, add another clinic and its accounts, then check access
  isolation and doctor-only shared clinical history.

Online testing uses **standalone mode**, so the website accepts bookings and
payments. Independent branch servers and outage synchronization are a separate
rollout described in `docs/MULTI_CLINIC.md`.

## Errors and updates

- **ModuleNotFoundError / no Django:** activate the environment shown by cPanel
  and install `requirements.txt` there.
- **403 / DisallowedHost:** confirm the exact domain in `.runtime/node.json` and
  that you use HTTPS. Do not set allowed hosts to `*`.
- **Too many redirects:** ask the provider to ensure Passenger supplies the
  HTTPS request scheme or Apache’s `HTTPS=on` server variable. The startup wrapper
  uses server metadata, not an arbitrary client header. Do not disable SSL to
  hide a proxy configuration problem.
- **Permission error / unable to open database:** ensure the Python application
  runs as the account owner and can write `.runtime`. Do not make it world-writable.
- **Database locked:** SQLite is suitable for limited testing. Configure a
  PostgreSQL database before sustained use with multiple simultaneous staff.
- **Missing CSS:** run `collectstatic` in the correct environment and restart.
- **Python unavailable:** request a Python-capable plan or move to a VPS. Renaming
  the application to PHP or uploading it into `public_html` will not make it run.

Back up before replacing source files:

```bash
python manage.py backup_clinic
```

Download the archive from `.runtime/backups` through your private cPanel File
Manager, store it securely, and preserve `.runtime` when uploading updates.
Run migrations/collectstatic and restart after updates. See `docs/BACKUPS.md`
for the separate-target restore procedure.

Your provider’s Python feature, actual domain routing/SSL and Passenger process
settings require checking on your own hosting account. Do not use real patients
until the intended database, permissions and backups/restore are verified.

Package checks passed in a fresh temporary installation: migrations/admin setup,
Passenger entry point, verified HTTPS login/logout, secure cookies, local static
assets and rejected cross-origin requests. The 70 Django tests also passed.
Your actual hosting provider and domain still require an installation check.
