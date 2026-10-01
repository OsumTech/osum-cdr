"""Durable inbox with opt-in, atomic custom-rate outbound billing."""
import hashlib
import hmac
import json
from datetime import datetime, timezone

from flask import Blueprint, current_app, request
from werkzeug.exceptions import RequestEntityTooLarge
from sqlalchemy import select, text

from .billing import billing_mode_lock, locked_tenant, normalise_number, post_entry, rate_call
from .models import Call, Did, WebhookBilling, WebhookEvent, WebhookSettings, db, utcnow

webhooks = Blueprint('webhooks', __name__)


def validate_payload(body):
    if not isinstance(body, dict) or body.get('event') != 'cdr':
        raise ValueError('Expected a CDR event.')
    for field in ('callid', 'direction', 'calldate', 'src', 'dst', 'disposition'):
        if not isinstance(body.get(field), str) or not body[field] or len(body[field]) > (200 if field == 'callid' else 500):
            raise ValueError('Missing or invalid CDR field.')
    if body['direction'] not in ('inbound', 'outbound'):
        raise ValueError('Invalid direction.')
    for field in ('billsec', 'duration'):
        if isinstance(body.get(field), bool) or not isinstance(body.get(field), int) or not 0 <= body[field] <= 604800:
            raise ValueError('Invalid call duration.')
    if body['billsec'] > body['duration']:
        raise ValueError('Billable duration exceeds total duration.')
    try:
        started = datetime.fromisoformat(body['calldate'].replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Invalid call timestamp.') from None
    if started.tzinfo is None:
        raise ValueError('Timestamp requires an explicit timezone.')
    # Retain only documented CDR fields. Unknown fields may contain secrets.
    return {**{field: body[field] for field in ('callid', 'direction', 'src', 'dst', 'billsec', 'duration', 'disposition')},
            'calldate': started.astimezone(timezone.utc).isoformat()}


def mapping(payload):
    try:
        number = '+' + normalise_number(payload['src'] if payload['direction'] == 'outbound' else payload['dst'])
    except ValueError:
        return None
    return db.session.scalar(select(Did).where(Did.number == number, Did.active.is_(True)))


def charge_event(event):
    """Runs only on first receipt, in the same transaction as the inbox write."""
    activation = db.session.get(WebhookBilling, 1)
    payload = event.payload
    started = datetime.fromisoformat(payload['calldate'])
    result = {'state': 'shadow', 'reason': 'Before billing activation'}
    if activation and started >= activation.started_at.replace(tzinfo=timezone.utc):
        result = {'state': 'pending', 'reason': 'Assign CLI before receipt; manual review required'}
        if event.tenant_id:
            tenant = locked_tenant(event.tenant_id)
            if not tenant.active:
                result['reason'] = 'Client is inactive; review required'
            elif event.direction != 'outbound' or tenant.vendor_cost_pricing:
                result['reason'] = 'Awaiting actual vendor charge'
            elif payload['billsec'] == 0:
                result = {'state': 'no_charge', 'reason': 'No billable seconds'}
            elif started > utcnow() or payload['disposition'].upper() != 'ANSWERED':
                result['reason'] = 'Review call timestamp or outcome'
            else:
                try:
                    number, seconds, rate, cost, label = rate_call(payload['dst'], payload['billsec'], tenant)
                except ValueError as exc:
                    result['reason'] = str(exc)
                else:
                    key = 'webhook:' + hashlib.sha256(('outbound:' + event.call_id).encode()).hexdigest()
                    db.session.add(Call(tenant_id=tenant.id, did_id=event.did_id, vendor_call_id=key,
                        started_at=started, destination=number, duration=payload['billsec'],
                        billed_seconds=seconds, rate=rate, cost=cost, rate_label='Webhook / ' + label))
                    post_entry(tenant, 'call:' + key, 'usage', f'Call to +{number} / {seconds}s billed', -cost)
                    result = {'state': 'charged', 'cost': str(cost), 'rate': str(rate), 'seconds': seconds}
    event.payload = {**payload, 'billing': result}


def receive(payload):
    billing_mode_lock()
    signature = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if db.engine.dialect.name == 'postgresql':
        lock = hashlib.sha256((payload['direction'] + ':' + payload['callid']).encode()).hexdigest()
        db.session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(lock[:15], 16)})
    event = db.session.scalar(select(WebhookEvent).where(WebhookEvent.call_id == payload['callid'],
                                                        WebhookEvent.direction == payload['direction']).with_for_update())
    if event:
        event.deliveries += 1
        event.last_received_at = utcnow()
        if event.fingerprint != signature:
            event.status = 'conflict'
        db.session.commit()
        return event
    did = mapping(payload)
    event = WebhookEvent(call_id=payload['callid'], direction=payload['direction'], fingerprint=signature,
        payload=payload, tenant_id=did.tenant_id if did else None, did_id=did.id if did else None,
        status='mapped' if did else 'unmapped')
    db.session.add(event)
    charge_event(event)
    db.session.commit()
    return event


@webhooks.post('/webhooks/didlogic/<secret>')
def didlogic(secret):
    # Authentication is possession of this URL secret, NOT the asserted caller ID.
    try:
        config = db.session.get(WebhookSettings, 1)
        digest = hashlib.sha256(secret.encode()).hexdigest()
        if not config or len(secret) > 128 or not hmac.compare_digest(digest, config.secret_hash):
            return {'error': 'Not found'}, 404
        if not config.enabled:
            return {'error': 'Receiver paused'}, 503
        request.max_content_length = 65536
        payload = validate_payload(request.get_json(silent=True))
        event = receive(payload)
        return {'received': True, 'mode': event.payload.get('billing', {}).get('state', 'shadow'), 'status': event.status}, 200
    except RequestEntityTooLarge:
        db.session.rollback()
        return {'error': 'Payload too large'}, 413
    except ValueError as exc:
        db.session.rollback()
        return {'error': str(exc)}, 400
    except Exception:
        db.session.rollback()
        # Never log the URL bearer secret or raw payload, even on failed writes.
        current_app.logger.error('Webhook storage failed; provider should retry.')
        return {'error': 'Temporarily unavailable'}, 503
