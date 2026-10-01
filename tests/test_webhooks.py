import hashlib
from datetime import date
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from app.models import Call, Did, Ledger, Tenant, User, WebhookEvent, WebhookSettings, db

SECRET = 'test-only-webhook-secret'
PATH = '/webhooks/didlogic/' + SECRET


def event(**changes):
    return {'event': 'cdr', 'callid': 'test-call-1', 'direction': 'outbound', 'src': '+442071234567',
            'dst': '447700900123', 'billsec': 5, 'duration': 9, 'disposition': 'ANSWERED',
            'calldate': '2025-01-01T12:00:00Z', 'user_id': 999, **changes}


@pytest.fixture
def receiver(app):
    config = WebhookSettings(id=1, enabled=True, secret_hash=hashlib.sha256(SECRET.encode()).hexdigest(),
                            secret_encrypted=Fernet(app.config['ENCRYPTION_KEY']).encrypt(SECRET.encode()).decode())
    db.session.add_all([config, Did(tenant_id=1, number='+442071234567', monthly_charge=5,
                                   billing_day=1, next_billing_date=date(2027, 1, 1))])
    db.session.commit()
    return config


def test_auth_csrf_independence_duplicates_conflicts_no_charges(receiver, app, client):
    app.config['WTF_CSRF_ENABLED'] = True
    assert client.post('/webhooks/didlogic/wrong', json=event()).status_code == 404
    assert db.session.scalar(select(func.count(WebhookEvent.id))) == 0
    assert client.post(PATH, json=event()).status_code == 200
    assert client.post(PATH, json=event()).status_code == 200
    record = db.session.scalar(select(WebhookEvent))
    assert record.tenant_id == 1 and record.status == 'mapped' and record.deliveries == 2
    assert record.payload['billsec'] == 5 and record.payload['duration'] == 9
    assert 'user_id' not in record.payload
    assert client.post(PATH, json=event(billsec=6)).status_code == 200
    assert record.status == 'conflict' and record.payload['billsec'] == 5
    assert db.session.scalar(select(func.count(WebhookEvent.id))) == 1
    assert db.session.scalar(select(func.count(Call.id))) == 0
    assert db.session.scalar(select(func.count(Ledger.id))) == 0
    assert db.session.get(Tenant, 1).balance == 0


def test_inbound_uses_destination_outbound_unknown_held(receiver, client):
    assert client.post(PATH, json=event(direction='inbound', src='anonymous', dst='442071234567')).json['status'] == 'mapped'
    assert client.post(PATH, json=event(callid='unknown', src='anonymous')).json['status'] == 'unmapped'
    assert db.session.scalar(select(func.count(WebhookEvent.id))) == 2


def test_validation_pause_and_failed_commit_retry(receiver, client):
    assert client.post(PATH, json=event(billsec=10)).status_code == 400
    assert client.post(PATH, json=event(billsec=True)).status_code == 400
    assert client.post(PATH, json=event(calldate='2025-01-01')).status_code == 400
    assert client.post(PATH, data='not-json', content_type='application/json').status_code == 400
    assert client.post(PATH, data='x' * 66000, content_type='application/json').status_code == 413
    with patch('app.webhooks.db.session.commit', side_effect=RuntimeError('simulated storage failure')):
        assert client.post(PATH, json=event()).status_code == 503
    assert db.session.scalar(select(func.count(WebhookEvent.id))) == 0
    assert client.post(PATH, json=event()).status_code == 200
    receiver.enabled = False
    db.session.commit()
    assert client.post(PATH, json=event()).status_code == 503


def test_admin_inbox_remapping_rotation_customer_denied(receiver, client):
    owner = User(email='hook-admin@example.test', is_admin=True, password_hash=generate_password_hash('webhook-test-password'))
    db.session.add(owner)
    db.session.commit()
    client.post(PATH, json=event(src='442071234568'))
    record = db.session.scalar(select(WebhookEvent))
    client.post('/login', data={'email': owner.email, 'password': 'webhook-test-password'})
    assert client.get('/admin/webhooks').status_code == 200
    assert client.post('/admin/webhooks', data={'action': 'remap', 'event_id': record.id}).status_code == 400
    db.session.add(Did(tenant_id=2, number='+442071234568', monthly_charge=0, billing_day=1, next_billing_date=date(2027, 1, 1)))
    db.session.commit()
    assert client.post('/admin/webhooks', data={'action': 'remap', 'event_id': record.id}).status_code == 302
    assert record.tenant_id == 2 and record.status == 'mapped'
    assert client.post('/admin/webhooks', data={'action': 'rotate'}).status_code == 302
    assert client.post(PATH, json=event()).status_code == 404
    client.post('/logout')
    client.post('/login', data={'email': 'a@example.test', 'password': 'test-password-123'})
    assert client.get('/admin/webhooks').status_code == 403
    assert client.post('/admin/webhooks', data={'action': 'rotate'}).status_code == 403
