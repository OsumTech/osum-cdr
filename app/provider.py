"""DID Logic adapter. Fail closed when its response cannot be safely billed."""
import os
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import select

from .billing import bill_call
from .models import SipAccount, db, utcnow

API_URL = 'https://app.didlogic.com/api/v1/calls'


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


def sync_calls():
    if os.getenv('LIVE_SYNC_ENABLED', 'false').lower() != 'true':
        return {'enabled': False, 'imported': 0}
    token, id_field = os.getenv('DIDLOGIC_TOKEN'), os.getenv('DIDLOGIC_CALL_ID_FIELD')
    if not token or not id_field or not os.getenv('SYNC_START_DATE'):
        raise ValueError('Live sync requires a token, confirmed ID field, and SYNC_START_DATE.')
    initial = datetime.strptime(os.environ['SYNC_START_DATE'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    now = utcnow()
    if initial > now:
        raise ValueError('SYNC_START_DATE cannot be in the future.')
    imported, failures = 0, []
    accounts = db.session.scalars(select(SipAccount).where(SipAccount.active.is_(True))).all()
    with requests.Session() as client:
        client.headers.update({'Authorization': f'Bearer {token}', 'Accept': 'application/json'})
        for account in accounts:
            try:
                since = account.synced_through
                since = max(initial, since.replace(tzinfo=timezone.utc) - timedelta(days=1)) if since else initial
                # API filters are dates, not timestamps. Replay at least a full day
                # for delayed CDRs, and resume all missed days after an outage.
                day = since.date()
                while day <= now.date():
                    page = 1
                    seen_pages = set()
                    while True:
                        response = client.get(API_URL, params={'type': 'sip', 'sip_account': account.provider_id,
                            'missed': '0', 'from': day.isoformat(), 'to': day.isoformat(),
                            'page': page, 'per_page': 1000}, timeout=(10, 60), allow_redirects=False)
                        if response.status_code != 200:
                            raise ValueError(f'Provider returned HTTP {response.status_code}.')
                        payload = response.json()
                        rows = payload.get('calls')
                        if not isinstance(rows, list) or not isinstance(payload.get('pagination'), dict):
                            raise ValueError('Unexpected provider response envelope.')
                        parsed = [parse_call(row, account, id_field) for row in rows]
                        signature = tuple(item[0] for item in parsed if item)
                        if rows and signature in seen_pages:
                            raise ValueError('Provider repeated a page; sync checkpoint not advanced.')
                        seen_pages.add(signature)
                        for item in parsed:
                            if item and initial <= item[1] <= now:
                                imported += int(bill_call(account.id, *item))
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
    if failures:
        # Never log provider response bodies or credentials.
        raise RuntimeError(f'CDR sync failed for local account IDs {failures}; check mapping, rates and provider schema. Checkpoints preserved.')
    return {'enabled': True, 'imported': imported}
