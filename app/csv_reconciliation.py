"""Read-only comparison of a manual provider CSV against a saved API snapshot."""
import csv
import hashlib
import io
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .billing import decimal_amount, normalise_number

MAX_BYTES = 900000
REQUIRED = {'Date', 'Time', 'SIP ID', 'Type', 'From', 'To', 'Duration', 'Charge'}


def caller(value):
    value = str(value).strip()
    match = re.fullmatch(r'.*<([^<>]+)>', value)
    if match:
        value = match.group(1).strip()
    try:
        return normalise_number(value)
    except ValueError:
        return value


def key(started, source, destination, duration, cost):
    return (started.astimezone(timezone.utc).isoformat(), caller(source),
            normalise_number(destination), int(duration), str(decimal_amount(cost)))


def totals(records):
    return {'calls': sum(records.values()),
            'seconds': sum(row[3] * count for row, count in records.items()),
            'wholesale': str(sum((Decimal(row[4]) * count for row, count in records.items()), Decimal(0)))}


def compare_csv(data, partition, offset_minutes):
    inbound = bool(getattr(partition, 'inbound', False))
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Choose a non-empty CSV smaller than 900 KB. Export one SIP account/day at a time.')
    if not -720 <= offset_minutes <= 840 or offset_minutes % 15:
        raise ValueError('Choose the UTC offset used by the provider export on that date.')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('Upload the original UTF-8 CSV from DID Logic, not an Excel workbook.') from None
    reader = csv.DictReader(io.StringIO(text, newline=''))
    if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or not REQUIRED <= set(reader.fieldnames):
        raise ValueError('CSV headers are missing or duplicated. Use the original DID Logic export.')
    expected = Counter()
    for group in partition.rows.values():
        identity = group['identity']
        expected[key(datetime.fromisoformat(identity[3]), identity[4], identity[5], identity[6], group['cost'])] += group['count']
    actual = Counter()
    ignored, zero_duration = 0, 0
    export_zone = timezone(timedelta(minutes=offset_minutes))
    try:
        for index, row in enumerate(reader, start=2):
            if index > 100002:
                raise ValueError('CSV has too many records.')
            if None in row or any(row.get(name) is None for name in REQUIRED):
                raise ValueError(f'CSV row {index} has an invalid number of columns.')
            if inbound:
                if row['Type'].strip().upper() == 'SIP TERM':
                    ignored += 1
                    continue
                receiving = row.get('DID number') or row.get('DID Number') or row.get('did_number') or row['To']
                try:
                    receiving = normalise_number(receiving)
                except ValueError:
                    raise ValueError('Inbound CSV must identify the receiving DID in a DID number column or To; a forwarding target is insufficient.') from None
                if receiving != normalise_number(partition.account.number):
                    ignored += 1
                    continue
                if row['Type'].strip().upper() not in ('INCOMING', 'INBOUND', 'DID', 'DID ORIG'):
                    raise ValueError('Unrecognised inbound CSV call type. Confirm the provider export format before comparing.')
            elif row['SIP ID'].strip() != partition.account.provider_id:
                ignored += 1
                continue
            started = datetime.strptime(row['Date'].strip() + ' ' + row['Time'].strip(), '%m/%d/%y %I:%M:%S %p').replace(tzinfo=export_zone).astimezone(timezone.utc)
            if started.date() != partition.day:
                ignored += 1
                continue
            if not inbound and row['Type'].strip() != 'SIP TERM':
                raise ValueError(f'CSV row {index} is not an outbound SIP call.')
            duration = int(row['Duration'])
            cost = decimal_amount(row['Charge'])
            if duration < 0 or cost < 0:
                raise ValueError(f'CSV row {index} has a negative duration or charge.')
            if duration == 0:
                if cost != 0 and not inbound:
                    raise ValueError(f'CSV row {index} has a charge but zero duration; investigate before reconciliation.')
                if cost == 0:
                    zero_duration += 1
                    continue
            if not row['From'].strip():
                raise ValueError(f'CSV row {index} has no caller.')
            actual[key(started, row['From'], receiving if inbound else row['To'], duration, cost)] += 1
    except (InvalidOperation, OverflowError, csv.Error) as exc:
        raise ValueError('CSV contains malformed values.') from exc
    if not actual and not zero_duration:
        raise ValueError('The file contains no records for the selected SIP account and UTC day. Check the date and export timezone.')
    differences = []
    for kind, remainder in (('Only in CSV', actual - expected), ('Only in API snapshot', expected - actual)):
        for row, count in sorted(remainder.items()):
            differences.append({'source': kind, 'time': row[0], 'caller': row[1], 'destination': row[2],
                                'seconds': row[3], 'cost': row[4], 'occurrences': count})
    return {'matched': not differences, 'csv': totals(actual), 'api': totals(expected),
            'ignored': ignored, 'zero_duration': zero_duration, 'differences': differences,
            'file_sha256': hashlib.sha256(data).hexdigest(), 'offset_minutes': offset_minutes,
            'snapshot_digest': partition.digest}
