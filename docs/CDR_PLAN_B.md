# CDR reconciliation without provider call IDs

Status: implemented in schema version 4 as scheduled shadow collection with
explicit administrator acceptance of closed days. Disabled by default.

Schema version 5 adds separate receiving-DID/day inbound snapshots. Incoming API
records are fetched with `type=incoming`, including missed calls, and mapped by
`did_number`. The paginated incoming day is downloaded once per run and reused
for all assigned active DIDs being checked that day. Only mapped numbers are
billed; status reports unassigned numbers encountered during those downloads.
With no active DIDs, status explicitly says inbound collection was not performed.
Positive-duration calls and charged zero-duration incoming records are retained.
Inbound costs always pass through unchanged at the application's six-decimal
precision. The old outbound fingerprints and accepted snapshots are preserved.

For inbound CSV verification, the receiving number must appear in a `DID number`,
`DID Number`, `did_number` column or in `To`. Supported inbound Type labels are
`INCOMING`, `INBOUND`, `DID`, and `DID ORIG`; unrecognised types fail validation.
Do not map a forwarded extension/SIP destination to a client implicitly. The real
provider's inbound CSV layout must be checked before relying on that comparison.

## Current workflow

Save the API token and first collection date in Integrations, confirm USD costs,
test the connection, map SIP accounts, then enable shadow collection. The existing
five-minute `sync-cdr` job collects per-account UTC-day snapshots. No Call ID is
required, and the collector never posts financial entries.

Each run revisits the most recent seven days and up to seven older days per
account for backfill or weekly historical checks. Imports validate page numbers,
page sizes, total pages, total records, row scope, timestamps and costs. The cap
is 100,000 raw records per account/day; an oversized day fails rather than being
silently truncated. Older-day timezone semantics still need reconciliation
against provider reports; out-of-day timestamps fail validation.

Review snapshots from Integrations → Review imported days. A closed day needs two
identical complete observations at least five minutes apart before acceptance.
This is an observation rule, not a provider completeness guarantee. Acceptance
requires a fresh snapshot (within 24 hours), unchanged configuration and an
explicit confirmation that counts, wholesale totals and client prices were
reviewed. New occurrence records and financial entries commit atomically.

After acceptance, additions require a new review. Decreased occurrence counts,
changed costs or changed rate attributes block acceptance. Investigate corrections
with the provider; automated reversals and manual adjustment UI are not included.
Same-count substitutions with identical attributes cannot be detected without
provider IDs. Previously imported provider-ID days cannot also be accepted through
reconciliation. Switching back to provider-ID billing after acceptance is blocked
pending a reviewed cutover. Prior retail charges are never recalculated.

Shadow records are administrator-only. Once accepted, rated calls appear in
customer history and its Excel export. Export contains retail costs and local SIP
labels, no credentials or wholesale data. All text cells are literal strings,
never formulas; dates and amounts are numeric Excel cells. The export follows
UTC date and destination filters, caps at 50,000 calls, and never silently truncates.

## Optional manual CSV verification

From an imported day choose **Compare manual provider CSV**. Upload an original
DID Logic CSV under 900 KB, select its actual UTC offset on the call date, and
confirm the export covers the whole account/day. UTF-8 CSV is supported; XLSX
uploads are not. Date/Time are interpreted as MM/DD/YY and 12-hour time. Choose
the export's actual offset including DST; a single file comparison cannot model
an offset change within the exported period.

The comparison preserves occurrence counts and checks timestamps, normalised
caller/destination, duration and wholesale charge. Caller display-name wrappers
are stripped for this comparison only. Different accounts/days and zero-duration
zero-cost records are counted separately. Charged zero-duration rows are rejected.
Reports show counts, seconds, wholesale totals, and downloadable differences.
The uploaded file is not retained; its hash, comparison details, actor and snapshot
digest are retained in an append-only report. Reports never post financial entries.

If a report exists, a mismatch or a changed snapshot blocks day acceptance until
a matching CSV is uploaded. Manual CSV checks are optional for other days.
A match verifies observed record agreement, not provider completeness or unique
call identity, and does not independently validate retail pricing.

The design considerations below describe the approach and its limits. Unattended
automatic acceptance, provider-ID webhooks and correction adjustments remain
future work, not enabled features.

## Evidence and limits

The observed response fields are `amd_score`, `amd_status`, `amount`,
`destination_name`, `duration`, `from`, `hangup_cause`, `sip_account`,
`timestamp`, `to`, and `type`. No explicit call ID is present. Observed timestamps
have second precision. One tenant may own several SIP accounts.

