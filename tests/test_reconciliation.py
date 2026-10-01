from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Call, CdrPartition, Integration, Ledger, SipAccount, Tenant, User, db, utcnow
from app.reconciliation import approve, canonical_rows, fetch_partition, save_snapshot


DAY = date(2025, 1, 1)


def raw(**changes):
    return {'type': 'sip', 'sip_account': 'provider-1', 'timestamp': '2025-01-01T12:00:00Z',
            'from': '441234567890', 'to': '447700900123', 'duration': 30, 'amount': '0.008', **changes}


@pytest.fixture
def configured(app):
    config = Integration(id=1, reconciliation_enabled=True, token_version=1, currency_confirmed=True,
                         start_date=DAY, last_test_ok=True)
    owner = User(email='recon@example.test', password_hash='unused', is_admin=True)
    db.session.add_all([config, owner])
    db.session.commit()
    return db.session.get(SipAccount, 1), owner.id


def reviewable(account, rows):
    groups = canonical_rows(rows, account, DAY)
    save_snapshot(account, DAY, groups, 1)
    part = db.session.scalar(select(CdrPartition))
    part.checked_at = utcnow() - timedelta(minutes=6)
    db.session.commit()
    save_snapshot(account, DAY, groups, 1)
    return db.session.scalar(select(CdrPartition))


def test_duplicate_occurrences_shadow_and_atomic_accept(configured):
    account, actor = configured
    part = reviewable(account, [raw(), raw()])
    assert part.status == 'review' and part.record_count == 2
    assert db.session.scalar(select(func.count(Call.id))) == 0
    assert db.session.scalar(select(func.count(Ledger.id))) == 0
    assert approve(part.id, part.digest, actor) == 2
    assert db.session.get(Tenant, 1).balance == Decimal('-0.028')
    assert approve(part.id, part.digest, actor) == 0
    assert db.session.scalar(select(func.count(Call.id))) == 2
    part = reviewable(account, [raw(), raw(), raw()])
    assert approve(part.id, part.digest, actor) == 1
    assert db.session.get(Tenant, 1).balance == Decimal('-0.042')


def test_corrections_do_not_charge_again(configured):
    account, actor = configured
    part = reviewable(account, [raw()])
    approve(part.id, part.digest, actor)
    part = reviewable(account, [raw(amount='0.009')])
    assert part.status == 'correction'
    with pytest.raises(ValueError):
        approve(part.id, part.digest, actor)
    assert db.session.scalar(select(func.count(Call.id))) == 1


def test_failed_rating_rolls_back_entire_day(configured):
    account, actor = configured
    part = reviewable(account, [raw(), raw(to='33123456789')])
    with pytest.raises(ValueError, match='fallback'):
        approve(part.id, part.digest, actor)
    assert db.session.scalar(select(func.count(Call.id))) == 0
    assert db.session.scalar(select(func.count(Ledger.id))) == 0
    assert db.session.get(Tenant, 1).balance == 0
    assert db.session.get(CdrPartition, part.id).accepted_at is None


def test_changed_digest_and_unsettled_days_cannot_be_accepted(configured):
    account, actor = configured
    part = reviewable(account, [raw()])
    with pytest.raises(ValueError):
        approve(part.id, 'stale-browser-digest', actor)
    save_snapshot(account, DAY, canonical_rows([raw(), raw(to='447700900124')], account, DAY), 1)
    assert part.status == 'observing'
    with pytest.raises(ValueError):
        approve(part.id, part.digest, actor)


def test_same_second_distinct_calls_and_reordering(configured):
    account, _ = configured
    rows = [raw(), raw(to='447700900124'), raw(**{'from': 'anonymous'})]
    assert len(canonical_rows(rows, account, DAY)) == 3
    assert canonical_rows(rows, account, DAY) == canonical_rows(list(reversed(rows)), account, DAY)
    with pytest.raises(ValueError, match='different costs'):
        canonical_rows([raw(), raw(amount='0.02')], account, DAY)


