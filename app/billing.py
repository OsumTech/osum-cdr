"""All monetary mutations are atomic and serialised on the tenant row."""
import calendar
import re
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from flask import current_app
from sqlalchemy import select

from .models import Call, Did, Invoice, Ledger, SipAccount, Tenant, db, utcnow

PRECISION = Decimal('0.000001')


def decimal_amount(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or abs(amount) >= Decimal('100000000000'):
        raise ValueError('Amount is outside the supported range.')
    return amount.quantize(PRECISION, rounding=ROUND_HALF_UP)


def normalise_number(value):
    number = re.sub(r'[\s()\-]', '', str(value))
    if number.startswith('+'):
        number = number[1:]
    elif number.startswith('00'):
        number = number[2:]
    if not re.fullmatch(r'[1-9][0-9]{5,14}', number):
        raise ValueError('Destination must be an international telephone number.')
    return number


def rate_call(destination, duration):
    number = normalise_number(destination)
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        raise ValueError('Answered duration must be a positive integer.')
    rates = [('441', '0.0160', 'UK landline'), ('442', '0.0160', 'UK landline'), ('447', '0.0280', 'UK mobile')]
    match = next(((Decimal(rate), label) for prefix, rate, label in sorted(rates, key=lambda r: len(r[0]), reverse=True) if number.startswith(prefix)), None)
    if match is None:
        fallback = current_app.config['FALLBACK_RATE']
        if fallback is None:
            raise ValueError('No approved rate for destination; configure FALLBACK_RATE.')
        match = (decimal_amount(fallback), 'Other destinations')
    rate, label = match
    if rate < 0:
        raise ValueError('Rates cannot be negative.')
    increment = current_app.config['BILLING_INCREMENT_SECONDS']
    seconds = ((duration + increment - 1) // increment) * increment
    return number, seconds, rate, decimal_amount(rate * Decimal(seconds) / 60), label


def locked_tenant(tenant_id):
    tenant = db.session.execute(select(Tenant).where(Tenant.id == tenant_id).with_for_update().execution_options(populate_existing=True)).scalar_one()
    return tenant


def post_entry(tenant, key, kind, description, amount):
    # Caller must hold the tenant row lock until transaction commit.
    tenant.balance = decimal_amount(tenant.balance + amount)
    db.session.add(Ledger(tenant_id=tenant.id, key=key, kind=kind, description=description,
                          amount=amount, balance_after=tenant.balance))


def top_up(tenant_id, amount, reference):
    amount = decimal_amount(amount)
    if amount <= 0 or not reference.strip() or len(reference) > 160:
        raise ValueError('Use a positive credit and a payment reference of 1–160 characters.')
    try:
        tenant = locked_tenant(tenant_id)
        key = f'topup:{tenant_id}:{reference}'
        existing = db.session.scalar(select(Ledger).where(Ledger.key == key))
        if existing:
            if existing.amount != amount:
                raise ValueError('This payment reference already exists with a different amount.')
            db.session.rollback()
            return False
        post_entry(tenant, key, 'topup', f'Payment received · {reference}', amount)
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        raise


def bill_call(account_id, vendor_call_id, started_at, destination, duration):
    if duration == 0:
        return False
    if not vendor_call_id or len(str(vendor_call_id)) > 200:
        raise ValueError('A stable provider call ID is required.')
    if started_at.tzinfo is None or started_at > utcnow():
        raise ValueError('Call start must be a timezone-aware, non-future timestamp.')
    number, seconds, rate, cost, label = rate_call(destination, duration)
    try:
        account = db.session.get(SipAccount, account_id)
        if account is None:
            raise ValueError('Unknown SIP account.')
        tenant = locked_tenant(account.tenant_id)
        existing = db.session.scalar(select(Call).where(Call.vendor_call_id == str(vendor_call_id)))
        if existing:
            if (existing.sip_account_id != account_id or existing.destination != number
                    or existing.duration != duration
                    or existing.started_at.replace(tzinfo=timezone.utc) != started_at.astimezone(timezone.utc)):
                raise ValueError('Provider call ID conflicts with a previously billed call.')
            db.session.rollback()
            return False
        db.session.add(Call(tenant_id=tenant.id, sip_account_id=account.id,
                            vendor_call_id=str(vendor_call_id), started_at=started_at,
                            destination=number, duration=duration, billed_seconds=seconds,
                            rate=rate, cost=cost, rate_label=label))
        post_entry(tenant, f'call:{vendor_call_id}', 'usage', f'Call to +{number} · {seconds}s billed', -cost)
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        raise


def following_month(day, anchor):
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(year, month, min(anchor, calendar.monthrange(year, month)[1]))


def renew_dids(today=None):
    today = today or utcnow().date()
    ids = db.session.scalars(select(Did.id).where(Did.active.is_(True), Did.next_billing_date <= today)).all()
    count = 0
    for did_id in ids:
        try:
            did = db.session.get(Did, did_id)
            tenant = locked_tenant(did.tenant_id)
            db.session.refresh(did)
            while did.active and did.next_billing_date <= today:
                due = did.next_billing_date
                key = f'did:{did.id}:{due.isoformat()}'
                if not db.session.scalar(select(Ledger.id).where(Ledger.key == key)):
                    post_entry(tenant, key, 'subscription', f'DID {did.number} · renewal {due.isoformat()}', -did.monthly_charge)
                    count += 1
                did.next_billing_date = following_month(due, did.billing_day)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
    return count


def close_month(period):
    start = datetime.strptime(period, '%Y-%m').replace(tzinfo=timezone.utc)
    end = datetime.combine(following_month(start.date(), 1), datetime.min.time(), tzinfo=timezone.utc)
    if end > utcnow():
        raise ValueError('Only completed months can be invoiced.')
    count = 0
    for tenant_id in db.session.scalars(select(Tenant.id)).all():
        try:
            tenant = locked_tenant(tenant_id)
            if db.session.scalar(select(Invoice.id).where(Invoice.tenant_id == tenant_id, Invoice.period == period)):
                db.session.rollback()
                continue
            entries = db.session.scalars(select(Ledger).where(Ledger.tenant_id == tenant_id, Ledger.created_at >= start, Ledger.created_at < end)).all()
            usage = -sum((e.amount for e in entries if e.kind == 'usage'), Decimal(0))
            subscriptions = -sum((e.amount for e in entries if e.kind == 'subscription'), Decimal(0))
            db.session.add(Invoice(tenant_id=tenant_id, period=period, customer_name=tenant.name,
                                   currency=tenant.currency, usage=usage, subscriptions=subscriptions,
                                   total=usage + subscriptions))
            db.session.commit()
            count += 1
        except Exception:
            db.session.rollback()
            raise
    return count
