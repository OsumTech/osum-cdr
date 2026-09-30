from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.billing import bill_call, close_month, rate_call, renew_dids, top_up
from app.models import Call, Did, Invoice, Ledger, Tenant, db

START = datetime(2025, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize('number,seconds,expected', [('+441234567890', 60, '0.016000'), ('00442071234567', 30, '0.008000'), ('447700900123', 61, '0.028467')])
def test_rates(app, number, seconds, expected):
    assert rate_call(number, seconds)[3] == Decimal(expected)


def test_increment_and_missing_rate(app):
    app.config['BILLING_INCREMENT_SECONDS'] = 60
    assert rate_call('447700900123', 61)[1:4] == (120, Decimal('0.0280'), Decimal('0.056000'))
    with pytest.raises(ValueError, match='approved rate'):
        rate_call('+12125551212', 60)
    app.config['FALLBACK_RATE'] = '0.05'
    assert rate_call('+12125551212', 1)[3] == Decimal('0.050000')


def test_duplicate_call_and_payment_do_not_double_charge(app):
    assert top_up(1, '10', 'payment-1')
    assert not top_up(1, '10', 'payment-1')
    assert bill_call(1, 'call-1', START, '447700900123', 60)
    assert not bill_call(1, 'call-1', START, '447700900123', 60)
    assert db.session.get(Tenant, 1).balance == Decimal('9.972000')
    assert db.session.scalar(select(func.count(Call.id))) == 1
    assert db.session.scalar(select(func.count(Ledger.id))) == 2
    assert db.session.get(Tenant, 2).balance == 0


def test_duplicate_reference_amount_conflict(app):
    top_up(1, '10', 'payment-1')
    with pytest.raises(ValueError, match='different amount'):
        top_up(1, '20', 'payment-1')
    assert db.session.get(Tenant, 1).balance == 10


def test_call_id_conflict_cannot_charge_other_tenant(app):
    bill_call(1, 'same-id', START, '447700900123', 60)
    with pytest.raises(ValueError, match='conflicts'):
        bill_call(2, 'same-id', START, '447700900123', 60)
    assert db.session.get(Tenant, 2).balance == 0


def test_atomic_rollback(app, monkeypatch):
    top_up(1, '10', 'initial')
    def fail():
        raise RuntimeError('simulated commit failure')
    monkeypatch.setattr(db.session, 'commit', fail)
    with pytest.raises(RuntimeError):
        bill_call(1, 'rollback-call', START, '447700900123', 60)
    assert db.session.get(Tenant, 1).balance == 10
    assert db.session.scalar(select(func.count(Call.id))) == 0
    assert db.session.scalar(select(func.count(Ledger.id))) == 1


def test_zero_duration_ignored_and_invalid_call_rejected(app):
    assert not bill_call(1, 'missed', START, '447700900123', 0)
    with pytest.raises(ValueError):
        bill_call(1, '', START, '447700900123', 20)
    assert db.session.get(Tenant, 1).balance == 0


def test_overdue_renewals_month_end_and_idempotency(app):
    db.session.add(Did(tenant_id=1, number='+442071234567', monthly_charge=Decimal('5'),
                       next_billing_date=date(2024, 1, 31), billing_day=31))
    db.session.commit()
    assert renew_dids(date(2024, 3, 31)) == 3
    assert renew_dids(date(2024, 3, 31)) == 0
    assert db.session.get(Did, 1).next_billing_date == date(2024, 4, 30)
    assert db.session.get(Tenant, 1).balance == -15
    assert db.session.scalar(select(func.count(Ledger.id))) == 3


def test_monthly_invoice_snapshots_posted_charges_once(app):
    bill_call(1, 'historic', START, '447700900123', 60)
    entry = db.session.scalar(select(Ledger))
    entry.created_at = START
    db.session.commit()
    close_month('2025-01')
    invoice = db.session.scalar(select(Invoice).where(Invoice.tenant_id == 1))
    assert invoice.total == Decimal('0.028000')
    assert invoice.customer_name == 'Test Company A'
    assert close_month('2025-01') == 0
    assert db.session.get(Tenant, 1).balance == Decimal('-0.028000')


@pytest.mark.parametrize('amount', ['NaN', 'Infinity', '-1', '0'])
def test_invalid_topups(app, amount):
    with pytest.raises(ValueError):
        top_up(1, amount, 'invalid')
