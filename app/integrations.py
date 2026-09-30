"""Server-side credentials and read-only provider inspection. No raw secrets in UI."""
from datetime import timedelta
from decimal import Decimal

import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from .models import Integration, db, utcnow

API_URL = 'https://app.didlogic.com/api/v1/calls'


def settings():
    return db.session.get(Integration, 1)


def decrypt_token(integration):
    if not integration or not integration.token_encrypted:
        raise ValueError('Save your DID Logic API token first.')
    try:
        return Fernet(current_app.config['ENCRYPTION_KEY']).decrypt(integration.token_encrypted.encode()).decode()
    except InvalidToken:
        raise ValueError('The saved token cannot be decrypted. Check the server encryption key.') from None


def read_calls(token, params, client=None):
    client = client or requests
    try:
        response = client.get(API_URL, headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
                              params=params, timeout=(10, 45), allow_redirects=False)
    except requests.RequestException:
        raise ValueError('Could not reach DID Logic. Check connectivity and try again.') from None
    messages = {401: 'DID Logic rejected the token.', 403: 'DID Logic denied access. Check token permissions and account status.',
                429: 'DID Logic rate limit reached. Try again later.'}
    if response.status_code != 200:
        raise ValueError(messages.get(response.status_code, f'DID Logic returned HTTP {response.status_code}.'))
    try:
        payload = response.json(parse_float=Decimal)
    except (ValueError, TypeError):
        raise ValueError('DID Logic returned an invalid JSON response.') from None
    if not isinstance(payload, dict) or not isinstance(payload.get('calls'), list) or not isinstance(payload.get('pagination'), dict):
        raise ValueError('Unexpected CDR response structure. No charges were created.')
    if any(not isinstance(row, dict) for row in payload['calls']):
        raise ValueError('Unexpected CDR record structure. No charges were created.')
    return payload


def preview_calls(integration):
    today = utcnow().date()
    payload = read_calls(decrypt_token(integration), {'type': 'sip', 'missed': '0', 'from': (today - timedelta(days=7)).isoformat(),
                                                    'to': today.isoformat(), 'page': 1, 'per_page': 20})
    fields = sorted({str(key)[:80] for row in payload['calls'] for key in row})
    rows = []
    for source in payload['calls'][:20]:
        row = {key: str(source[key])[:200] if source.get(key) is not None else None
               for key in ('timestamp', 'type', 'sip_account', 'to', 'duration', 'per_minute', 'amount')}
        row['call_id'] = str(source[integration.call_id_field])[:200] if source.get(integration.call_id_field) is not None else None
        rows.append(row)
    return fields, rows
