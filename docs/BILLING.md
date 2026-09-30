# Billing behaviour and integration contract

## Rating and ledger

Currency is USD. Decimal amounts retain six places; floating point is never used.
Prefixes `441`/`442` cost USD 0.016000/minute and `447` costs USD 0.028000/minute.
International numbers accept `+`, `00`, spaces, parentheses and hyphens.
Longest prefix wins; other destinations require an approved `FALLBACK_RATE`.

`BILLING_INCREMENT_SECONDS=1` provisionally charges per second. Supported
increments: 1, 6, 30, 60. Duration rounds up to the increment, then cost rounds
half-up once to six decimals. Confirm this policy before enabling live sync.
Zero-duration calls are skipped. Existing calls retain their original rate.

Every charge locks the tenant row and atomically inserts its source, ledger entry
and balance update. Unique call IDs, tenant/payment references, and DID/date keys
prevent duplicate charges. PostgreSQL triggers prohibit UPDATE/DELETE of call,
ledger and invoice history; the database operator remains privileged.

Balances may become negative because calls have already incurred usage before
the CDR arrives. Hiding a debit would hide a liability. Provider/PBX call admission,
concurrency limits and suspension are separately required for prepaid enforcement.

## Subscriptions and invoices

Active DIDs renew for every date <= today, recovering missed runs. The original
day is preserved: 31 January -> 28/29 February -> 31 March. Historical next-billing
dates intentionally cause catch-up charges.

Nightly billing snapshots one invoice per company/completed ledger posting month.
Retries never debit the invoice again. Late calls appear in the current posting
month; the dashboard groups by call date. PDFs itemise aggregate usage and DID
subscriptions with selectable text and six-place totals matching the ledger.
No tax, seller registration, payment instructions or business details are invented.
These are prepaid usage statements until the legal invoice details are supplied.

## DID Logic contract

Official reference: https://docs.didlogic.com/docs/api/retrieve-call-detail-records/

`GET https://app.didlogic.com/api/v1/calls` uses Bearer authentication. The adapter
sends `type=sip`, `missed=0`, `sip_account`, inclusive `from`/`to` dates, and
`page`/`per_page` (1000 maximum). It fetches individual days and pages until a short
page, detects repeated pages, has connection/read timeouts and rejects redirects.

The published schema documents timestamp, type, duration, destination and account,
but not a stable unique call ID. Before enabling imports:

1. Ask DID Logic to confirm a stable, globally unique ID in the actual response.
2. Set its top-level field name in `DIDLOGIC_CALL_ID_FIELD`. Never derive identity
   from timestamp/number/duration; separate real calls may share those values.
3. Set `SYNC_START_DATE` to the first date you deliberately intend to bill.
4. Set the token privately in `.env`, confirm all rates, and validate the mapping.
5. Only then set `LIVE_SYNC_ENABLED=true`.

Initial sync starts at the configured date. Future syncs resume from a durable
checkpoint with a day of overlap. Outages do not skip missed days. Very late calls
beyond the overlap require an operator-controlled historical replay. Any missing
ID, unsupported rate, mismatched account, malformed page or API error preserves
the checkpoint. Already committed calls deduplicate on retry. Logs exclude bearer
tokens, SIP secrets and wholesale response bodies.

## Current boundaries

Tenant/user/trunk/DID setup, confirmed top-ups and password recovery use VPS
operator commands. There is no reseller web-admin interface yet. Stripe/PayPal,
email delivery, low-balance alerts, suspension and self-service purchases are
future work. No UI controls pretend those integrations are active. SQLite is
local/test-only; production requires PostgreSQL. Full provider masking also
requires a branded SIP hostname configured outside the portal.
