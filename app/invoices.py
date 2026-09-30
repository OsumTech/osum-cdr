from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def render_invoice(invoice):
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=(210 * mm, 297 * mm),
                                 rightMargin=20 * mm, leftMargin=20 * mm, topMargin=20 * mm, bottomMargin=20 * mm,
                                 title=f'Osumtech invoice {invoice.number}', author='Osumtech')
    styles = getSampleStyleSheet()
    styles['Normal'].textColor = colors.HexColor('#141210')
    logo = Path(__file__).parent / 'static' / 'brand' / 'logo-light.png'
    story = [Image(str(logo), width=48 * mm, height=12 * mm, hAlign='LEFT'), Spacer(1, 12 * mm),
             Paragraph(f'Invoice {invoice.number}', styles['Title']),
             Paragraph(f'Customer: {escape(invoice.customer_name)}', styles['Normal']),
             Paragraph(f'Billing month: {invoice.period} · Issued: {invoice.issued_at:%d %B %Y}', styles['Normal']),
             Paragraph(f'Currency: {invoice.currency}', styles['Normal']), Spacer(1, 10 * mm)]
    rows = [['Description', f'Amount ({invoice.currency})'],
            ['Call usage posted during billing month', f'{invoice.usage:,.6f}'],
            ['DID activation, setup & recurring charges', f'{invoice.subscriptions:,.6f}'],
            ['Total charged to prepaid balance', f'{invoice.total:,.6f}']]
    table = Table(rows, colWidths=[118 * mm, 52 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ECE6DA')),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#141210')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LINEBELOW', (0, 0), (-1, 0), .5, colors.HexColor('#DCD4C6')),
        ('TOPPADDING', (0, 0), (-1, -1), 12), ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
    ]))
    story.extend([table, Spacer(1, 10 * mm), Paragraph('Prepaid usage statement. These charges have already been posted to your balance; this is not a request for an additional payment.', styles['Normal']),
                  Spacer(1, 5 * mm), Paragraph('Charges are shown to six decimal places to match the ledger. Late-arriving usage appears in the month it is posted.', styles['Normal'])])
    document.build(story)
    return output.getvalue()
