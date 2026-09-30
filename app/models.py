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


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    email = db.Column(db.String(254), nullable=False, unique=True)
    password_hash = db.Column(db.String(512), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    tenant = db.relationship(Tenant)

    auth_version = db.Column(db.Integer, nullable=False, default=1)

    def get_id(self):
        return f'{self.id}:{self.auth_version}'

    @property
    def is_active(self):
        return self.active and self.tenant.active


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