def test_complete_pagination_and_changed_totals(configured, monkeypatch):
    account, _ = configured
    def fake(token, params):
        return {'calls': [raw()], 'pagination': {'page': params['page'], 'per_page': 1,
                                               'total_pages': 2, 'total_records': 2}}
    monkeypatch.setattr('app.reconciliation.read_calls', fake)
    assert sum(g['count'] for g in fetch_partition('unused', account, DAY).values()) == 2
    def incomplete(token, params):
        result = fake(token, params)
        if params['page'] == 2:
            result['pagination']['total_records'] = 3
        return result
    monkeypatch.setattr('app.reconciliation.read_calls', incomplete)
    with pytest.raises(ValueError):
        fetch_partition('unused', account, DAY)
    assert db.session.scalar(select(func.count(CdrPartition.id))) == 0


def test_cutover_blocks_previously_billed_provider_id(configured):
    from app.billing import bill_call
    account, actor = configured
    bill_call(account.id, 'provider-original', datetime(2025, 1, 1, 12, tzinfo=timezone.utc), '447700900123', 30)
    part = reviewable(account, [raw()])
    with pytest.raises(ValueError, match='cutover'):
        approve(part.id, part.digest, actor)


def test_customer_cannot_access_reconciliation(signed_in):
    assert signed_in.get('/admin/reconciliation').status_code == 403
    assert signed_in.post('/admin/reconciliation').status_code == 403


def test_scheduled_collection_and_failed_refresh(configured, monkeypatch, app):
    from cryptography.fernet import Fernet
    from app.provider import sync_calls
    account, _ = configured
    config = db.session.get(Integration, 1)
    config.start_date = utcnow().date() - timedelta(days=1)
    config.token_encrypted = Fernet(app.config['ENCRYPTION_KEY']).encrypt(b'test-token').decode()
    db.session.commit()
    def response(token, params):
        assert token == 'test-token'
        return {'calls': [], 'pagination': {'page': 1, 'per_page': 1000, 'total_pages': 0, 'total_records': 0}}
    monkeypatch.setattr('app.reconciliation.read_calls', response)
    result = sync_calls()
    assert result['mode'] == 'shadow' and result['partitions'] == 4
    assert db.session.scalar(select(func.count(CdrPartition.id))) == 4
    assert db.session.scalar(select(func.count(Ledger.id))) == 0
    for part in db.session.scalars(select(CdrPartition)):
        part.checked_at = utcnow() - timedelta(minutes=6)
    db.session.commit()
    sync_calls()
    assert db.session.scalar(select(func.count(CdrPartition.id)).where(CdrPartition.status == 'review')) == 2
    def failure(token, params):
        raise ValueError('Provider pagination is invalid.')
    monkeypatch.setattr('app.reconciliation.read_calls', failure)
    with pytest.raises(ValueError, match='Shadow collection failed'):
        sync_calls()
    assert all(p.status == 'fetch_error' for p in db.session.scalars(select(CdrPartition)))
    assert db.session.scalar(select(func.count(Call.id))) == 0


def test_admin_can_review_nonempty_partition(configured, client):
    from werkzeug.security import generate_password_hash
    account, actor = configured
    db.session.get(User, actor).password_hash = generate_password_hash('recon-test-password')
    db.session.commit()
    part = reviewable(account, [raw(), raw()])
    client.post('/login', data={'email': 'recon@example.test', 'password': 'recon-test-password'})
    response = client.get('/admin/reconciliation')
    assert response.status_code == 200 and b'2 answered calls' in response.data
    assert client.post('/admin/reconciliation', data={'partition_id': part.id, 'digest': part.digest}).status_code == 400
    assert client.post('/admin/reconciliation', data={'partition_id': part.id, 'digest': part.digest, 'reviewed': 'on'}).status_code == 302
    assert db.session.scalar(select(func.count(Call.id))) == 2
