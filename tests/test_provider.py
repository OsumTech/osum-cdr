from datetime import datetime, timezone

import pytest

from app.models import SipAccount, db
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
    monkeypatch.setenv('LIVE_SYNC_ENABLED', 'true')
    monkeypatch.delenv('DIDLOGIC_CALL_ID_FIELD', raising=False)
    with pytest.raises(ValueError, match='confirmed ID'):
        sync_calls()
