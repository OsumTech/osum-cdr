# Verification

Verified locally before publishing:

- PostgreSQL 17 tests cover parallel duplicate imports, parallel distinct charges,
  atomic rollback, append-only triggers, administrator permissions, client pricing,
  one-time activation charges and data-preserving upgrades from version 1.
- Separate Chrome browser tests: sign-in, customer and admin pages at widths 1440,
  768, 390 and 320, no page-wide overflow, loaded logos, SIP-password reveal,
  logout, administrator client creation and token setup. Test records are isolated
  from production; provider interactions in tests use explicit mocks.
- PDF text extraction confirms invoice number, customer and exact ledger total.
- Docker image build and Compose configuration validation.

Live provider CDRs, VPS networking, Let's Encrypt issuance and provider-side call
suspension have not been tested. Live CDR imports start disabled until the admin
tests their real token and confirms the provider billing contract.

To reproduce PostgreSQL tests, set `TEST_DATABASE_URL` to an **empty disposable**
database, then run `python -m pytest -q`. Tests create and drop their tables.
Never point this variable at production. To include local Chrome checks, install
the development dependencies and Chrome, then set `BROWSER_TESTS=1`.