A timestamp checkpoint reduces retrieval work but does not identify a call.
A hash cannot distinguish identical records. Occurrence counts can preserve
identical calls only if complete provider exports contain each call once.
Without that property, exact billing cannot be guaranteed from this data alone.

## Proposed import process

1. Use an explicit integration mode: provider ID or snapshot reconciliation.
   Keep the current provider-ID mode as the default. Plan B starts in shadow mode:
   collect and compare records without posting customer charges.
2. Retrieve complete UTC-day partitions per provider account and SIP account.
   On the first run, start at the configured billing date. Afterwards revisit
   overlapping days from the last successfully reconciled partition, rather
   than requesting only timestamps strictly greater than the last call.
   Size the lookback and settlement delay from measured provider behaviour;
   neither is yet a confirmed operational value.
3. Stage every page before billing. Validate account mapping, timestamps,
   durations, currency and pagination totals where supplied. Do not assume a
   short page proves completeness unless that pagination contract is verified.
   Failed, truncated or inconsistent scans cannot advance the checkpoint.
4. Build a versioned canonical record key from provider-account scope, SIP
   account, UTC timestamp at supplied precision, caller, destination, call type
   and duration. Preserve original values for investigation. Define number and
   timestamp normalisation explicitly. Exclude mutable descriptive fields and
   provider cost from identity; retain them as comparison attributes.
5. Compare multisets, not sets: store each key's occurrence count. Two matching
   records represent two occurrences, not one. Internal occurrence numbers
   1..N provide idempotency keys; page position never determines identity.
6. Accept additions automatically only under a verified append-only/finalised
   export contract. Otherwise quarantine changed partitions for review: a new
   key might be a correction of an old call, not an additional call. Decreased
   counts, changed costs or ambiguous changes must never silently create new
   debits or remove old ones.
7. Under account/partition locking, atomically accept the snapshot, insert new
   occurrence records, post ledger debits, update balances and advance the
   partition checkpoint. Refactor the existing per-call commit boundary for
   this path. Unique database constraints provide retry protection. Stage large
   downloads outside the billing transaction.
8. Reconcile older periods on a configurable schedule within provider retention.
   Surface unresolved differences and the oldest unreconciled partition in the
   admin dashboard. A recent worker run is not proof of complete billing.

Fetching a live day can shift offset-based pages. Prefer provider snapshot/cursor
support or settled exports. Repeating a scan and comparing counts and digests
helps detect instability but does not prove completeness. Live-day data may be
shown as provisional; post debits only under the validated acceptance rules.

## Storage and billing changes

- Import runs: integration scope, mode, canonicalisation version, status and
  diagnostic metadata, without API credentials.
- Partition snapshots: account/day, fetched pages and totals, digest, acquisition
  time, acceptance status and reconciliation reason.
- Occurrence records: canonical key, occurrence number, retained provider
  attributes, linked rated call and ledger entry; unique within provider scope.
- Partition checkpoints independent of the maximum call timestamp, including
  successfully fetched empty days.
- Append-only adjustments for approved corrections, linked to original charges.
  Never edit financial history in place.

Retain the original retail rate on already billed calls. Specify historical-rate
policy before backfilling; current rates must not silently reprice prior debits.
Do not label cost/duration-derived effective rates as provider tariff rates.
Keep retained CDR data access-controlled and define retention before rollout.

## Acceptance checks

- Identical calls occurring twice produce exactly two charges; replay adds none.
- Same-second calls with different destinations remain distinct.
- A legitimate late occurrence is charged once when accepted.
- Reordered pages do not change the result; inconsistent pagination is held.
- Network failure on the final page produces no accepted partial snapshot.
- Crashes and concurrent workers do not duplicate ledger entries or checkpoints.
- Corrections, removals and changing duration/cost trigger reconciliation holds.
- Empty days, UTC boundaries and multiple SIP accounts reconcile independently.
- Existing charges retain their rates after client pricing changes.
- Switching from ID-based billing cannot rebill historical calls: require an
  explicit cutover boundary and reconciliation of any overlapping interval.

## Rollout decision

If DID Logic confirms no ID exists, first validate pagination, duplicate-row,
late-arrival, correction, retention and timestamp semantics using its response
and repeated real exports. Implement the staged collector and shadow comparison,
then compare call counts and wholesale totals with provider reports over an
agreed observation period. Enable charging only for partitions whose acceptance
rules are satisfied. If the API cannot supply reliable exports, use a provider
CDR export or upstream PBX/SBC records with unique IDs and adequate call coverage;
do not present a locally generated fingerprint as proof of unique calls.

Delayed reconciliation increases prepaid exposure. Provider/PBX call controls
remain necessary; this import design does not stop active calls at zero balance.
