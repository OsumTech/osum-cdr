# DID Logic webhook shadow test

Schema 5 adds a durable webhook inbox, not webhook billing. No event handler
creates a Call or Ledger row, estimates wholesale costs or changes a balance.
The existing reconciliation flow remains the only way to accept these API calls
for billing. Do not disable it or count inbox events as additional billed usage.

## Configure and test

The admin sidebar now opens **Live calls** directly. Filter by client, direction
or mapping status, review provider call outcomes, and compare call time with
receipt time. Summary counts cover all stored events; the table follows the
selected filters. Refresh calls to fetch the latest saved deliveries.
Receiver configuration and its private URL are collapsed after setup.
Historical API tools remain under **History & reconciliation**. This navigation
change does not start or stop existing workers, import history or post charges.

1. Deploy the image and run `upgrade-db`. Install the current
   `deploy/nginx-https.conf` into `deploy/nginx-active/default.conf`, run
   `nginx -t` and reload. The webhook location disables access/error logging of
   its bearer URL. The image also uses a Gunicorn access logger that omits that
   route. Any additional reverse proxy must likewise exclude or redact it.
2. Open Live calls → Receiver configuration and private URL. Create a shadow receiver and copy its
   private HTTPS URL into DID Logic's CDR webhook setting. Keep the URL secret;
   never paste it into chat, analytics, monitoring URLs or public logs.
3. Assign active CLIs/DIDs to the correct client. Outbound mapping uses `src`;
   inbound mapping uses `dst`. Provider-side enforcement of outbound CLI is an
   operational prerequisite for trusting that mapping. A caller number is not
   cryptographic authentication. One number must have one owner.
4. Complete one real outbound call and one real inbound call. Check the inbox's
   direction, mapped client, `callid`, billable seconds and total duration against
   provider records. Unknown numbers are retained as Unmapped and can be checked
   again after assigning the number. No production demo events are generated.
5. Confirm balances and the ledger have not changed because of these events.
   Existing cron jobs may separately charge DID renewals; isolate that in any
   before/after comparison. Scheduled shadow collection itself posts no money.

Identical repeated deliveries increment the original event's delivery count.
Changed core fields under the same direction and Call ID flag Conflict and keep
the original payload. The namespace includes direction because inbound IDs may
use provider encryption. Independent concurrent deliveries are serialised by
PostgreSQL advisory locks plus a unique constraint. Acknowledge only after commit.
Storage failures and a paused receiver return 503 for retry; invalid credentials
return 404 and malformed events return 400. Requests are capped at 64 KB.

Payload names follow the documented CDR example: `event`, `callid`, `direction`,
`calldate`, `src`, `dst`, `billsec`, `duration`, `disposition`. Unknown fields are
not retained. No SIP account is inferred from `user_id`. Non-CDR events must use
a separate receiver. Only administrator sessions can view/rotate the secret or
inspect the inbox. Rotation invalidates the old URL immediately.

## Before any future real-time financial posting

Observe real provider events, validate enforced CLI coverage and inbound `dst`
semantics, and test delayed/repeated deliveries. Retain reconciliation for missed
events and corrections. Define an explicit cutover so historical API imports
cannot bill calls already charged by webhook. Vendor-cost clients and inbound
calls need actual provider charges; static rates can only estimate those costs.
Do not label estimated margins as exact or guarantee second-level delivery.
These financial changes and a queue-based rating worker are not implemented in
the shadow receiver.
