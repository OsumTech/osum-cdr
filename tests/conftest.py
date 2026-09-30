import os

import pytest
from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash

from app import create_app
from app.models import SipAccount, Tenant, User, db


@pytest.fixture
def app(tmp_path):
    key = Fernet.generate_key().decode()
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only-' * 8, 'ENCRYPTION_KEY': key,
                      'SQLALCHEMY_DATABASE_URI': os.getenv('TEST_DATABASE_URL', f'sqlite:///{tmp_path / "test.db"}'),
                      'SESSION_COOKIE_SECURE': False, 'TRUSTED_HOSTS': ['localhost'],
                      'WTF_CSRF_ENABLED': False, 'FALLBACK_RATE': None, 'BILLING_INCREMENT_SECONDS': 1})
    with app.app_context():
        db.create_all()
        a, b = Tenant(name='Test Company A'), Tenant(name='Test Company B')
        db.session.add_all([a, b])
        db.session.flush()
        for tenant, email in ((a, 'a@example.test'), (b, 'b@example.test')):
            db.session.add(User(tenant_id=tenant.id, email=email, password_hash=generate_password_hash('test-password-123')))
            db.session.add(SipAccount(tenant_id=tenant.id, provider_id=f'provider-{tenant.id}', label=f'Trunk {tenant.id}',
                username=f'username-{tenant.id}', host='sip.example.test',
                password_encrypted=Fernet(key).encrypt(f'secret-{tenant.id}'.encode()).decode()))
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def signed_in(client):
    response = client.post('/login', data={'email': 'a@example.test', 'password': 'test-password-123'})
    assert response.status_code == 302
    return client
