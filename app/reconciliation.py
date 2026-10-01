"""Complete-day, occurrence-preserving CDR collection and reviewed billing.

Hashes are matching keys, not proof of call identity. No shadow run posts money.
"""
import hashlib
import json
import math
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from .billing import bill_call, bill_inbound, decimal_amount, normalise_number
from .integrations import decrypt_token, read_calls
from .models import AuditEvent, Call, CdrPartition, CsvReconciliation, Did, Integration, SipAccount, db, utcnow

MAX_RECORDS = 100000
LOOKBACK_DAYS = 7


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def canonical_rows(rows, account, day):
    groups = {}
    inbound = isinstance(account, Did)
    for row in rows:
        if inbound:
            if row.get('type') != 'incoming':
                raise ValueError('Unexpected type in inbound export.')
            if normalise_number(row.get('did_number')) != normalise_number(account.number):
                continue
        elif row.get('type') != 'sip' or str(row.get('sip_account')) != account.provider_id:
            raise ValueError('CDR account or call type does not match the requested partition.')
        duration = row.get('duration')
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
            raise ValueError('CDR duration must be a non-negative integer.')
        try:
            started = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
        except (KeyError, TypeError, AttributeError, ValueError):
            raise ValueError('CDR timestamp is invalid.') from None
        if started.tzinfo is None or started.astimezone(timezone.utc).date() != day or started > utcnow():
            raise ValueError('CDR timestamp is outside the requested UTC day.')
        if duration == 0 and not inbound:
            continue
        caller = row.get('from')
        if not isinstance(caller, str) or not caller or len(caller) > 500:
            raise ValueError('CDR caller is missing or invalid.')
        # Keep caller presentation intact; lossy normalisation can merge calls.
        identity = [1, account.id, account.provider_id, started.astimezone(timezone.utc).isoformat(),
                    caller, normalise_number(account.number if inbound else row.get('to')), duration, 'incoming' if inbound else 'sip']
        if inbound:
            # The receiving DID owns the charge; forwarding targets are not tenant identifiers.
            target = row.get('to')
            if not isinstance(target, str) or not target or len(target) > 500:
                raise ValueError('Inbound forwarding destination is invalid.')
            identity.append(target)
        if row.get('amount') is None:
            raise ValueError('CDR provider charge is missing.')
        cost = decimal_amount(row['amount'])
        rate = decimal_amount(row['per_minute']) if row.get('per_minute') is not None else None
        if cost < 0 or (rate is not None and rate < 0):
            raise ValueError('CDR provider amounts must be non-negative.')
        if inbound and duration == 0 and cost == 0:
            continue
        key = fingerprint(identity)
        attributes = {'identity': identity, 'cost': str(cost), 'rate': str(rate) if rate is not None else None}
        if key in groups:
            if any(groups[key][k] != attributes[k] for k in attributes):
                raise ValueError('Indistinguishable calls have different costs; manual reconciliation is required.')
            groups[key]['count'] += 1
        else:
            groups[key] = {**attributes, 'count': 1}
    return groups


def fetch_partition(token, account, day, inbound_cache=None):
    inbound = isinstance(account, Did)
    if inbound and inbound_cache is not None and day in inbound_cache:
        return canonical_rows(inbound_cache[day], account, day)
    rows, expected = [], None
    page = 1
    while True:
        params = {'type': 'incoming' if inbound else 'sip', 'missed': '1' if inbound else '0',
                  'from': day.isoformat(), 'to': day.isoformat(), 'page': page, 'per_page': 1000}
        if not inbound:
            params['sip_account'] = account.provider_id
        payload = read_calls(token, params)
        meta = payload['pagination']
        values = [meta.get(k) for k in ('page', 'per_page', 'total_pages', 'total_records')]
        if any(isinstance(v, bool) or not isinstance(v, int) for v in values):
            raise ValueError('Provider pagination metadata is incomplete.')
        current, size, pages, total = values
        if current != page or not 1 <= size <= 1000 or not 0 <= total <= MAX_RECORDS:
            raise ValueError('Provider pagination is invalid or exceeds the 100,000 record/day limit.')
        if pages not in ({0, 1} if total == 0 else {math.ceil(total / size)}):
            raise ValueError('Provider page count does not match the record total.')
        signature = (size, pages, total)
        if expected is not None and expected != signature:
            raise ValueError('Provider totals changed during pagination; retry on the next cycle.')
        expected = signature
        if len(payload['calls']) != min(size, max(0, total - (page - 1) * size)):
            raise ValueError('Provider returned an incomplete page.')
        rows.extend(payload['calls'])
        if page >= pages:
            break
        page += 1
    if len(rows) != expected[2]:
        raise ValueError('Provider export count mismatch.')
    if inbound and inbound_cache is not None:
        inbound_cache[day] = rows
    return canonical_rows(rows, account, day)


