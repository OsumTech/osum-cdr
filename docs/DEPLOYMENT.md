# Deploy to cdr.osumtech.net

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
Keep `APP_ENV=production`. Provider sync starts disabled in the database. If Python is missing,
install it with `apt install -y python3`.

## 2. Initialise and create only the administrator

```sh
docker compose up -d db
docker compose run --rm web flask --app app init-db
docker compose run --rm web flask --app app create-admin
```

Enter your administrator email and a strong password. No client company,
customer login, SIP account, number or demonstration record is created.
`init-db` creates schema version 6; `upgrade-db` safely upgrades earlier releases.
Version 3 adds optional vendor-cost call pricing; existing clients keep their retail pricing.
If you already created a customer in version 1, use a different email for the
administrator; the existing customer remains intact.

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
docker compose run --rm certbot certonly --webroot -w /var/www/certbot --email YOUR_EMAIL -d cdr.osumtech.net
cp deploy/nginx-https.conf deploy/nginx-active/default.conf
docker compose exec nginx nginx -t
docker compose exec nginx nginx -s reload
curl --fail https://cdr.osumtech.net/health
```

Visit https://cdr.osumtech.net and sign in. Health verifies database connectivity,
not worker freshness.

## 5. Configure everything else in the admin dashboard

1. Sign in with the administrator account. You are redirected to `/admin/`.
2. Open **Integrations**, paste the DID Logic token and save. It is encrypted in
   PostgreSQL and never displayed again. Keep `ENCRYPTION_KEY` in server `.env`.
3. Click **Test connection & fetch CDRs**. This reads real calls without charging
   customers. No placeholder records are generated when the API has no data.
4. Open **Clients → Create client**. Set the company, customer login, landline
   and mobile retail prices, billing increment, optional fallback rate, initial
   CLI price, setup fee and recurring CLI charge.
5. In that client's settings, assign actual SIP accounts using the identifier
   from the provider preview, then assign real telephone numbers.
6. Choose **New activation** to debit initial CLI price plus setup now and begin
   monthly recurring charges one month later. Choose **Existing number** to map
   a previously activated number without recharging its setup; enter next renewal.
7. Record externally verified payments under the client's Payments section.
   Reusing a payment reference does not duplicate a credit.
8. Configure the confirmed stable call-ID field, USD cost confirmation and first
   billing date in Integrations, test again, then explicitly enable billing sync.

Client settings also manage additional customer logins, password resets, portal
access, SIP credentials and future number renewals. Changes are audited.
Full white-labelling requires a working branded SIP hostname outside the portal.
Mapping services does not buy numbers or provision trunks with DID Logic.
Wholesale fields remain administrator-only.

For a forgotten **administrator** password, use server-side recovery:

```sh
docker compose exec web flask --app app reset-password
```

There is no unauthenticated public admin-registration page.

## 6. Enable workers

For DID Logic's current API without Call IDs, use **Enable shadow collection**
in Integrations after saving the start date, confirming USD, testing the token,
and mapping SIP accounts. Leave the Call ID field blank. The same `sync-cdr`
cron command below collects snapshots without charging customers. Run it again
at least five minutes later to compare complete snapshots. Review closed days
under **Review imported days** and explicitly accept them to post charges.
No manual CSV download is needed. See [CDR_PLAN_B.md](CDR_PLAN_B.md).
Version 5 also collects incoming calls for active receiving DIDs assigned to
clients. Assign previously purchased numbers as **Existing number** to avoid
charging setup again. Inbound snapshots are reviewed separately from outbound
SIP snapshots; accepting an inbound day never repeats outbound charges. Inbound
call charges always equal the provider's final USD amount, regardless of client
retail rates. Collection and acceptance do not themselves trigger DID renewals.
Customer **Call history → Export Excel** downloads accepted/rated calls for the
selected UTC date range and destination filter (up to 50,000 rows per download).

Read [BILLING.md](BILLING.md). Confirm commercial rates before accepting shadow
snapshots. The separate provider-ID billing mode still requires confirmed unique
call IDs; do not enable that mode for the current DID Logic API. Legacy token/sync `.env`
variables are no longer read. No API token needs to be entered at installation.

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
docker compose stop web
docker compose run --rm web flask --app app upgrade-db
docker compose up -d web
docker compose restart nginx
curl --fail https://cdr.osumtech.net/health
```

Pause only the Osumtech cron entries during a schema upgrade, then restore them
after verification. The version 2 upgrade preserves v1 tenants, users, balances,
calls, invoices and SIP credentials. It retains the legacy global fallback rate
and billing increment as each existing client's initial plan. Run `create-admin`
once after upgrading from v1; do not promote a customer login implicitly.
Keep the original `.env` and encryption key. Do not rerun the first-install Nginx
bootstrap over an existing HTTPS configuration.
Restart Nginx after recreating web to resolve its new container IP. Keep protected off-server backups.
Do not run `docker compose down -v`: it deletes database and certificate volumes.

## If `create-admin` is missing

This command requires the admin release in both the Git checkout and the built
web image. Publish local changes to GitHub first, then run `git pull --ff-only
origin main` and `docker compose build web` on the VPS. Confirm registration with
`docker compose run --rm web flask --app app --help`. Follow the upgrade sequence
above before creating the administrator; pulling source alone does not rebuild
the image.

## Before live billing

For **webhook setup and billing activation**, see [WEBHOOK_TEST.md](WEBHOOK_TEST.md).
After deploying schema 6, update the active HTTPS Nginx template before creating
the receiver URL, so its private path is excluded from logs:

```sh
cp deploy/nginx-https.conf deploy/nginx-active/default.conf
docker compose exec nginx nginx -t
docker compose exec nginx nginx -s reload
```

Webhooks collect without charging until outbound billing is explicitly activated.
After activation, new eligible calls debit the client at their saved custom rates.

- Verify login, logout, tenant isolation and PDF downloads over HTTPS.
- Confirm a real CDR's stable ID, account mapping, timezone and duration.
- Reconcile the first import, repeat it, and confirm no second debit.
- Configure provider/PBX call limits and suspension: polling cannot prevent
  overspending during calls.
- Supply legal invoice details and tax policy before using PDFs as statutory
  invoices. Current PDFs are prepaid usage statements.

## Activate custom-rate webhook billing (schema 6)

Follow the update/backup commands above, including `upgrade-db`. Open **Live
calls** and click **Activate outbound billing now**. New answered outbound calls
starting after activation use each mapped client's custom rates. Older events
are not charged. API posting is locked to avoid duplicate deductions. Inbound
and vendor-cost clients remain pending actual wholesale charges. See
[webhook billing behaviour](WEBHOOK_TEST.md) before testing charges.
