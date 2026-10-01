"""Versioned upgrade from the published v1 schema; no customer data is removed."""
from sqlalchemy import inspect, text
from flask import current_app

from .models import db

VERSION = 4


def upgrade_schema():
    with db.engine.begin() as connection:
        postgres = connection.dialect.name == 'postgresql'
        if postgres:
            connection.execute(text('SELECT pg_advisory_xact_lock(730021)'))
        inspector = inspect(connection)
        tables = inspector.get_table_names()
        if 'schema_version' in tables:
            version = connection.execute(text('SELECT version FROM schema_version WHERE id = 1')).scalar()
            if version and version > VERSION:
                raise RuntimeError('Database is newer than this application; do not downgrade.')
        if 'user' in tables and 'is_admin' not in {c['name'] for c in inspector.get_columns('user')}:
            if not postgres:
                raise RuntimeError('Existing v1 upgrades require PostgreSQL. Back up and migrate the local development database separately.')
            statements = [
                'ALTER TABLE "user" ALTER COLUMN tenant_id DROP NOT NULL',
                'ALTER TABLE "user" ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE',
                'ALTER TABLE "user" ADD CONSTRAINT user_role_scope CHECK ((is_admin AND tenant_id IS NULL) OR (NOT is_admin AND tenant_id IS NOT NULL))',
                'ALTER TABLE tenant ADD COLUMN landline_rate NUMERIC(18,6) NOT NULL DEFAULT 0.016',
                'ALTER TABLE tenant ADD COLUMN mobile_rate NUMERIC(18,6) NOT NULL DEFAULT 0.028',
                'ALTER TABLE tenant ADD COLUMN fallback_rate NUMERIC(18,6)',
                'ALTER TABLE tenant ADD COLUMN billing_increment INTEGER NOT NULL DEFAULT 1',
                'ALTER TABLE tenant ADD COLUMN cli_initial_cost NUMERIC(18,6) NOT NULL DEFAULT 0',
                'ALTER TABLE tenant ADD COLUMN cli_setup_cost NUMERIC(18,6) NOT NULL DEFAULT 0',
                'ALTER TABLE tenant ADD COLUMN cli_monthly_cost NUMERIC(18,6) NOT NULL DEFAULT 5',
                'ALTER TABLE tenant ADD CHECK (landline_rate >= 0 AND mobile_rate >= 0 AND (fallback_rate IS NULL OR fallback_rate >= 0))',
                'ALTER TABLE tenant ADD CHECK (billing_increment IN (1, 6, 30, 60))',
                'ALTER TABLE tenant ADD CHECK (cli_initial_cost >= 0 AND cli_setup_cost >= 0 AND cli_monthly_cost >= 0)',
                'ALTER TABLE did ADD COLUMN initial_cost NUMERIC(18,6) NOT NULL DEFAULT 0',
                'ALTER TABLE did ADD COLUMN setup_cost NUMERIC(18,6) NOT NULL DEFAULT 0',
                'ALTER TABLE "call" ADD COLUMN wholesale_rate NUMERIC(18,6)',
                'ALTER TABLE "call" ADD COLUMN wholesale_cost NUMERIC(18,6)',
            ]
            for statement in statements:
                connection.execute(text(statement))
            # Preserve the v1 global commercial settings when adopting per-client plans.
            connection.execute(text('UPDATE tenant SET billing_increment = :increment, fallback_rate = :fallback'),
                               {'increment': current_app.config['BILLING_INCREMENT_SECONDS'],
                                'fallback': current_app.config['FALLBACK_RATE']})
        if 'tenant' in tables and 'vendor_cost_pricing' not in {c['name'] for c in inspect(connection).get_columns('tenant')}:
            connection.execute(text('ALTER TABLE tenant ADD COLUMN vendor_cost_pricing BOOLEAN NOT NULL DEFAULT FALSE'))
        if 'integration' in tables and 'reconciliation_enabled' not in {c['name'] for c in inspect(connection).get_columns('integration')}:
            connection.execute(text('ALTER TABLE integration ADD COLUMN reconciliation_enabled BOOLEAN NOT NULL DEFAULT FALSE'))
        db.metadata.create_all(connection)
        if postgres:
            connection.execute(text('''CREATE OR REPLACE FUNCTION protect_billing_history() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'Billing history is append-only'; END;
                $$ LANGUAGE plpgsql'''))
            for table in ('ledger', 'call', 'invoice', 'audit_event', 'csv_reconciliation'):
                connection.execute(text(f'DROP TRIGGER IF EXISTS immutable_history ON "{table}"'))
                connection.execute(text(f'CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON "{table}" FOR EACH ROW EXECUTE FUNCTION protect_billing_history()'))
        connection.execute(text('DELETE FROM schema_version WHERE id = 1'))
        connection.execute(text('INSERT INTO schema_version (id, version) VALUES (1, :version)'), {'version': VERSION})
