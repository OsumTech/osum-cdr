from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.integrations import preview_calls, read_calls
from app.models import Integration


def test_read_only_preview_allows_only_safe_fields(app, monkeypatch):
    token = 'unit-test-private-token'
    config = Integration(token_encrypted=Fernet(app.config['ENCRYPTION_KEY']).encrypt(token.encode()).decode(), call_id_field='id')
    captured = {}
    def get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return SimpleNamespace(status_code=200, json=lambda **kwargs: {'calls': [
            {'id': 'unique-call', 'timestamp': '2026-09-01T00:00:00Z', 'duration': 60, 'sip_account': 'provider-1',
             'to': '+447700900123', 'type': 'sip', 'amount': '0.02', 'per_minute': '0.02',
             'api_token': 'should-never-be-stored', 'sip_password': 'should-never-be-shown'}], 'pagination': {}})
    monkeypatch.setattr('app.integrations.requests.get', get)
    fields, rows = preview_calls(config)
    assert captured['headers']['Authorization'] == f'Bearer {token}'
    assert captured['allow_redirects'] is False
    assert captured['params']['per_page'] == 20
    assert rows[0]['call_id'] == 'unique-call'
    assert 'should-never' not in str(rows)
    assert 'api_token' not in rows[0]


def test_provider_errors_never_echo_response_body(app, monkeypatch):
    monkeypatch.setattr('app.integrations.requests.get', lambda *a, **kw: SimpleNamespace(status_code=401, text='secret response body'))
    with pytest.raises(ValueError, match='rejected the token') as error:
        read_calls('private', {})
    assert 'secret' not in str(error.value)
