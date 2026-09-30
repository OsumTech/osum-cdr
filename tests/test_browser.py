"""Opt-in local browser QA. Uses only disposable test accounts and records."""
import os
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server


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
    from app.models import User, db
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
                for path in ('/admin/', '/admin/clients', '/admin/clients/new', '/admin/clients/1', '/admin/integrations', '/admin/calls'):
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
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
