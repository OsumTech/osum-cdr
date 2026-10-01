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
    filtered = client.get('/admin/webhooks?status=unmapped&direction=outbound')
    assert filtered.status_code == 200
    assert b'test-call-1' in filtered.data and b'ANSWERED' in filtered.data
    assert b'test-call-1' not in client.get('/admin/webhooks?status=mapped').data
    assert b'test-call-1' not in client.get('/admin/webhooks?direction=inbound').data
    assert b'test-call-1' not in client.get('/admin/webhooks?tenant_id=2').data
    assert client.get('/admin/webhooks?status=invalid').status_code == 400
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


def test_live_billing_atomic_duplicate_and_cutover(receiver, client):
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal
    from app.models import WebhookBilling
    now = datetime.now(timezone.utc)
    db.session.add(WebhookBilling(id=1, started_at=now - timedelta(minutes=2)))
    tenant = db.session.get(Tenant, 1)
    tenant.landline_rate = Decimal('0.016')
    tenant.billing_increment = 60
    db.session.commit()
    body = event(calldate=(now - timedelta(minutes=1)).isoformat(), dst='441670641217', billsec=57, duration=68)
    with patch('app.webhooks.db.session.commit', side_effect=RuntimeError('test failure')):
        assert client.post(PATH, json=body).status_code == 503
    assert db.session.scalar(select(func.count(Call.id))) == 0
    assert db.session.get(Tenant, 1).balance == 0
    assert client.post(PATH, json=body).json['mode'] == 'charged'
    assert client.post(PATH, json=body).json['mode'] == 'charged'
    assert db.session.scalar(select(func.count(Call.id))) == 1
    assert db.session.scalar(select(func.count(Ledger.id))) == 1
    call = db.session.scalar(select(Call))
    assert call.cost == Decimal('0.016000') and call.billed_seconds == 60
    assert call.wholesale_cost is None and call.tenant_id == 1
    assert db.session.get(Tenant, 1).balance == Decimal('-0.016000')
    assert client.post(PATH, json={**body, 'billsec': 56}).json['status'] == 'conflict'
    assert db.session.get(Tenant, 1).balance == Decimal('-0.016000')
    assert client.post(PATH, json=event(callid='historical')).json['mode'] == 'shadow'
    assert db.session.scalar(select(func.count(Call.id))) == 1


def test_pending_vendor_inbound_missing_rate_and_zero(receiver, client):
    from datetime import datetime, timedelta, timezone
    from app.models import WebhookBilling
    now = datetime.now(timezone.utc)
    db.session.add(WebhookBilling(id=1, started_at=now - timedelta(minutes=2)))
    db.session.commit()
    body = event(calldate=(now - timedelta(minutes=1)).isoformat())
    assert client.post(PATH, json={**body, 'callid': 'incoming', 'direction': 'inbound', 'dst': '442071234567'}).json['mode'] == 'pending'
    assert client.post(PATH, json={**body, 'callid': 'zero', 'billsec': 0}).json['mode'] == 'no_charge'
    assert client.post(PATH, json={**body, 'callid': 'no-rate', 'dst': '15551234567'}).json['mode'] == 'pending'
    db.session.get(Tenant, 1).vendor_cost_pricing = True
    db.session.commit()
    assert client.post(PATH, json={**body, 'callid': 'vendor'}).json['mode'] == 'pending'
    assert db.session.scalar(select(func.count(Ledger.id))) == 0


def test_activation_and_page_sizes(receiver, client):
    from app.models import WebhookBilling
    from app.billing import bill_call
    from datetime import datetime, timezone
    db.session.add(User(email='billing-admin@example.test', is_admin=True, password_hash=generate_password_hash('test-password-123')))
    db.session.commit()
    for i in range(12):
        assert client.post(PATH, json=event(callid=f'page-call-{i}')).status_code == 200
    client.post('/login', data={'email': 'billing-admin@example.test', 'password': 'test-password-123'})
    html = client.get('/admin/webhooks').data
    assert html.count(b'class="call-identifier"') == 10
    assert client.get('/admin/webhooks?per_page=25').data.count(b'class="call-identifier"') == 12
    assert client.get('/admin/webhooks?per_page=11').status_code == 400
    assert client.post('/admin/webhooks', data={'action': 'activate_billing'}).status_code == 302
    activation = db.session.get(WebhookBilling, 1).started_at
    assert client.post('/admin/webhooks', data={'action': 'activate_billing'}).status_code == 302
    assert db.session.get(WebhookBilling, 1).started_at == activation
    assert db.session.scalar(select(func.count(Ledger.id))) == 0
    with pytest.raises(ValueError, match='Historical/API'):
        bill_call(1, 'legacy-call', datetime.now(timezone.utc), '441670641217', 20)
    db.session.rollback()


def test_concurrent_webhook_delivery_charges_once(receiver, app):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta, timezone
    from app.models import WebhookBilling
    if db.engine.dialect.name != 'postgresql':
        pytest.skip('Concurrent billing requires production PostgreSQL locks')
    now = datetime.now(timezone.utc)
    db.session.add(WebhookBilling(id=1, started_at=now - timedelta(minutes=2)))
    db.session.commit()
    body = event(calldate=(now - timedelta(minutes=1)).isoformat())
    def deliver(_):
        with app.test_client() as browser:
            return browser.post(PATH, json=body).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(deliver, range(8))) == [200] * 8
    db.session.expire_all()
    assert db.session.scalar(select(func.count(Call.id))) == 1
    assert db.session.scalar(select(func.count(Ledger.id))) == 1
    assert db.session.scalar(select(WebhookEvent)).deliveries == 8
