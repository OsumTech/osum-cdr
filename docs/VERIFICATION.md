# First-release verification

Verified locally before publishing:

- 31 passing tests against isolated PostgreSQL 17, including parallel duplicate
  imports, parallel distinct charges, atomic rollback and append-only triggers.
- Separate Chrome browser test: sign-in, all four customer pages at widths 1440,
  768, 390 and 320, no page-wide overflow, loaded logos, SIP-password reveal,
  logout and no JavaScript exceptions. Test records are isolated from production.
- PDF text extraction confirms invoice number, customer and exact ledger total.
- Docker image build and Compose configuration validation.

Live provider CDRs, VPS networking, Let's Encrypt issuance and provider-side call
suspension have not been tested. Live CDR imports remain disabled. Administrator
pricing controls requested during this build are tracked in NEXT_ADMIN_RELEASE.md.

To reproduce PostgreSQL tests, set `TEST_DATABASE_URL` to an **empty disposable**
database, then run `python -m pytest -q`. Tests create and drop their tables.
Never point this variable at production. To include local Chrome checks, install
the development dependencies and Chrome, then set `BROWSER_TESTS=1`.
