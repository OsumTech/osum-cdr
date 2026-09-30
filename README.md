# Osumtech VoIP Billing & Reseller Portal

Multi-tenant prepaid telecom billing and customer portal for Osumtech.

Planned stack: Flask, PostgreSQL, Python background workers, Docker Compose,
and Nginx. Application and deployment configuration are not implemented yet.

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