def partition_scope(account):
    return CdrPartition.did_id == account.id if isinstance(account, Did) else CdrPartition.sip_account_id == account.id


def has_corrections(accepted, latest):
    return any(key not in latest or latest[key]['count'] < old['count']
               or any(latest[key][name] != old[name] for name in ('identity', 'cost', 'rate'))
               for key, old in accepted.items())


def save_snapshot(account, day, groups, version):
    # Same lock order as approval and integration settings changes.
    config = db.session.execute(select(Integration).where(Integration.id == 1).with_for_update()
                                .execution_options(populate_existing=True)).scalar_one()
    if not config.reconciliation_enabled or config.token_version != version:
        raise ValueError('Integration changed during collection; snapshot discarded.')
    partition = db.session.scalar(select(CdrPartition).where(partition_scope(account),
                                                           CdrPartition.day == day).with_for_update())
    now, digest = utcnow(), fingerprint(groups)
    if partition is None:
        source = {'did_id': account.id} if isinstance(account, Did) else {'sip_account_id': account.id}
        partition = CdrPartition(**source, day=day, digest=digest, token_version=version,
                                 rows=groups, accepted_rows={}, observations=1, first_seen=now, checked_at=now)
        db.session.add(partition)
    elif partition.digest == digest and partition.token_version == version:
        if now - utc(partition.checked_at) >= timedelta(minutes=5):
            partition.observations += 1
    else:
        partition.digest, partition.rows = digest, groups
        partition.token_version, partition.observations, partition.first_seen = version, 1, now
    partition.checked_at = now
    partition.record_count = sum(row['count'] for row in groups.values())
    partition.wholesale_total = sum((Decimal(row['cost']) * row['count'] for row in groups.values()), Decimal(0))
    if has_corrections(partition.accepted_rows, groups):
        partition.status = 'correction'
    elif partition.accepted_at and partition.accepted_rows == groups:
        partition.status = 'accepted'
    elif day < now.date() and partition.observations >= 2:
        partition.status = 'review'
    else:
        partition.status = 'observing'
    db.session.commit()


def collect(config):
    if not config.token_encrypted or not config.last_test_ok or not config.start_date or not config.currency_confirmed:
        raise ValueError('Shadow collection requires a tested connection, start date and USD confirmation.')
    if config.start_date > utcnow().date():
        raise ValueError('Collection start cannot be in the future.')
    token, version, start = decrypt_token(config), config.token_version, config.start_date
    accounts = db.session.scalars(select(SipAccount).where(SipAccount.active.is_(True)).order_by(SipAccount.id)).all()
    accounts += db.session.scalars(select(Did).where(Did.active.is_(True)).order_by(Did.id)).all()
    inbound_cache = {}
    collected, failed = 0, []
    first_error = None
    for account in accounts:
        account_id = account.id
        today = utcnow().date()
        existing = {p.day: p for p in db.session.scalars(select(CdrPartition).where(partition_scope(account)))}
        recent_start = max(start, today - timedelta(days=LOOKBACK_DAYS - 1))
        days = {recent_start + timedelta(days=n) for n in range((today - recent_start).days + 1)}
        # Backfill seven old days per cycle; revisit older partitions weekly.
        day, extra = start, 0
        while day < recent_start and extra < 7:
            old = existing.get(day)
            if old is None or old.observations < 2 or utc(old.checked_at) < utcnow() - timedelta(days=7):
                days.add(day)
                extra += 1
            day += timedelta(days=1)
        for day in sorted(days):
            try:
                groups = fetch_partition(token, account, day, inbound_cache) if isinstance(account, Did) else fetch_partition(token, account, day)
                save_snapshot(account, day, groups, version)
                collected += 1
            except Exception as exc:
                db.session.rollback()
                if first_error is None:
                    first_error = str(exc)[:160] if isinstance(exc, ValueError) else 'Unexpected provider data or database error.'
                failed.append((('DID ' if isinstance(account, Did) else 'SIP ') + str(account_id), day.isoformat()))
                # A previously reviewable snapshot cannot remain approvable after
                # an unsuccessful refresh. Preserve its data for investigation.
                partition = db.session.scalar(select(CdrPartition).where(partition_scope(account),
                                                                         CdrPartition.day == day))
                if partition:
                    partition.status, partition.observations = 'fetch_error', 0
                db.session.commit()
        if isinstance(account, SipAccount):
            account.last_sync_error = any(a == f'SIP {account_id}' for a, _ in failed)
        db.session.commit()
    config = db.session.get(Integration, 1)
    config.last_sync_at, config.sync_requested = utcnow(), False
    config.last_sync_message = f'Shadow collection: {collected} day snapshots checked; {len(failed)} failed. No charges posted.'
    if failed:
        config.last_sync_message += f' Failed account/day: {failed[:3]}.'
    mapped = {normalise_number(a.number) for a in accounts if isinstance(a, Did)}
    if not mapped:
        config.last_sync_message += ' Inbound not collected: assign active DIDs to clients.'
    else:
        unmapped = 0
        for rows in inbound_cache.values():
            for row in rows:
                try:
                    unmapped += int(normalise_number(row.get('did_number')) not in mapped)
                except ValueError:
                    unmapped += 1
        if unmapped:
            config.last_sync_message += f' {unmapped} inbound records have unassigned DIDs; not billed.'
    config.last_sync_message = config.last_sync_message[:300]
    db.session.commit()
    if failed:
        raise ValueError(f'Shadow collection failed for account/day partitions: {failed[:10]}. {first_error} No charges posted.')
    return {'enabled': True, 'mode': 'shadow', 'partitions': collected, 'imported': 0}


