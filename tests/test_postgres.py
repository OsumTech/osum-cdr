from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.billing import bill_call, top_up
from app.models import Call, Ledger, Tenant, db


def postgres_only():
    if db.engine.dialect.name != 'postgresql':
        pytest.skip('Requires isolated PostgreSQL TEST_DATABASE_URL')


def test_concurrent_duplicate_charges(app):
    postgres_only()
    top_up(1, '100', 'starting-balance')
    def charge(_):
        with app.app_context():
            return bill_call(1, 'concurrent-id', datetime(2025, 1, 1, tzinfo=timezone.utc), '447700900123', 60)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(charge, range(8)))
    db.session.expire_all()
    assert sum(results) == 1
    assert db.session.get(Tenant, 1).balance == Decimal('99.972000')
    assert db.session.scalar(select(func.count(Call.id))) == 1


def test_concurrent_distinct_charges_preserve_balance(app):
    postgres_only()
    top_up(1, '100', 'starting-balance')
    def charge(index):
        with app.app_context():
            return bill_call(1, f'parallel-{index}', datetime(2025, 1, 1, tzinfo=timezone.utc), '447700900123', 60)
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert all(executor.map(charge, range(20)))
    db.session.expire_all()
    assert db.session.get(Tenant, 1).balance == Decimal('99.440000')
    assert db.session.scalar(select(func.sum(Ledger.amount)).where(Ledger.tenant_id == 1)) == Decimal('99.440000')


def test_history_cannot_be_edited_or_deleted(app):
    postgres_only()
    result = app.test_cli_runner().invoke(args=['init-db'])
    assert result.exit_code == 0, result.output
    top_up(1, '10', 'protected')
    for command in ('UPDATE ledger SET amount = 999', 'DELETE FROM ledger'):
        with pytest.raises(DBAPIError, match='append-only'):
            db.session.execute(text(command))
        db.session.rollback()
    assert db.session.scalar(select(Ledger.amount)) == 10
