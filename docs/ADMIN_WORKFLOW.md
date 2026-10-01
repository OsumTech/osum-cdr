# Administrator workflow

Implemented in schema version 2. Installation creates only the administrator;
all client management takes place in the authenticated admin dashboard.

## First client with reconciliation (schema version 4)

1. Create the company and customer login in **Clients**. Enter the real commercial
   rates or enable **Use vendor cost for calls — no uplift**. Choose the customer
   password directly in the dashboard, not in chat.
2. Add its real provider SIP account identifiers. One client can have several
   accounts. A provider SIP account belongs to exactly one client.
3. In **Integrations**, save the first collection date, confirm USD and test the
   saved token. Leave Call ID blank and enable shadow collection.
4. Run the existing `sync-cdr` worker/cron. Allow two complete observations at
   least five minutes apart for a closed UTC day.
5. Open **Review imported days**, inspect counts/costs, and optionally upload the
   manual provider CSV through **Compare manual provider CSV**. Select the real
   export timezone. A mismatch or stale report blocks acceptance.
6. After review, accept the day to post its new calls and ledger debits atomically.
   A second acceptance must add no charges. Confirm the client's resulting balance.
7. Sign in as the client, select the date range in **Call history**, and choose
   **Export Excel**. The download contains only that client's rated calls.

Test fixtures used during development do not create production clients or calls.

- Dedicated administrator dashboard with role-enforced access.
- Show provider wholesale per-minute rate and actual wholesale call amount beside
  the tenant's retail rate and billed amount; compute the resulting margin.
- Configure landline and mobile rates separately for each client at setup and
  in client settings. Store the applied rate on each call so edits are prospective.
- Configure per-client/per-number initial CLI cost, a separate one-time setup
  charge, and ongoing monthly recurring cost. Bill the initial charges once;
  subsequent renewals bill only the recurring amount.
- Audit administrator changes and financial operations. Customers must never
  receive wholesale fields or administrator controls.
- Encrypted provider settings and read-only real-CDR previews. Actual account
  validation happens after the owner saves a real token; no provider token is
  bundled with the application.

Store the API token through the admin integration page, never in conversation or
Git. Only the server encryption key remains in environment configuration. Saving
settings disables sync until retested and explicitly enabled. Confirm provider
call identity and USD cost interpretation before live billing. The empty system
never generates fake activity. Automated test fixtures are isolated and are not
copied into the production Docker image.
