"""Small, dependency-free XLSX export. All customer strings are literal cells."""
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED


def literal(value):
    # XML 1.0 cannot represent arbitrary control characters.
    return escape(''.join(c for c in str(value) if c in '\t\n\r' or 32 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF)[:32767])


def cdr_workbook(rows, company, start, end):
    def cell(column, row, value, style=0, numeric=False):
        ref = f'{column}{row}'
        if numeric:
            return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
        return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{literal(value)}</t></is></c>'

    body = ['<row r="1">' + cell('A', 1, 'Osumtech · Call history', 1) + '</row>',
            '<row r="2">' + cell('A', 2, company) + '</row>',
            '<row r="3">' + cell('A', 3, f'{start} to {end} inclusive · UTC · USD') + '</row>']
    headers = ['Time (UTC)', 'SIP account / receiving DID', 'Destination / receiving DID', 'Description', 'Duration (seconds)', 'Charge (USD)']
    body.append('<row r="5">' + ''.join(cell(chr(65+i), 5, h, 1) for i, h in enumerate(headers)) + '</row>')
    for index, (call, label) in enumerate(rows, start=6):
        serial = (call.started_at.replace(tzinfo=None) - datetime(1899, 12, 30)).total_seconds() / 86400
        body.append(f'<row r="{index}">' + cell('A', index, serial, 2, True)
                    + cell('B', index, label) + cell('C', index, '+' + call.destination)
                    + cell('D', index, call.rate_label) + cell('E', index, call.duration, 0, True)
                    + cell('F', index, call.cost, 3, True) + '</row>')
    last = max(5, len(rows) + 5)
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:F{last}"/><sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="5" topLeftCell="A6" activePane="bottomLeft" state="frozen"/>'
        '</sheetView></sheetViews><cols><col min="1" max="1" width="24" customWidth="1"/>'
        '<col min="2" max="3" width="24" customWidth="1"/><col min="4" max="4" width="34" customWidth="1"/>'
        '<col min="5" max="6" width="23" customWidth="1"/></cols><sheetData>' + ''.join(body)
        + f'</sheetData><autoFilter ref="A5:F{last}"/><mergeCells count="3"><mergeCell ref="A1:F1"/>'
        '<mergeCell ref="A2:F2"/><mergeCell ref="A3:F3"/></mergeCells></worksheet>')
    styles = '''<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="2"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/><numFmt numFmtId="165" formatCode="0.0000"/></numFmts>
<fonts count="2"><font><sz val="11"/><color rgb="FF141210"/><name val="Inter"/></font><font><b/><sz val="11"/><color rgb="FF141210"/><name val="Inter"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF6F3EC"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>''')
        archive.writestr('_rels/.rels', '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        archive.writestr('xl/workbook.xml', '''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Call history" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        archive.writestr('xl/_rels/workbook.xml.rels', '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''')
        archive.writestr('xl/styles.xml', styles)
        archive.writestr('xl/worksheets/sheet1.xml', sheet)
    output.seek(0)
    return output
