# Live webhook calls and outbound billing

Schema 6 adds an explicit billing activation point. Deploy and upgrade the
schema before opening Live calls. The existing receiver URL stays valid.

## Activate

Open Live calls and select **Activate outbound billing now**. This records the
current UTC time once, pauses API collection/billing, and locks historical/API
financial posting to prevent duplicate charges. Repeating activation does not
move the start time. Existing calls and balances are preserved.

Only new events for calls **starting at or after activation** are eligible.
Existing inbox entries and calls starting before activation remain uncharged,
even if delivered again. Configure active CLI ownership before new calls arrive.

Answered outbound calls with positive billsec use the mapped client's custom
landline/mobile/fallback rate and billing increment. The stored call, ledger debit,
balance and inbox event commit together. Duplicate deliveries charge once;
changed payloads flag a conflict and preserve the original charge for review.
Whole-sale cost remains unknown and is excluded from known-margin calculations.

Inbound and vendor-cost pricing require actual provider costs and stay Pending.
Unknown CLI, inactive clients, missing rates and invalid outcomes are held with
an explanation. Remapping an old event does not debit it. No automatic pending
reprocessing or historical CSV posting is introduced in this release.

## Verify

Make a real answered outbound call after activation using an assigned CLI.
Check its Charge / USD and billed seconds against the client's saved rates.
Confirm the client call history, ledger and balance show that same charge.
A duplicate delivery must only increment the delivery count. Check an inbound
call remains Pending awaiting actual vendor charge.

Calls are not blocked or terminated when balances become negative; deductions
happen after the call ends. Provider-side call control remains separate.

## Inbox and receiver

Rows default to 10; choose 25, 50 or 100. Filters and pagination preserve page
size. Long IDs, received timestamps and delivery counts are inside Call details.
The table has a bounded scrolling area and sticky headers.

Receiver configuration contains the private URL and pause/resume controls.
Keep the URL secret. Nginx and Gunicorn must omit it from access logs, as in the
provided deployment configuration. A paused receiver or storage failure returns
503; invalid credentials return 404; malformed events return 400.
Authentication uses the private URL, not the asserted caller ID. Enforce the
outbound CLI at the provider. Inbound mapping uses dst, outbound mapping uses src.
No SIP account is inferred from user_id.
