from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from flask import g
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from app.billing import bill_call, renew_dids
from app.models import AuditEvent, Call, Did, Integration, Ledger, Tenant, User, db, utcnow


@pytest.fixture
def admin_client(app, client):
    db.session.add(User(email='admin@example.test', password_hash=generate_password_hash('admin-test-password'), is_admin=True))
    db.session.commit()
    client.post('/login', data={'email': 'admin@example.test', 'password': 'admin-test-password'})
    return client


def pricing(**overrides):
    return {'name': 'Real Client', 'landline_rate': '0.04', 'mobile_rate': '0.07', 'fallback_rate': '',
            'billing_increment': '1', 'cli_initial_cost': '8', 'cli_setup_cost': '12', 'cli_monthly_cost': '5',
            'email': 'new@example.test', 'password': 'customer-test-password', 'active': 'on', **overrides}


def test_vendor_cost_pricing_exact_charge_and_replay(app, admin_client):
    response = admin_client.post('/admin/clients/1', data=pricing(action='settings', vendor_cost_pricing='on', billing_increment='60'))
    assert response.status_code == 302
    tenant = db.session.get(Tenant, 1)
    assert tenant.vendor_cost_pricing is True
    before = tenant.balance
    started = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert bill_call(1, 'vendor-pass-through', started, '33123456789', 5, wholesale_cost='0.00083')
    call = db.session.scalar(select(Call).where(Call.vendor_call_id == 'vendor-pass-through'))
    assert call.cost == call.wholesale_cost == Decimal('0.000830')
    assert call.billed_seconds == 5
    assert call.rate_label == 'Vendor cost (effective rate)'
    assert tenant.balance == before - Decimal('0.000830')
    response = admin_client.post('/admin/clients/1', data=pricing(action='settings'))
    assert response.status_code == 302
    assert not bill_call(1, 'vendor-pass-through', started, '33123456789', 5, wholesale_cost='0.00083')
    assert db.session.get(Tenant, 1).balance == before - Decimal('0.000830')


def test_vendor_cost_missing_fails_and_zero_is_valid(app):
    tenant = db.session.get(Tenant, 1)
    tenant.vendor_cost_pricing = True
    db.session.commit()
    before = tenant.balance
    started = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match='actual provider charge'):
        bill_call(1, 'missing-cost', started, '447700900123', 10)
    assert db.session.scalar(select(func.count(Call.id))) == 0
    assert tenant.balance == before
    assert bill_call(1, 'free-call', started, '447700900123', 10, wholesale_cost=0)
    assert tenant.balance == before


def test_customer_cannot_access_admin(signed_in):
    for path in ('/admin/', '/admin/clients', '/admin/clients/new', '/admin/clients/1', '/admin/integrations', '/admin/calls'):
        assert signed_in.get(path).status_code == 403
        assert signed_in.post(path, data={'action': 'topup', 'amount': '9999'}).status_code in (403, 405)


def test_admin_workspace_pages_and_redirect(admin_client):
    assert admin_client.get('/').location.endswith('/admin/')
    for path in ('/admin/', '/admin/clients', '/admin/clients/new', '/admin/clients/1', '/admin/integrations', '/admin/calls'):
        response = admin_client.get(path)
        assert response.status_code == 200, (path, response.data)


def test_bootstrap_admin_creates_no_company_or_calls(app):
    tenants_before = db.session.scalar(select(func.count(Tenant.id)))
    result = app.test_cli_runner().invoke(args=['create-admin', '--email', 'owner@example.test', '--password', 'owner-secure-password'])
    assert result.exit_code == 0, result.output
    admin = db.session.scalar(select(User).where(User.is_admin.is_(True)))
    assert admin.tenant_id is None
    assert db.session.scalar(select(func.count(Tenant.id))) == tenants_before
    assert db.session.scalar(select(func.count(Call.id))) == 0
    repeated = app.test_cli_runner().invoke(args=['create-admin', '--email', 'owner2@example.test', '--password', 'owner-secure-password'])
    assert repeated.exit_code != 0


def test_create_client_and_apply_own_rates(app, admin_client):
    response = admin_client.post('/admin/clients/new', data=pricing())
    assert response.status_code == 302
    tenant = db.session.scalar(select(Tenant).where(Tenant.name == 'Real Client'))
    assert tenant.landline_rate == Decimal('0.04')
    assert tenant.mobile_rate == Decimal('0.07')
    assert tenant.cli_setup_cost == 12
    assert tenant.balance == 0
    assert not db.session.scalar(select(User).where(User.tenant_id == tenant.id)).is_admin
    # Change a mapped existing client's rates; one-second calls use their plan.
    response = admin_client.post('/admin/clients/1', data=pricing(action='settings', billing_increment='60'))
    assert response.status_code == 302
    assert bill_call(1, 'custom-rate', datetime(2025, 1, 1, tzinfo=timezone.utc), '447700900123', 1,
                     wholesale_rate='0.02', wholesale_cost='0.000333')
    call = db.session.scalar(select(Call))
    assert call.cost == Decimal('0.070000')
    assert call.wholesale_cost == Decimal('0.000333')
    assert b'0.020000' in admin_client.get('/admin/calls').data
    # A subsequent pricing edit does not rerate a duplicate or an existing call.
    admin_client.post('/admin/clients/1', data=pricing(action='settings', mobile_rate='0.09'))
    assert not bill_call(1, 'custom-rate', datetime(2025, 1, 1, tzinfo=timezone.utc), '447700900123', 1)
    assert db.session.get(Call, call.id).cost == Decimal('0.070000')


