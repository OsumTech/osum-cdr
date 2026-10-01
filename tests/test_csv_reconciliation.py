import csv
from datetime import date, timedelta
from io import BytesIO, StringIO
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from app.csv_reconciliation import compare_csv
from app.models import Call, CdrPartition, CsvReconciliation, Integration, Ledger, SipAccount, User, db, utcnow
from app.reconciliation import approve, canonical_rows, save_snapshot


def csv_bytes(rows):
    text = StringIO(newline='')
    writer = csv.writer(text)
    writer.writerow(['Date', 'Time', 'SIP ID', 'Type', 'From', 'To', 'Duration', 'Charge'])
    writer.writerows(rows)
    return text.getvalue().encode()


@pytest.fixture
def partition(app):
    account = db.session.get(SipAccount, 1)
    db.session.add(Integration(id=1, reconciliation_enabled=True, currency_confirmed=True, token_version=1))
    db.session.commit()
    rows = [{'timestamp': '2025-01-01T23:30:00Z', 'sip_account': account.provider_id, 'type': 'sip',
             'from': '441234567890', 'to': '447700900123', 'duration': 30, 'amount': '0.008'}] * 2
    groups = canonical_rows(rows, account, date(2025, 1, 1))
    save_snapshot(account, date(2025, 1, 1), groups, 1)
    part = db.session.scalar(select(CdrPartition))
    part.checked_at = utcnow() - timedelta(minutes=6)
    db.session.commit()
    save_snapshot(account, date(2025, 1, 1), groups, 1)
    return part


def sample_row():
    return ['01/02/25', '12:30:00 am', 'provider-1', 'SIP TERM', '"Name" <441234567890>', '447700900123', '30', '0.008']


def test_occurrences_timezone_and_cost_comparison(partition):
    row = sample_row()
    result = compare_csv(csv_bytes([row, row]), partition, 60)
    assert result['matched'] and result['csv'] == result['api']
    assert result['csv']['calls'] == 2 and result['csv']['seconds'] == 60
    assert compare_csv(csv_bytes([row]), partition, 60)['matched'] is False
    altered = row.copy()
    altered[-1] = '0.009'
    assert len(compare_csv(csv_bytes([row, altered]), partition, 60)['differences']) == 2
    with pytest.raises(ValueError, match='no records'):
        compare_csv(csv_bytes([row]), partition, 0)


def test_malformed_and_zero_duration_charge_rejected(partition):
    with pytest.raises(ValueError, match='headers'):
        compare_csv(b'Bad,Headers\n1,2', partition, 0)
    row = sample_row()
    row[-2] = '0'
    with pytest.raises(ValueError, match='zero duration'):
        compare_csv(csv_bytes([row]), partition, 60)
    with pytest.raises(ValueError, match='900 KB'):
        compare_csv(b'x' * 900001, partition, 0)


def test_admin_upload_is_read_only_and_mismatch_blocks_billing(partition, client):
    owner = User(email='csv-owner@example.test', is_admin=True, password_hash=generate_password_hash('test-csv-password'))
    db.session.add(owner)
    db.session.commit()
    client.post('/login', data={'email': owner.email, 'password': 'test-csv-password'})
    path = f'/admin/reconciliation/{partition.id}/csv'
    assert client.get(path).status_code == 200
    def upload(rows):
        return client.post(path, data={'offset_minutes': '60', 'complete_export': 'on',
                                      'csv_file': (BytesIO(csv_bytes(rows)), 'provider.csv')})
    assert upload([sample_row()]).status_code == 302
    assert db.session.scalar(select(func.count(Ledger.id))) == 0
    assert db.session.scalar(select(func.count(Call.id))) == 0
    with pytest.raises(ValueError, match='CSV comparison'):
        approve(partition.id, partition.digest, owner.id)
    result = db.session.scalar(select(CsvReconciliation))
    download = client.get(f'/admin/reconciliation/csv/{result.id}/download')
    assert download.status_code == 200 and b'Only in API snapshot' in download.data
    assert upload([sample_row(), sample_row()]).status_code == 302
    assert b'Matched' in client.get(path).data
    assert approve(partition.id, partition.digest, owner.id) == 2


def test_customer_cannot_read_or_upload_reports(partition, signed_in):
    path = f'/admin/reconciliation/{partition.id}/csv'
    assert signed_in.get(path).status_code == 403
    assert signed_in.post(path).status_code == 403
    assert signed_in.get('/admin/reconciliation/csv/1/download').status_code == 403
