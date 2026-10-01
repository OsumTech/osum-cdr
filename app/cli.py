import json
from datetime import date, datetime, timezone
from decimal import InvalidOperation

import click
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from werkzeug.security import generate_password_hash

from .billing import close_month, decimal_amount, normalise_number, renew_dids, top_up
from .models import Did, Ledger, LoginThrottle, SipAccount, Tenant, User, db, utcnow


def register_commands(app):
    @app.cli.command('init-db')
    def init_db():
        """Create or safely upgrade the schema. Does not create any accounts."""
        from .schema import upgrade_schema
        upgrade_schema()
        click.echo('Schema version 8 ready. Create the first administrator with create-admin.')

    @app.cli.command('upgrade-db')
    def upgrade_db():
        from .schema import upgrade_schema
        upgrade_schema()
        click.echo('Schema upgraded to version 8. Existing clients and billing history preserved.')

    @app.cli.command('create-admin')
    @click.option('--email', prompt=True)
    @click.password_option()
    def create_admin(email, password):
        """Bootstrap one administrator without creating a tenant or demo records."""
        from .models import AuditEvent
        email = email.strip().lower()
        if '@' not in email or len(email) > 254 or len(password) < 12:
            raise click.ClickException('Use a valid email and a password of at least 12 characters.')
        if db.session.scalar(select(User.id).where(User.is_admin.is_(True))):
            raise click.ClickException('An administrator already exists. Use reset-password for recovery.')
        if db.session.scalar(select(User.id).where(User.email == email)):
            raise click.ClickException('Email belongs to an existing customer. Choose a separate administrator email.')
        user = User(email=email, password_hash=generate_password_hash(password), is_admin=True, tenant_id=None)
        db.session.add(user)
        db.session.flush()
        db.session.add(AuditEvent(actor_id=user.id, action='admin.bootstrap', target=str(user.id), details={}))
        db.session.commit()
        click.echo('Administrator created. Sign in and add clients from the dashboard.')

    @app.cli.command('create-tenant')
    @click.option('--name', prompt=True)
    @click.option('--email', prompt=True)
    @click.password_option()
    def create_tenant(name, email, password):
        """Create a company and its first customer login. No default credentials."""
        email = email.strip().lower()
        if not 1 <= len(name.strip()) <= 160 or '@' not in email or len(email) > 254 or len(password) < 12:
            raise click.ClickException('Use a company name, valid email and password of at least 12 characters.')
        if db.session.scalar(select(User.id).where(User.email == email)):
            raise click.ClickException('Email already exists.')
        tenant = Tenant(name=name.strip())
        db.session.add(tenant)
        db.session.flush()
        db.session.add(User(tenant_id=tenant.id, email=email, password_hash=generate_password_hash(password)))
        db.session.commit()
        click.echo(f'Created tenant {tenant.id}.')

    @app.cli.command('list-tenants')
    def list_tenants():
        for tenant in db.session.scalars(select(Tenant).order_by(Tenant.id)):
            click.echo(f'{tenant.id}\t{tenant.name}\t{tenant.currency} {tenant.balance:.6f}')

    @app.cli.command('add-user')
    @click.option('--tenant-id', type=int, required=True)
    @click.option('--email', prompt=True)
    @click.password_option()
    def add_user(tenant_id, email, password):
        email = email.strip().lower()
        if not db.session.get(Tenant, tenant_id) or '@' not in email or len(email) > 254 or len(password) < 12:
            raise click.ClickException('Use a valid tenant, email and password of at least 12 characters.')
        if db.session.scalar(select(User.id).where(User.email == email)):
            raise click.ClickException('Email already exists.')
        db.session.add(User(tenant_id=tenant_id, email=email, password_hash=generate_password_hash(password)))
        db.session.commit()
        click.echo('User added.')

    @app.cli.command('reset-password')
    @click.option('--email', prompt=True)
    @click.password_option()
    def reset_password(email, password):
        user = db.session.scalar(select(User).where(User.email == email.strip().lower()))
        if not user or len(password) < 12:
            raise click.ClickException('User must exist and password must be at least 12 characters.')
        user.password_hash = generate_password_hash(password)
        user.auth_version += 1
        db.session.commit()
        click.echo('Password updated.')

    @app.cli.command('add-sip')
    @click.option('--tenant-id', type=int, required=True)
    @click.option('--provider-id', prompt=True)
    @click.option('--label', prompt=True)
    @click.option('--username', prompt=True)
    @click.option('--host', prompt=True)
    @click.password_option()
    def add_sip(tenant_id, provider_id, label, username, host, password):
        """Map an already-provisioned trunk. Does not provision vendor resources."""
        if not db.session.get(Tenant, tenant_id):
            raise click.ClickException('Tenant does not exist.')
        if any(not value.strip() or len(value) > limit for value, limit in ((provider_id, 160), (label, 100), (username, 160), (host, 254))):
            raise click.ClickException('All trunk fields must be nonempty and within their length limits.')
        encrypted = Fernet(app.config['ENCRYPTION_KEY']).encrypt(password.encode()).decode()
        db.session.add(SipAccount(tenant_id=tenant_id, provider_id=provider_id, label=label, username=username, host=host, password_encrypted=encrypted))
        db.session.commit()
        click.echo('SIP account mapped. Confirm the provider identifier matches its CDR response exactly.')

    @app.cli.command('add-did')
    @click.option('--tenant-id', type=int, required=True)
    @click.option('--number', prompt=True)
    @click.option('--monthly-charge', prompt=True)
    @click.option('--next-billing-date', prompt=True)
    def add_did(tenant_id, number, monthly_charge, next_billing_date):
        if not db.session.get(Tenant, tenant_id):
            raise click.ClickException('Tenant does not exist.')
        try:
            amount = decimal_amount(monthly_charge)
            due = date.fromisoformat(next_billing_date)
            number = '+' + normalise_number(number)
            if amount < 0:
                raise ValueError('Charge must not be negative.')
        except (ValueError, InvalidOperation) as exc:
            raise click.ClickException(str(exc)) from exc
        db.session.add(Did(tenant_id=tenant_id, number=number, monthly_charge=amount, next_billing_date=due, billing_day=due.day))
        db.session.commit()
        click.echo('DID added. The nightly worker will charge all due renewals, including past dates.')

    @app.cli.command('top-up')
    @click.option('--tenant-id', type=int, required=True)
    @click.option('--amount', required=True)
    @click.option('--reference', required=True)
    def credit(tenant_id, amount, reference):
        """Record an externally verified payment using its unique reference."""
        if not db.session.get(Tenant, tenant_id):
            raise click.ClickException('Tenant does not exist.')
        try:
            posted = top_up(tenant_id, amount, reference)
        except (ValueError, InvalidOperation) as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo('Credit posted.' if posted else 'Reference already recorded; no duplicate credit.')

    @app.cli.command('sync-cdr')
    def sync_cdr():
        from .provider import sync_calls
        click.echo(json.dumps(sync_calls()))

    @app.cli.command('renew-dids')
    def renew():
        click.echo(f'{renew_dids()} renewals posted.')

    @app.cli.command('close-month')
    @click.option('--period', required=True, help='Completed calendar month, YYYY-MM.')
    def close(period):
        click.echo(f'{close_month(period)} invoices created.')

    @app.cli.command('nightly')
    def nightly():
        from datetime import timedelta
        click.echo(f'{renew_dids()} renewals posted.')
        # Catch up every completed posting month after missed worker runs.
        first = db.session.scalar(select(db.func.min(Ledger.created_at)))
        if first:
            month = first.date().replace(day=1)
            current = utcnow().date().replace(day=1)
            from .billing import following_month
            while month < current:
                close_month(month.strftime('%Y-%m'))
                month = following_month(month, 1)
        db.session.execute(db.delete(LoginThrottle).where(LoginThrottle.window_start < utcnow() - timedelta(days=1)))
        db.session.commit()
        click.echo('Nightly billing complete.')
