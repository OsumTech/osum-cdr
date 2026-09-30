# Administrator workflow

Implemented in schema version 2. Installation creates only the administrator;
all client management takes place in the authenticated admin dashboard.

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
