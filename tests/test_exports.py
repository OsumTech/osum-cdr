from datetime import datetime, timezone
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from app.billing import bill_call
from app.models import SipAccount, db


def test_xlsx_export_tenant_scope_dates_and_literal_strings(signed_in):
    when = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)
    db.session.get(SipAccount, 1).label = '=HYPERLINK("https://example.test","click")'
    db.session.commit()
    bill_call(1, 'export-a', when, '447700900111', 30, wholesale_cost='0.001')
    bill_call(2, 'export-b', when, '447700900222', 30)
    bill_call(1, 'export-old', when.replace(year=2024), '447700900333', 30)
    response = signed_in.get('/calls/export.xlsx?start=2025-01-01&end=2025-01-01&tenant_id=2')
    assert response.status_code == 200
    assert 'spreadsheetml' in response.content_type
    assert 'no-store' in response.headers['Cache-Control']
    from openpyxl import load_workbook
    workbook = load_workbook(BytesIO(response.data))
    sheet = workbook['Call history']
    assert sheet['A6'].value == when.replace(tzinfo=None)
    assert sheet['B6'].data_type == 's'
    assert sheet['C6'].value == '+447700900111'
    assert sheet['F6'].value == 0.014
    assert sheet.freeze_panes == 'A6'
    assert sheet.auto_filter.ref == 'A5:F6'
    with ZipFile(BytesIO(response.data)) as archive:
        for name in archive.namelist():
            ET.fromstring(archive.read(name))
        sheet = archive.read('xl/worksheets/sheet1.xml').decode()
        assert '447700900111' in sheet
        assert '447700900222' not in sheet and '447700900333' not in sheet
        assert '<f' not in sheet and 'HYPERLINK' in sheet and 'inlineStr' in sheet
        assert '0.014000' in sheet and 'wholesale' not in sheet.lower()
    filtered = signed_in.get('/calls/export.xlsx?start=2025-01-01&end=2025-01-01&destination=999')
    with ZipFile(BytesIO(filtered.data)) as archive:
        assert b'447700900111' not in archive.read('xl/worksheets/sheet1.xml')


def test_export_requires_login_and_valid_dates(client, signed_in):
    assert signed_in.get('/calls/export.xlsx?start=invalid').status_code == 400
    assert signed_in.get('/calls/export.xlsx?start=2026-01-02&end=2026-01-01').status_code == 400
    signed_in.post('/logout')
    assert client.get('/calls/export.xlsx').status_code == 302
