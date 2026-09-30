# Requested administrator controls

Confirmed by the owner during the first customer-portal build. These are the next
implementation scope, not features available in the initial customer release.

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
- Inspect an authorised, read-only provider CDR sample before finalising field
  mapping, stable call identity and wholesale cost interpretation.

Keep the API token in private environment configuration, not conversation or Git.
Do not enable live billing until these commercial settings and provider fields
are confirmed. Existing global default rates are provisional first-version rules.