def approve(partition_id, digest, actor_id):
    """Explicitly reviewed additions only. All financial writes share one transaction."""
    try:
        config = db.session.execute(select(Integration).where(Integration.id == 1).with_for_update()
                                    .execution_options(populate_existing=True)).scalar_one()
        partition = db.session.scalar(select(CdrPartition).where(CdrPartition.id == partition_id).with_for_update())
        if not partition or not config.reconciliation_enabled or not config.currency_confirmed:
            raise ValueError('Enable shadow collection and confirm currency before accepting a partition.')
        if partition.accepted_at and partition.accepted_rows == partition.rows and partition.digest == digest:
            return 0
        if (partition.digest != digest or partition.token_version != config.token_version
                or partition.status != 'review' or partition.day >= utcnow().date()
                or utcnow() - utc(partition.checked_at) > timedelta(hours=24)):
            raise ValueError('Snapshot changed, is stale, or has not passed repeated collection. Refresh before approval.')
        if has_corrections(partition.accepted_rows, partition.rows):
            raise ValueError('Existing charges need reconciliation; corrections cannot be billed as new calls.')
        comparison = db.session.scalar(select(CsvReconciliation).where(CsvReconciliation.partition_id == partition.id)
                                       .order_by(CsvReconciliation.id.desc()).limit(1))
        if comparison and (not comparison.report['matched'] or comparison.report['snapshot_digest'] != partition.digest):
            raise ValueError('The latest CSV comparison differs or is stale. Reconcile and upload a matching export before acceptance.')
        account = partition.account
        if not account.active:
            raise ValueError('This SIP account is inactive.')
        # Prevent switching import strategies from double-billing a day.
        start = datetime.combine(partition.day, time.min, tzinfo=timezone.utc)
        scope = Call.did_id == account.id if partition.inbound else Call.sip_account_id == account.id
        legacy = db.session.scalar(select(Call.id).where(scope, Call.started_at >= start,
            Call.started_at < start + timedelta(days=1), ~Call.vendor_call_id.startswith('recon:v1:')))
        if legacy:
            raise ValueError('This day already contains provider-ID imports; reconcile the cutover before billing.')
        imported = 0
        for key, group in sorted(partition.rows.items()):
            old_count = partition.accepted_rows.get(key, {}).get('count', 0)
            identity = group['identity']
            for occurrence in range(old_count + 1, group['count'] + 1):
                if partition.inbound:
                    imported += int(bill_inbound(account.id, f'recon:v1:{key}:{occurrence}',
                        datetime.fromisoformat(identity[3]), identity[6], group['cost'], commit=False))
                else:
                    imported += int(bill_call(account.id, f'recon:v1:{key}:{occurrence}',
                        datetime.fromisoformat(identity[3]), identity[5], identity[6],
                        wholesale_rate=group['rate'], wholesale_cost=group['cost'], commit=False))
        partition.accepted_rows = partition.rows
        partition.accepted_at, partition.status = utcnow(), 'accepted'
        db.session.add(AuditEvent(actor_id=actor_id, action='reconciliation.accept', target=str(partition.id),
                                 details={'digest': digest, 'new_calls': imported, 'day': partition.day.isoformat()}))
        db.session.commit()
        return imported
    except Exception:
        db.session.rollback()
        raise
