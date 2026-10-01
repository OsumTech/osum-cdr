# Billing behaviour and integration contract

## Rating and ledger

Inbound CDRs in shadow reconciliation (schema 5) are mapped through the API's
`did_number`, never the forwarding `to` target or a guessed SIP account. Only
active DIDs assigned to a client are collected into that client's inbound
snapshots. Each receiving DID/day is reviewed independently. Accepted inbound
calls debit the exact final provider `amount` at six-decimal precision, always
with zero uplift; outgoing retail settings do not apply. A charged zero-duration
inbound record is retained; a zero-duration zero-charge record is skipped.
Missing cost or receiving DID blocks the affected snapshot. The existing
provider-ID import mode remains outbound-only; use shadow reconciliation for
inbound. Initial DID purchase and monthly renewal charges are separate from
inbound call usage and must not be added to each call.

Currency is USD. Decimal amounts retain six places; floating point is never used.
New-client forms suggest USD 0.016000/minute for `441`/`442` and USD 0.028000/minute
for `447`. The administrator sets each client's actual rates in the dashboard.
International numbers accept `+`, `00`, spaces, parentheses and hyphens.
Longest prefix wins; other destinations require an approved client fallback rate.

Client billing increments provisionally default to one second. Supported
increments: 1, 6, 30, 60. Duration rounds up to the increment, then cost rounds
half-up once to six decimals. Confirm each client's policy before enabling live sync.
Zero-duration calls are skipped. Existing calls retain their original rate.

Every charge locks the tenant row and atomically inserts its source, ledger entry
and balance update. Unique call IDs, tenant/payment references, and DID/date keys
prevent duplicate charges. PostgreSQL triggers prohibit UPDATE/DELETE of call,
ledger and invoice history; the database operator remains privileged.

Balances may become negative because calls have already incurred usage before
the CDR arrives. Hiding a debit would hide a liability. Provider/PBX call admission,
concurrency limits and suspension are separately required for prepaid enforcement.

## Subscriptions and invoices

New number activation debits its initial CLI price and one-time setup fee in one
transaction. The first recurring renewal is one month later. Retry or duplicate
number assignment does not charge twice. Existing-number mapping posts no initial
charge and uses the renewal date entered by the administrator. Initial/setup
amounts and the number's recurring price are stored separately. Client CLI
defaults apply only to future assignments; changing them does not change an
existing number's price.

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
2. Set its top-level field name in **Integrations → Stable call-ID field**. Never derive identity
   from timestamp/number/duration; separate real calls may share those values.
3. Set the first billing date in Integrations to the first date you intend to bill.
4. Save the API token through the admin page, confirm USD wholesale costs and all
   client rates, and test the connection. The preview makes read-only requests and
   cannot post charges. Provider fields are allowlisted, not dumped wholesale.
5. Only then click **Enable billing sync**. Saving or replacing settings disables
   sync and invalidates the old test. Legacy token/sync environment settings are ignored.

Initial sync starts at the configured date. Future syncs resume from a durable
checkpoint with a day of overlap. Outages do not skip missed days. Very late calls
beyond the overlap require an operator-controlled historical replay. Any missing
ID, unsupported rate, mismatched account, malformed page or API error preserves
the checkpoint. Already committed calls deduplicate on retry. Logs exclude bearer
tokens, SIP secrets and wholesale response bodies.

## Current boundaries

Tenant/user/trunk/DID setup, confirmed top-ups and customer password recovery use
the administrator dashboard. Only the initial admin and admin password recovery
need VPS commands. The admin is not a tenant and customers cannot access admin routes.
Stripe/PayPal,
email delivery, low-balance alerts, suspension and self-service purchases are
future work. No UI controls pretend those integrations are active. SQLite is
local/test-only; production requires PostgreSQL. Full provider masking also
requires a branded SIP hostname configured outside the portal.

## Wholesale visibility and audit

The import snapshots provider `per_minute` and `amount` alongside the applied
retail rate and cost. An administrator must confirm these are non-negative USD
costs before enabling billing. Missing provider values remain null and display
as unavailable, never invented zeros. Margin totals exclude calls whose wholesale
cost is unavailable and show the excluded count. Customer routes never render
wholesale fields. Settings, token changes (without secret values), client changes,
initial charges and top-ups are audited. PostgreSQL protects audit history from
UPDATE/DELETE alongside billing records.

Rates apply at import/posting time and are snapshotted on each call. Changes do
not rerate previously imported calls, including deduplicated historical replays.
The background worker still requires the VPS cron setup. Request sync queues work
for its next scheduled cycle; a button does not imply a worker is running.
# Vendor-cost call pricing

In client pricing, enable **Use vendor cost for calls — no uplift** to debit the
provider's final USD call charge, stored to six decimal places. Zero charges are
valid; a missing or invalid charge stops the import for that call. Retail prefix
rates, fallback rates and retail billing increments are ignored in this mode.
The stored rate is an effective rate calculated from cost and answered duration,
not a claim about the provider tariff or its billing increments. Provider tariff
information, when available, remains separately stored as the wholesale rate.
CLI initial, setup and recurring charges continue to use the configured prices.
Changes affect newly imported calls only; retries do not reprice existing calls.
Existing clients retain retail pricing after the version 3 database upgrade.
