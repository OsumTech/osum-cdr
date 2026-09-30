from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO

from pypdf import PdfReader
from sqlalchemy import select

from app.billing import bill_call, top_up
from app.models import Invoice, Tenant, db


def test_authentication_required(client):
    for url in ['/', '/calls', '/trunks', '/billing', '/invoices/1/download']:
        assert client.get(url).status_code == 302


def test_all_pages_render_empty_states(signed_in):
    for url in ['/', '/calls', '/trunks', '/billing']:
        response = signed_in.get(url)
        assert response.status_code == 200
        assert b'Test Company A' in response.data
        assert b'Test Company B' not in response.data
        assert response.headers['Cache-Control'] == 'no-store'


def test_tenant_isolation_for_calls_ledger_and_trunks(app, signed_in):
    bill_call(2, 'tenant-b-private', datetime(2025, 1, 1, tzinfo=timezone.utc), '447700999999', 60)
    top_up(2, '100', 'PRIVATE-REFERENCE')
    assert b'447700999999' not in signed_in.get('/calls').data
    assert b'PRIVATE-REFERENCE' not in signed_in.get('/billing').data
    assert b'username-2' not in signed_in.get('/trunks').data
    assert b'secret-1' not in signed_in.get('/trunks').data
    assert signed_in.post('/trunks', data={'account_id': 2, 'password': 'test-password-123'}).status_code == 404
    assert b'secret-1' in signed_in.post('/trunks', data={'account_id': 1, 'password': 'test-password-123'}).data


def test_invoice_download_is_scoped_and_text_selectable(app, signed_in):
    for tenant_id in (1, 2):
        db.session.add(Invoice(tenant_id=tenant_id, period='2025-01', customer_name=f'Company {tenant_id}', currency='USD', usage=Decimal('1.23'), subscriptions=Decimal('5'), total=Decimal('6.23')))
    db.session.commit()
    assert signed_in.get('/invoices/2/download').status_code == 404
    response = signed_in.get('/invoices/1/download')
    assert response.status_code == 200
    pdf = PdfReader(BytesIO(response.data))
    text = pdf.pages[0].extract_text()
    assert 'INV-0001' in text and '6.230000' in text and 'Company 1' in text


def test_login_failure_and_throttle(client):
    for _ in range(10):
        assert client.post('/login', data={'email': 'a@example.test', 'password': 'wrong'}).status_code == 401
    assert client.post('/login', data={'email': 'a@example.test', 'password': 'wrong'}).status_code == 429


def test_csrf_rejects_unprotected_post(app, client):
    app.config['WTF_CSRF_ENABLED'] = True
    assert client.post('/login', data={'email': 'a@example.test', 'password': 'test-password-123'}).status_code == 400


def test_inactive_tenant_loses_session(app, signed_in):
    tenant = db.session.get(Tenant, 1)
    tenant.active = False
    db.session.commit()
    assert signed_in.get('/').status_code == 302


def test_destination_filter_and_no_vendor_brand(signed_in):
    assert b'No matching calls.' in signed_in.get('/calls?destination=447').data
    for url in ['/', '/calls', '/billing', '/trunks']:
        assert b'didlogic' not in signed_in.get(url).data.lower()
