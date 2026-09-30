# Deploy to cdr.asumtech.net

Assumes Ubuntu 24.04, Docker Engine with Compose, and the repository at
`/root/apps/osum-cdr`. Run on the VPS as root. DNS must point to this server and
inbound TCP 80/443 must be permitted. Keep SSH access open.

## 1. Pull and build

```sh
cd /root/apps/osum-cdr
git pull --ff-only origin main
python3 deploy/init_env.py
docker compose build
```

The initialiser creates random session, database and encryption secrets with
owner-only permissions and refuses to overwrite an existing `.env`. It does not
print credentials. Never send them in chat or commit them to Git. Back up `.env`
securely; losing the encryption key makes stored SIP passwords unreadable.
Keep `APP_ENV=production` and `LIVE_SYNC_ENABLED=false`. If Python is missing,
install it with `apt install -y python3`.

## 2. Initialise and create the first company

```sh
docker compose up -d db
docker compose run --rm web flask --app app init-db
docker compose run --rm web flask --app app create-tenant
```

Enter the real company name, email and a strong password at the prompts. There
are no default accounts. Save the displayed tenant ID. `init-db` bootstraps
schema version 1; it is not a schema-upgrade tool. Future changes need migrations.

## 3. Start the HTTPS bootstrap

Runtime Nginx configuration is separate from tracked templates to keep Git pulls
clean. Do not replace active HTTPS configuration with the bootstrap on upgrades.

```sh
mkdir -p deploy/nginx-active
cp deploy/nginx/default.conf deploy/nginx-active/default.conf
cp deploy/nginx/proxy-headers.inc deploy/nginx-active/proxy-headers.inc
docker compose up -d web nginx
```

HTTP now returns 503 except for ACME challenges. The login is only served once
HTTPS is enabled, so credentials cannot be submitted over plain HTTP.

## 4. Issue the certificate

Replace `YOUR_EMAIL` with your renewal contact address. Read and accept the
Let's Encrypt terms when Certbot prompts you.

```sh
docker compose run --rm certbot certonly --webroot -w /var/www/certbot --email YOUR_EMAIL -d cdr.asumtech.net
cp deploy/nginx-https.conf deploy/nginx-active/default.conf
docker compose exec nginx nginx -t
docker compose exec nginx nginx -s reload
curl --fail https://cdr.asumtech.net/health
```

Visit https://cdr.asumtech.net and sign in. Health verifies database connectivity,
not worker freshness.

## 5. Configure real services

```sh
docker compose exec web flask --app app list-tenants
docker compose exec web flask --app app add-sip --tenant-id 1
docker compose exec web flask --app app add-did --tenant-id 1
docker compose exec web flask --app app add-user --tenant-id 1
```

Use your actual tenant ID. These commands map already-provisioned services; they
do not purchase DIDs or create provider trunks. `provider-id` must exactly match
the CDR's `sip_account` value. SIP secrets are encrypted and revealed only after
confirming the portal password. Full white-labelling requires a working branded
SIP hostname at the provider/network layer; the portal cannot mask DNS/SIP routing.

Record a top-up only after verifying its external payment. A repeated reference
and amount does not create a second credit:

```sh
docker compose exec web flask --app app top-up --tenant-id 1 --amount 100.00 --reference ACTUAL-PAYMENT-REFERENCE
docker compose exec web flask --app app reset-password
```

The second command is for password recovery; resets revoke existing sessions.

## 6. Enable workers

Read [BILLING.md](BILLING.md). Confirm commercial rates and the provider's unique
call ID before enabling live sync in `.env`.

```sh
timedatectl set-timezone UTC
docker compose run --rm web flask --app app sync-cdr
docker compose run --rm web flask --app app renew-dids
crontab -e
```

Add `deploy/cron.example` lines, preserving existing cron jobs. Nightly billing
runs at 00:05 UTC. Host locks prevent overlapping jobs. Active provisioned
services continue accruing charges even if the tenant's portal login is disabled;
local status does not suspend the upstream service.

```sh
cp deploy/logrotate.conf /etc/logrotate.d/osum-cdr
```

Monitor `/var/log/osum-cdr-*.log` and arrange external failure alerts. Certificate
renewal runs twice daily. Host cron launches ephemeral app containers; credentials
are not stored in crontab.

## Updates and backups

Back up the database and `.env` securely before updates. Test restores before
using the system for live billing.

```sh
mkdir -p backups
chmod 700 backups
umask 077
docker compose exec -T db pg_dump -U osum -d osum -Fc > "backups/osum-$(date -u +%Y%m%dT%H%M%SZ).dump"
git pull --ff-only origin main
docker compose build
docker compose up -d web
docker compose restart nginx
curl --fail https://cdr.asumtech.net/health
```

Restart Nginx after recreating web to resolve its new container IP. Future schema
changes must include migration instructions. Keep protected off-server backups.
Do not run `docker compose down -v`: it deletes database and certificate volumes.

## Before live billing

- Verify login, logout, tenant isolation and PDF downloads over HTTPS.
- Confirm a real CDR's stable ID, account mapping, timezone and duration.
- Reconcile the first import, repeat it, and confirm no second debit.
- Configure provider/PBX call limits and suspension: polling cannot prevent
  overspending during calls.
- Supply legal invoice details and tax policy before using PDFs as statutory
  invoices. Current PDFs are prepaid usage statements.
