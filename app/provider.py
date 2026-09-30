"""DID Logic adapter. Requires saved, tested administrator configuration."""
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone

import requests
from sqlalchemy import select, text

from .billing import bill_call
from .integrations import API_URL, decrypt_token, read_calls, settings
from .models import SipAccount, db, utcnow


def parse_call(row, account, id_field):
    if row.get('type') != 'sip':
        raise ValueError('Unexpected call type in outbound response.')
    duration = row.get('duration')
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
        raise ValueError('Invalid provider duration.')
    if duration == 0:
        return None
    if str(row.get('sip_account')) != account.provider_id:
        raise ValueError('Provider SIP account does not match the local mapping.')
    call_id = row.get(id_field)
    if isinstance(call_id, bool) or not isinstance(call_id, (str, int)) or not str(call_id).strip():
        raise ValueError('Provider response lacks a stable unique call ID; no charge posted.')
    started = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
    if started.tzinfo is None:
        raise ValueError('Provider timestamp must include a timezone.')
    return str(call_id), started.astimezone(timezone.utc), row['to'], duration


@contextmanager
def sync_lock():
    if db.engine.dialect.name != 'postgresql':
        yield True
        return
    with db.engine.connect() as connection:
        acquired = connection.execute(text('SELECT pg_try_advisory_lock(730022)')).scalar()
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text('SELECT pg_advisory_unlock(730022)'))


def sync_calls():
    with sync_lock() as acquired:
        if not acquired:
            return {'enabled': True, 'busy': True, 'imported': 0}
        return _sync_calls()


def _sync_calls():
    integration = settings()
    if not integration or not integration.enabled:
        return {'enabled': False, 'imported': 0}
    if not integration.token_encrypted or not integration.call_id_field or not integration.start_date or not integration.last_test_ok or not integration.currency_confirmed:
        raise ValueError('Live sync requires a tested token, confirmed ID field, USD confirmation and start date.')
    token, id_field, version = decrypt_token(integration), integration.call_id_field, integration.token_version
    initial = datetime.combine(integration.start_date, time.min, tzinfo=timezone.utc)
    now = utcnow()
    if initial > now:
        raise ValueError('Sync start cannot be in the future.')
    imported, failures = 0, []
    accounts = db.session.scalars(select(SipAccount).where(SipAccount.active.is_(True))).all()
    with requests.Session() as client:
        for account in accounts:
            try:
                since = account.synced_through
                since = max(initial, since.replace(tzinfo=timezone.utc) - timedelta(days=1)) if since else initial
                day = since.date()
                while day <= now.date():
                    db.session.refresh(integration)
                    if not integration.enabled or integration.token_version != version:
                        return {'enabled': integration.enabled, 'interrupted': True, 'imported': imported}
                    page, seen_pages = 1, set()
                    while True:
                        payload = read_calls(token, {'type': 'sip', 'sip_account': account.provider_id,
                            'missed': '0', 'from': day.isoformat(), 'to': day.isoformat(),
                            'page': page, 'per_page': 1000}, client)
                        rows = payload['calls']
                        parsed = [parse_call(row, account, id_field) for row in rows]
                        signature = tuple(item[0] for item in parsed if item)
                        if rows and signature in seen_pages:
                            raise ValueError('Provider repeated a page; checkpoint preserved.')
                        seen_pages.add(signature)
                        for row, item in zip(rows, parsed):
                            if item and initial <= item[1] <= now:
                                imported += int(bill_call(account.id, *item, wholesale_rate=row.get('per_minute'), wholesale_cost=row.get('amount')))
                        if len(rows) < 1000:
                            break
                        page += 1
                        if page > 10000:
                            raise ValueError('Provider pagination exceeded safety limit.')
                    day += timedelta(days=1)
                account.synced_through = now
                account.last_sync_error = False
                db.session.commit()
            except Exception:
                db.session.rollback()
                account.last_sync_error = True
                db.session.commit()
                failures.append(account.id)
    integration = settings()
    integration.sync_requested = False
    integration.last_sync_at = utcnow()
    integration.last_sync_message = (f'Imported {imported} calls. Failed local account IDs: {failures}.' if failures
                                     else f'Imported {imported} new calls across {len(accounts)} mapped accounts.')[:300]
    db.session.commit()
    if failures:
        raise RuntimeError(f'CDR sync failed for local account IDs {failures}; check mapping, client rates and provider schema. Checkpoints preserved.')
    return {'enabled': True, 'imported': imported}
