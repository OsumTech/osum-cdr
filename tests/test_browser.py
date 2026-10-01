"""Opt-in local browser QA. Uses only disposable test accounts and records."""
import os
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server


@pytest.mark.skipif(os.getenv('BROWSER_TESTS') != '1', reason='Set BROWSER_TESTS=1 for CSV workflow QA')
def test_csv_review_accept_and_customer_excel(app):
    from datetime import date, timedelta
    from io import BytesIO
    from openpyxl import load_workbook
    from playwright.sync_api import sync_playwright
    from sqlalchemy import select
    from werkzeug.security import generate_password_hash
    from app.models import CdrPartition, Did, Integration, SipAccount, User, db, utcnow
    from app.reconciliation import canonical_rows, save_snapshot
    db.session.add(User(email='csv-browser@example.test', is_admin=True, password_hash=generate_password_hash('csv-browser-password')))
    db.session.add(Integration(id=1, reconciliation_enabled=True, currency_confirmed=True, token_version=1))
    db.session.commit()
    account = db.session.get(SipAccount, 1)
    groups = canonical_rows([{'type': 'sip', 'sip_account': 'provider-1', 'timestamp': '2025-01-01T12:00:00Z',
                             'from': '441234567890', 'to': '447700900123', 'duration': 30, 'amount': '0.008'}], account, date(2025, 1, 1))
    save_snapshot(account, date(2025, 1, 1), groups, 1)
    inbound_did = Did(tenant_id=1, number='+442071234567', monthly_charge=5, next_billing_date=date(2027, 1, 1), billing_day=1)
    db.session.add(inbound_did)
    db.session.commit()
    inbound_groups = canonical_rows([{'type': 'incoming', 'did_number': '442071234567', 'to': 'extension-100',
        'from': 'anonymous', 'timestamp': '2025-01-01T12:01:00Z', 'duration': 60, 'amount': '0.0900'}], inbound_did, date(2025, 1, 1))
    save_snapshot(inbound_did, date(2025, 1, 1), inbound_groups, 1)
    part = db.session.scalar(select(CdrPartition))
    part.checked_at = utcnow() - timedelta(minutes=6)
    db.session.commit()
    save_snapshot(account, date(2025, 1, 1), groups, 1)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = Path('test-results')
    results.mkdir(exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            base = f'http://localhost:{server.server_port}'
            page.goto(base + '/login')
            page.get_by_label('Email address').fill('csv-browser@example.test')
            page.get_by_label('Password', exact=True).fill('csv-browser-password')
            page.get_by_role('button', name='Sign in').click()
            page.goto(base + '/admin/reconciliation')
            page.get_by_role('link', name='Compare manual provider CSV').last.click()
            csv_data = b'Date,Time,SIP ID,Type,From,To,Duration,Charge\n01/01/25,12:00:00 pm,provider-1,SIP TERM,441234567890,447700900123,30,0.008\n'
            page.get_by_label('Provider CSV').set_input_files({'name': 'test-provider.csv', 'mimeType': 'text/csv', 'buffer': csv_data})
            page.get_by_label('Export timezone').select_option('0')
            page.get_by_label('This export contains').check()
            page.get_by_role('button', name='Compare CSV').click()
            page.get_by_role('heading', name='Matched', exact=True).wait_for()
            for width in (1440, 768, 390, 320):
                page.set_viewport_size({'width': width, 'height': 1000})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(results / f'csv-comparison-{width}.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.goto(base + '/admin/reconciliation')
            page.get_by_label("I verified this day's").check()
            page.get_by_role('button', name='Accept day').click()
            page.get_by_text('Accepted snapshot. 1 new calls charged.', exact=False).wait_for()
            page.get_by_role('button', name='Sign out').click()
            page.get_by_label('Email address').fill('a@example.test')
            page.get_by_label('Password', exact=True).fill('test-password-123')
            page.get_by_role('button', name='Sign in').click()
            page.goto(base + '/calls?start=2025-01-01&end=2025-01-01')
            page.screenshot(path=str(results / 'customer-export.png'), full_page=True)
            with page.expect_download() as download_info:
                page.get_by_role('button', name='Export Excel').click()
            download = download_info.value
            book = load_workbook(BytesIO(Path(download.path()).read_bytes()))
            assert book.active['C6'].value == '+447700900123'
            assert book.active['F6'].value == 0.014
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.skipif(os.getenv('BROWSER_TESTS') != '1', reason='Set BROWSER_TESTS=1 to run local Chrome QA')
def test_responsive_portal_and_login(app):
    from playwright.sync_api import sync_playwright
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = Path('test-results')
    results.mkdir(exist_ok=True)
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://localhost:{server.server_port}/login')
            page.screenshot(path=str(results / 'login-desktop.png'), full_page=True)
            page.get_by_label('Email address').fill('a@example.test')
            page.get_by_label('Password', exact=True).fill('test-password-123')
            page.get_by_role('button', name='Sign in').click()
            page.get_by_role('heading', name='Overview', exact=True).wait_for()
            for width in (1440, 768, 390, 320):
                page.set_viewport_size({'width': width, 'height': 1000})
                for path in ('/', '/calls', '/trunks', '/billing'):
                    page.goto(f'http://localhost:{server.server_port}{path}')
                    assert page.locator('h1').count() == 1
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), (width, path)
                    assert page.locator('img').evaluate_all('(images) => images.every(i => i.complete && i.naturalWidth > 0)')
                page.goto(f'http://localhost:{server.server_port}/')
                page.screenshot(path=str(results / f'overview-{width}.png'), full_page=True)
            page.goto(f'http://localhost:{server.server_port}/trunks')
            page.get_by_text('Reveal SIP password', exact=True).click()
            page.get_by_label('Confirm your portal password').fill('test-password-123')
            page.get_by_role('button', name='Reveal password', exact=True).click()
            assert page.locator('code').inner_text() == 'secret-1'
            page.get_by_role('button', name='Sign out').click()
            page.get_by_role('heading', name='Welcome back.').wait_for()
            page.screenshot(path=str(results / 'login-mobile.png'), full_page=True)
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.skipif(os.getenv('BROWSER_TESTS') != '1', reason='Set BROWSER_TESTS=1 for admin Chrome QA')
def test_admin_responsive_onboarding(app, monkeypatch):
    from playwright.sync_api import sync_playwright
    from werkzeug.security import generate_password_hash
    from app.models import User, WebhookEvent, db
    for i in range(12):
        db.session.add(WebhookEvent(call_id=f'long-provider-call-identifier-{i}@192.0.2.1:5060', direction='outbound',
            fingerprint='a' * 64, tenant_id=1, status='mapped', payload={
                'calldate': '2025-01-01T12:00:00+00:00', 'src': '447700900123', 'dst': '441670641217',
                'disposition': 'ANSWERED', 'billsec': 57, 'duration': 68,
                'billing': {'state': 'charged', 'cost': '0.015200', 'rate': '0.016000', 'seconds': 57}}))
    db.session.add(User(email='owner@example.test', password_hash=generate_password_hash('owner-test-password'), is_admin=True))
    db.session.commit()
    monkeypatch.setattr('app.admin.preview_calls', lambda config: (['id', 'duration'], []))
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = Path('test-results')
    results.mkdir(exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.goto(f'http://localhost:{server.server_port}/login')
            page.get_by_label('Email address').fill('owner@example.test')
            page.get_by_label('Password', exact=True).fill('owner-test-password')
            page.get_by_role('button', name='Sign in').click()
            page.get_by_role('heading', name='Admin overview').wait_for()
            for width in (1440, 768, 390, 320):
                page.set_viewport_size({'width': width, 'height': 1000})
                for path in ('/admin/', '/admin/clients', '/admin/clients/new', '/admin/clients/1', '/admin/integrations', '/admin/calls', '/admin/reconciliation', '/admin/webhooks'):
                    response = page.goto(f'http://localhost:{server.server_port}{path}')
                    assert response.status == 200
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), (width, path)
                page.goto(f'http://localhost:{server.server_port}/admin/')
                page.screenshot(path=str(results / f'admin-{width}.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.goto(f'http://localhost:{server.server_port}/admin/clients/new')
            page.get_by_label('Company name').fill('Browser Test Client')
            page.get_by_label('Client login email').fill('browser-client@example.test')
            page.get_by_label('Initial password').fill('browser-test-password')
            page.get_by_role('button', name='Create client', exact=True).click()
            page.get_by_role('heading', name='Browser Test Client').wait_for()
            page.screenshot(path=str(results / 'admin-client.png'), full_page=True)
            page.goto(f'http://localhost:{server.server_port}/admin/integrations')
            page.get_by_label('API token').fill('test-token-never-production')
            page.get_by_role('button', name='Save connection settings').click()
            assert page.get_by_label('API token').input_value() == ''
            page.get_by_role('button', name='Test connection & fetch CDRs').click()
            assert page.get_by_text('Connected. Fetched 0 real CDRs', exact=False).count() >= 1
            page.screenshot(path=str(results / 'admin-integration.png'), full_page=True)
            page.goto(f'http://localhost:{server.server_port}/admin/webhooks')
            assert page.locator('.live-call-table tbody tr').count() == 10
            page.get_by_label('Rows per page').select_option('25')
            page.get_by_role('button', name='Filter calls', exact=True).click()
            assert page.locator('.live-call-table tbody tr').count() == 12
            page.locator('.live-call-scroll').scroll_into_view_if_needed()
            page.screenshot(path=str(results / 'live-calls-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('.live-call-scroll').scroll_into_view_if_needed()
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path=str(results / 'live-calls-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.get_by_role('button', name='Create shadow receiver').click()
            assert page.get_by_label('Private CDR webhook URL').input_value().startswith('https://')
            page.get_by_role('heading', name='Receiver enabled', exact=True).wait_for()
            page.get_by_text('Receiver configuration and private URL', exact=True).click()
            page.get_by_role('button', name='Pause receiver').click()
            page.get_by_role('heading', name='Receiver paused', exact=True).wait_for()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
