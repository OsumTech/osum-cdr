from datetime import datetime, timezone
from decimal import Decimal

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc)


class Tenant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    balance = db.Column(db.Numeric(18, 6), nullable=False, default=Decimal('0'))
    currency = db.Column(db.String(3), nullable=False, default='USD')
    active = db.Column(db.Boolean, nullable=False, default=True)
    vendor_cost_pricing = db.Column(db.Boolean, nullable=False, default=False)
    landline_rate = db.Column(db.Numeric(18, 6), nullable=False, default=Decimal('0.016'))
    mobile_rate = db.Column(db.Numeric(18, 6), nullable=False, default=Decimal('0.028'))
    fallback_rate = db.Column(db.Numeric(18, 6))
    billing_increment = db.Column(db.Integer, nullable=False, default=1)
    cli_initial_cost = db.Column(db.Numeric(18, 6), nullable=False, default=Decimal('0'))
    cli_setup_cost = db.Column(db.Numeric(18, 6), nullable=False, default=Decimal('0'))
    cli_monthly_cost = db.Column(db.Numeric(18, 6), nullable=False, default=Decimal('5'))
    __table_args__ = (
        db.CheckConstraint('landline_rate >= 0 AND mobile_rate >= 0 AND (fallback_rate IS NULL OR fallback_rate >= 0)'),
        db.CheckConstraint('billing_increment IN (1, 6, 30, 60)'),
        db.CheckConstraint('cli_initial_cost >= 0 AND cli_setup_cost >= 0 AND cli_monthly_cost >= 0'),
    )


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=True, index=True)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    email = db.Column(db.String(254), nullable=False, unique=True)
    password_hash = db.Column(db.String(512), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    tenant = db.relationship(Tenant)

    auth_version = db.Column(db.Integer, nullable=False, default=1)

    def get_id(self):
        return f'{self.id}:{self.auth_version}'

    @property
    def is_active(self):
        return self.active and (self.is_admin or (self.tenant is not None and self.tenant.active))

    __table_args__ = (db.CheckConstraint('(is_admin AND tenant_id IS NULL) OR (NOT is_admin AND tenant_id IS NOT NULL)', name='user_role_scope'),)


class SipAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    provider_id = db.Column(db.String(160), unique=True, nullable=False)
    label = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(160), nullable=False)
    host = db.Column(db.String(254), nullable=False)
    password_encrypted = db.Column(db.Text, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    synced_through = db.Column(db.DateTime(timezone=True))
    last_sync_error = db.Column(db.Boolean, nullable=False, default=False)
    tenant = db.relationship(Tenant)
    __table_args__ = (db.UniqueConstraint('id', 'tenant_id'),)


class Did(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    number = db.Column(db.String(32), nullable=False, unique=True)
    monthly_charge = db.Column(db.Numeric(18, 6), nullable=False)
    next_billing_date = db.Column(db.Date, nullable=False, index=True)
    billing_day = db.Column(db.Integer, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    initial_cost = db.Column(db.Numeric(18, 6), nullable=False, default=Decimal('0'))
    setup_cost = db.Column(db.Numeric(18, 6), nullable=False, default=Decimal('0'))
    __table_args__ = (
        db.CheckConstraint('monthly_charge >= 0'),
        db.CheckConstraint('billing_day BETWEEN 1 AND 31'),
    )


class Call(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    sip_account_id = db.Column(db.Integer, nullable=False)
    vendor_call_id = db.Column(db.String(200), nullable=False, unique=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    destination = db.Column(db.String(32), nullable=False)
    duration = db.Column(db.Integer, nullable=False)
    billed_seconds = db.Column(db.Integer, nullable=False)
    rate = db.Column(db.Numeric(18, 6), nullable=False)
    cost = db.Column(db.Numeric(18, 6), nullable=False)
    rate_label = db.Column(db.String(80), nullable=False)
    wholesale_rate = db.Column(db.Numeric(18, 6))
    wholesale_cost = db.Column(db.Numeric(18, 6))
    __table_args__ = (
        db.ForeignKeyConstraint(['sip_account_id', 'tenant_id'], ['sip_account.id', 'sip_account.tenant_id']),
        db.CheckConstraint('duration > 0 AND billed_seconds >= duration AND cost >= 0'),
    )


class Ledger(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    key = db.Column(db.String(240), nullable=False, unique=True)
    kind = db.Column(db.String(24), nullable=False)
    description = db.Column(db.String(300), nullable=False)
    amount = db.Column(db.Numeric(18, 6), nullable=False)
    balance_after = db.Column(db.Numeric(18, 6), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    __table_args__ = (db.CheckConstraint("kind IN ('topup', 'usage', 'subscription')"),)


class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    period = db.Column(db.String(7), nullable=False)
    customer_name = db.Column(db.String(160), nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    issued_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    usage = db.Column(db.Numeric(18, 6), nullable=False)
    subscriptions = db.Column(db.Numeric(18, 6), nullable=False)
    total = db.Column(db.Numeric(18, 6), nullable=False)
    __table_args__ = (db.UniqueConstraint('tenant_id', 'period'),)

    @property
    def number(self):
        return f'INV-{self.id:04d}'


class LoginThrottle(db.Model):
    key = db.Column(db.String(64), primary_key=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    window_start = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class Integration(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token_encrypted = db.Column(db.Text)
    token_version = db.Column(db.Integer, nullable=False, default=0)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    call_id_field = db.Column(db.String(80), nullable=False, default='')
    start_date = db.Column(db.Date)
    currency_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    last_test_at = db.Column(db.DateTime(timezone=True))
    last_test_ok = db.Column(db.Boolean, nullable=False, default=False)
    last_test_message = db.Column(db.String(300))
    last_sync_at = db.Column(db.DateTime(timezone=True))
    last_sync_message = db.Column(db.String(300))
    sample_fields = db.Column(db.JSON)
    preview_rows = db.Column(db.JSON)
    sync_requested = db.Column(db.Boolean, nullable=False, default=False)
    reconciliation_enabled = db.Column(db.Boolean, nullable=False, default=False)
    __table_args__ = (db.CheckConstraint('id = 1'),)


class AuditEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    action = db.Column(db.String(80), nullable=False)
    target = db.Column(db.String(160), nullable=False)
    details = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class SchemaVersion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    version = db.Column(db.Integer, nullable=False)


class CdrPartition(db.Model):
    """Complete provider-day snapshots; shadow data never enters customer views."""
    id = db.Column(db.Integer, primary_key=True)
    sip_account_id = db.Column(db.Integer, db.ForeignKey('sip_account.id'), nullable=False)
    day = db.Column(db.Date, nullable=False)
    rows = db.Column(db.JSON, nullable=False, default=dict)
    accepted_rows = db.Column(db.JSON, nullable=False, default=dict)
    digest = db.Column(db.String(64), nullable=False)
    token_version = db.Column(db.Integer, nullable=False)
    observations = db.Column(db.Integer, nullable=False, default=1)
    first_seen = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    checked_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    accepted_at = db.Column(db.DateTime(timezone=True))
    status = db.Column(db.String(30), nullable=False, default='observing')
    record_count = db.Column(db.Integer, nullable=False)
    wholesale_total = db.Column(db.Numeric(18, 6), nullable=False)
    account = db.relationship(SipAccount)
    __table_args__ = (db.UniqueConstraint('sip_account_id', 'day'),)


class CsvReconciliation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    partition_id = db.Column(db.Integer, db.ForeignKey('cdr_partition.id'), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    report = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    partition = db.relationship(CdrPartition)
