from datetime import datetime, timezone

import pytest

from cryptography.fernet import Fernet
from sqlalchemy import func, select
from app.models import Call, Integration, Ledger, SipAccount, db, utcnow
from app.provider import parse_call, sync_calls


def row(**overrides):
    return {'id': 'stable-id', 'type': 'sip', 'duration': 60, 'sip_account': 'provider-1',
            'timestamp': '2025-01-01T12:00:00Z', 'to': '+447700900123', **overrides}


def test_parser_requires_stable_id_and_matching_account(app):
    account = db.session.get(SipAccount, 1)
    assert parse_call(row(), account, 'id')[0] == 'stable-id'
    with pytest.raises(ValueError, match='stable unique'):
        parse_call(row(id=None), account, 'id')
    with pytest.raises(ValueError, match='mapping'):
        parse_call(row(sip_account='another-account'), account, 'id')
    with pytest.raises(ValueError, match='timezone'):
        parse_call(row(timestamp='2025-01-01T12:00:00'), account, 'id')
    assert parse_call(row(duration=0), account, 'id') is None


def test_sync_disabled_by_default(app, monkeypatch):
    monkeypatch.delenv('LIVE_SYNC_ENABLED', raising=False)
    assert sync_calls() == {'enabled': False, 'imported': 0}


def test_sync_refuses_missing_configuration(app, monkeypatch):
    db.session.add(Integration(id=1, enabled=True))
    db.session.commit()
    with pytest.raises(ValueError, match='confirmed ID'):
        sync_calls()


def test_sync_uses_saved_token_and_records_wholesale_once(app, monkeypatch):
    token = 'stored-test-token'
    config = Integration(id=1, enabled=True, last_test_ok=True, currency_confirmed=True,
        call_id_field='id', start_date=utcnow().date(),
        token_encrypted=Fernet(app.config['ENCRYPTION_KEY']).encrypt(token.encode()).decode())
    db.session.add(config)
    db.session.commit()
    monkeypatch.setenv('DIDLOGIC_TOKEN', 'obsolete-env-token')
    def response(saved_token, params, client):
        assert saved_token == token
        return {'calls': [row(id=f"call-{params['sip_account']}", sip_account=params['sip_account'],
            timestamp=utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
            per_minute='0.01', amount='0.01')], 'pagination': {}}
    monkeypatch.setattr('app.provider.read_calls', response)
    assert sync_calls()['imported'] == 2
    assert sync_calls()['imported'] == 0
    assert db.session.scalar(select(func.count(Call.id))) == 2
    assert db.session.scalar(select(func.count(Ledger.id))) == 2
    assert str(db.session.scalar(select(Call.wholesale_cost).limit(1))) == '0.010000'
    assert db.session.get(Integration, 1).last_sync_at is not None
