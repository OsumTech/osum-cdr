# Osumtech VoIP Billing & Reseller Portal

Multi-tenant prepaid telecom billing and customer portal for Osumtech.

Flask, PostgreSQL, Python background workers, Docker Compose and Nginx.
Deployment domain: **cdr.asumtech.net**.

## Application

The portal includes company-scoped login, dashboard, filtered call history,
encrypted SIP credentials, assigned DIDs, transaction ledger and PDF usage invoices.
Billing uses decimal arithmetic, atomic ledger updates, retry-safe call imports,
overdue subscription catch-up and immutable PostgreSQL billing history.
Tenant administration and verified top-ups use server-side operator commands.

Read [deployment instructions](docs/DEPLOYMENT.md) to install the application,
enable HTTPS, create accounts and schedule workers. Review the
[billing contract](docs/BILLING.md) before live charging. The provider's stable
call-ID field still needs confirmation, so live imports are disabled by default.
Five-minute retrospective billing cannot enforce upstream prepaid limits.

## Local development and tests

Python 3.13+:

```sh
python -m venv .venv
# Linux: source .venv/bin/activate
# Windows PowerShell: .venv/Scripts/Activate.ps1
pip install -r requirements-dev.txt
python -m pytest -q
```

To run locally, set `APP_ENV=development`, a random `SECRET_KEY` of at least
32 characters, and a generated Fernet `ENCRYPTION_KEY` in the shell environment:

```sh
python -c "import secrets; print(secrets.token_hex(32))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
flask --app app init-db
flask --app app create-tenant
flask --app app run
```

The first two commands print values to set in your environment. Local SQLite
defaults to `instance/portal.db`; production requires PostgreSQL. Docker Compose
loads `.env`; the local Flask CLI does not automatically load it. No built-in
accounts or demo activity are created.

Read [AGENTS.md](AGENTS.md) and [design rules](docs/DESIGN_RULES.md) before UI edits.
Approved logos and self-hosted Inter are tracked under `app/static/`.

## Connect a remote repository

Create an empty private repository on your Git hosting service, then run locally:

```sh
git remote add origin <REPOSITORY_SSH_URL>
git push -u origin main
```

## First checkout on the Linux VPS

Install Git and configure a read-only SSH deploy key for the repository on the
VPS. Verify the Git host SSH fingerprint before trusting it. Then run:

```sh
git clone <REPOSITORY_SSH_URL> ~/osum-cdr
cd ~/osum-cdr
```

## Sync future changes

On your development machine, review and commit the intended files, then push:

```sh
git status
git add <FILES>
git commit -m "Describe the change"
git push origin main
```

On the VPS:

```sh
cd ~/osum-cdr
git pull --ff-only origin main
```

Keep the VPS checkout free of source edits. Git transfers source code; application
restart, database migrations, and deployment automation will be added with the app.

## Secrets and runtime data

Keep real credentials in an untracked `.env` file on each machine. Commit only
placeholder values in `.env.example` when configuration is introduced. Database
data, backups, certificates, and generated output must remain outside Git.
