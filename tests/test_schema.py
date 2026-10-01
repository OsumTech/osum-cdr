import pytest
from sqlalchemy import select, text

from app.models import Call, SchemaVersion, Tenant, User, db
from app.schema import upgrade_schema


def test_fresh_schema_upgrade_is_idempotent(app):
    upgrade_schema()
    upgrade_schema()
    assert db.session.get(SchemaVersion, 1).version == 4
    assert db.session.get(Tenant, 1).name == 'Test Company A'


def test_v2_upgrade_defaults_to_existing_retail_pricing(app):
    db.session.remove()
    with db.engine.begin() as connection:
        connection.execute(text('ALTER TABLE tenant DROP COLUMN vendor_cost_pricing'))
    upgrade_schema()
    upgrade_schema()
    assert db.session.get(Tenant, 1).vendor_cost_pricing is False


def test_v3_upgrade_adds_paused_reconciliation(app):
    from app.models import Integration
    db.session.add(Integration(id=1, enabled=False))
    db.session.commit()
    db.session.remove()
    with db.engine.begin() as connection:
        connection.execute(text('ALTER TABLE integration DROP COLUMN reconciliation_enabled'))
        connection.execute(text('DROP TABLE csv_reconciliation'))
        connection.execute(text('DROP TABLE cdr_partition'))
    upgrade_schema()
    assert db.session.get(Integration, 1).reconciliation_enabled is False


def test_v1_upgrade_preserves_existing_customer_and_rates(app):
    if db.engine.dialect.name != 'postgresql':
        pytest.skip('v1 production schema upgrades require PostgreSQL')
    db.session.remove()
    db.drop_all()
    with db.engine.begin() as connection:
        for statement in (
            'CREATE TABLE tenant (id SERIAL PRIMARY KEY, name VARCHAR(160) NOT NULL, balance NUMERIC(18,6) NOT NULL, currency VARCHAR(3) NOT NULL, active BOOLEAN NOT NULL)',
            'CREATE TABLE "user" (id SERIAL PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenant(id), email VARCHAR(254) UNIQUE NOT NULL, password_hash VARCHAR(512) NOT NULL, active BOOLEAN NOT NULL, auth_version INTEGER NOT NULL)',
            'CREATE TABLE did (id SERIAL PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenant(id), number VARCHAR(32) UNIQUE NOT NULL, monthly_charge NUMERIC(18,6) NOT NULL, next_billing_date DATE NOT NULL, billing_day INTEGER NOT NULL, active BOOLEAN NOT NULL)',
            'CREATE TABLE sip_account (id SERIAL PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenant(id), provider_id VARCHAR(160) UNIQUE NOT NULL, label VARCHAR(100) NOT NULL, username VARCHAR(160) NOT NULL, host VARCHAR(254) NOT NULL, password_encrypted TEXT NOT NULL, active BOOLEAN NOT NULL, synced_through TIMESTAMPTZ, last_sync_error BOOLEAN NOT NULL, UNIQUE(id,tenant_id))',
            'CREATE TABLE "call" (id SERIAL PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenant(id), sip_account_id INTEGER NOT NULL, vendor_call_id VARCHAR(200) UNIQUE NOT NULL, started_at TIMESTAMPTZ NOT NULL, destination VARCHAR(32) NOT NULL, duration INTEGER NOT NULL, billed_seconds INTEGER NOT NULL, rate NUMERIC(18,6) NOT NULL, cost NUMERIC(18,6) NOT NULL, rate_label VARCHAR(80) NOT NULL, FOREIGN KEY(sip_account_id,tenant_id) REFERENCES sip_account(id,tenant_id))',
            "INSERT INTO tenant VALUES (1,'Existing company',73.25,'USD',true)",
            "INSERT INTO \"user\" VALUES (1,1,'existing@example.test','unchanged-password-hash',true,1)",
            "INSERT INTO did VALUES (1,1,'+442071234567',5,'2026-12-31',31,true)",
            "INSERT INTO sip_account VALUES (1,1,'provider-legacy','Existing trunk','user','sip.example.test','encrypted-secret',true,NULL,false)",
            "INSERT INTO \"call\" VALUES (1,1,1,'legacy-id','2025-01-01T00:00:00Z','447700900123',60,60,0.028,0.028,'UK mobile')",
        ):
            connection.execute(text(statement))
    app.config['BILLING_INCREMENT_SECONDS'] = 60
    app.config['FALLBACK_RATE'] = '0.05'
    upgrade_schema()
    upgrade_schema()
    tenant = db.session.get(Tenant, 1)
    assert str(tenant.balance) == '73.250000'
    assert tenant.billing_increment == 60
    assert str(tenant.fallback_rate) == '0.050000'
    user = db.session.get(User, 1)
    assert user.password_hash == 'unchanged-password-hash'
    assert user.tenant_id == 1 and not user.is_admin
    call = db.session.get(Call, 1)
    assert call.vendor_call_id == 'legacy-id' and call.wholesale_cost is None