def test_new_number_charges_once_then_recurring_only(app, admin_client):
    data = {'action': 'did', 'mode': 'new', 'number': '+442071234567', 'initial_cost': '8', 'setup_cost': '12',
            'monthly_charge': '5', 'confirm_charge': 'on'}
    assert admin_client.post('/admin/clients/1', data=data).status_code == 302
    did = db.session.scalar(select(Did))
    assert db.session.get(Tenant, 1).balance == -20
    assert admin_client.post('/admin/clients/1', data=data).status_code == 400
    assert db.session.get(Tenant, 1).balance == -20
    assert renew_dids(did.next_billing_date) == 1
    assert db.session.get(Tenant, 1).balance == -25
    assert db.session.scalar(select(func.count(Ledger.id))) == 3


def test_existing_number_does_not_charge_setup(app, admin_client):
    data = {'action': 'did', 'mode': 'existing', 'number': '+442071234567', 'initial_cost': '99', 'setup_cost': '99',
            'monthly_charge': '5', 'next_billing_date': utcnow().date().isoformat()}
    assert admin_client.post('/admin/clients/1', data=data).status_code == 302
    assert db.session.get(Tenant, 1).balance == 0
    assert db.session.scalar(select(Did)).setup_cost == 0


def test_token_encrypted_masked_and_test_does_not_bill(app, admin_client, monkeypatch):
    token = 'private-provider-token-for-unit-test'
    response = admin_client.post('/admin/integrations', data={'action': 'save', 'api_token': token, 'call_id_field': 'id',
        'start_date': '2025-01-01', 'currency_confirmed': 'on'})
    assert response.status_code == 302
    integration = db.session.get(Integration, 1)
    assert token not in integration.token_encrypted
    assert Fernet(app.config['ENCRYPTION_KEY']).decrypt(integration.token_encrypted.encode()).decode() == token
    assert not integration.enabled
    assert token.encode() not in admin_client.get('/admin/integrations').data
    monkeypatch.setattr('app.admin.preview_calls', lambda config: (['id', 'duration', 'amount'], [
        {'call_id': 'sample-real-contract', 'timestamp': '2025-01-01T12:00:00Z', 'sip_account': 'provider-1', 'to': '+447700900123', 'duration': '60', 'per_minute': '0.01', 'amount': '0.01'}]))
    assert admin_client.post('/admin/integrations', data={'action': 'test'}).status_code == 302
    assert db.session.scalar(select(func.count(Ledger.id))) == 0
    assert db.session.scalar(select(func.count(Call.id))) == 0
    assert admin_client.post('/admin/integrations', data={'action': 'enable'}).status_code == 400
    assert admin_client.post('/admin/integrations', data={'action': 'enable', 'id_confirmed': 'on'}).status_code == 302
    assert db.session.get(Integration, 1).enabled
    admin_client.post('/admin/integrations', data={'action': 'save', 'api_token': 'replacement-private-token'})
    assert not db.session.get(Integration, 1).enabled
    assert not db.session.get(Integration, 1).last_test_ok
    assert token not in str([event.details for event in db.session.scalars(select(AuditEvent))])


def test_duplicate_email_rolls_back_company_and_invalid_price(app, admin_client):
    before = db.session.scalar(select(func.count(Tenant.id)))
    assert admin_client.post('/admin/clients/new', data=pricing(email='a@example.test')).status_code == 400
    assert db.session.scalar(select(func.count(Tenant.id))) == before
    assert admin_client.post('/admin/clients/new', data=pricing(mobile_rate='-1')).status_code == 400
    assert db.session.scalar(select(func.count(Tenant.id))) == before


def test_admin_topup_requires_verified_payment_and_is_idempotent(app, admin_client):
    payload = {'action': 'topup', 'amount': '30', 'reference': 'actual-payment-reference'}
    assert admin_client.post('/admin/clients/1', data=payload).status_code == 400
    payload['payment_verified'] = 'on'
    assert admin_client.post('/admin/clients/1', data=payload).status_code == 302
    assert admin_client.post('/admin/clients/1', data=payload).status_code == 302
    assert db.session.get(Tenant, 1).balance == 30


def test_customer_pages_never_show_wholesale(app, signed_in):
    bill_call(1, 'cost-private', datetime(2025, 1, 1, tzinfo=timezone.utc), '447700900123', 60,
              wholesale_rate='0.012345', wholesale_cost='0.012345')
    for path in ('/', '/calls', '/billing'):
        assert b'0.012345' not in signed_in.get(path).data
