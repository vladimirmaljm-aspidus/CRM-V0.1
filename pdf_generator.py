"""Server-side generator PDF-a za ponude i fakture.

Zašto server-side: klijent u portalu treba da preuzme PDF u trenutku kada
admin sačuva ponudu — bez browser-side PDF generisanja. Takođe, svi podaci o
proizvodu (HS code, specifikacija, pakovanje, sertifikati) moraju da uđu u PDF
identično kao što su u CRM bazi (ranije je jspdf preskakao neka polja).

Koristi reportlab (bez spoljnih zavisnosti osim requirements.txt)."""

import io
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image
)
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

from config import DB_FILE, UPLOAD_FOLDER
from utils import decrypt_data

logger = logging.getLogger(__name__)


def _fmt_money(amount, currency=""):
    try:
        if amount is None or amount == "": return "-"
        v = float(amount)
        return f"{v:,.2f} {currency}".strip()
    except Exception:
        return f"{amount} {currency}".strip()


def _fetch_company_and_settings():
    """Ucitava podatke firme i settings (last invoice num, VAT itd)."""
    with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key='company'")
        comp_row = c.fetchone()
        c.execute("SELECT value FROM settings WHERE key='settings'")
        gen_row = c.fetchone()
    company = decrypt_data(comp_row[0]) if comp_row and comp_row[0] else {}
    settings = decrypt_data(gen_row[0]) if gen_row and gen_row[0] else {}
    return (company if isinstance(company, dict) else {},
            settings if isinstance(settings, dict) else {})


def _fetch_partner(partner_id):
    if not partner_id: return {}
    with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
        c = conn.cursor()
        c.execute("SELECT data FROM partners WHERE id=?", (partner_id,))
        row = c.fetchone()
    if not row: return {}
    p = decrypt_data(row[0])
    return p if isinstance(p, dict) else {}


def _fetch_product(product_id):
    if not product_id: return {}
    with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
        c = conn.cursor()
        c.execute("SELECT data FROM products WHERE id=?", (product_id,))
        row = c.fetchone()
    if not row: return {}
    p = decrypt_data(row[0])
    return p if isinstance(p, dict) else {}


def _brand_color(company):
    """Vraca RGB tuple za primary boju iz company.brandColor (hex) ili default plavu."""
    hexc = (company or {}).get('brandColor', '#2563eb')
    try:
        hexc = hexc.lstrip('#')
        return colors.HexColor('#' + hexc)
    except Exception:
        return colors.HexColor('#2563eb')


def _para(text, style):
    """Sigurno vraca Paragraph (escape osnovnih html karaktera)."""
    s = "" if text is None else str(text)
    s = s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br/>')
    return Paragraph(s, style)


def _build_styles():
    styles = getSampleStyleSheet()
    return {
        'h1': ParagraphStyle('H1', parent=styles['Heading1'], fontSize=22, textColor=colors.HexColor('#101828'),
                             leading=26, spaceAfter=4, leftIndent=0),
        'h2': ParagraphStyle('H2', parent=styles['Heading2'], fontSize=13, textColor=colors.HexColor('#101828'),
                             leading=16, spaceAfter=6, spaceBefore=10),
        'body': ParagraphStyle('Body', parent=styles['Normal'], fontSize=9.5, textColor=colors.HexColor('#344054'),
                               leading=13),
        'small': ParagraphStyle('Small', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#667085'),
                                leading=10),
        'label': ParagraphStyle('Label', parent=styles['Normal'], fontSize=7.5, textColor=colors.HexColor('#667085'),
                                leading=10, textTransform='uppercase'),
        'right': ParagraphStyle('Right', parent=styles['Normal'], fontSize=9.5, textColor=colors.HexColor('#101828'),
                                alignment=TA_RIGHT, leading=13),
        'center': ParagraphStyle('Center', parent=styles['Normal'], fontSize=9.5, alignment=TA_CENTER, leading=13),
    }


def build_offer_pdf(offer, company=None, settings=None):
    """Generise PDF ponude i vraca bytes. Ukljucuje SVE podatke o proizvodu iz CRM baze."""
    if company is None or settings is None:
        c, s = _fetch_company_and_settings()
        company = company or c
        settings = settings or s

    buyer = _fetch_partner(offer.get('customerId'))
    # Sve stavke
    items = offer.get('items') or []
    if not items and offer.get('productId'):
        items = [{
            'productId': offer.get('productId'),
            'quantity': offer.get('quantity'),
            'price': offer.get('sellingPrice') or offer.get('price'),
            'unit': offer.get('unit'),
        }]

    styles = _build_styles()
    primary = _brand_color(company)
    styles['h1'].textColor = primary

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15*mm, rightMargin=15*mm,
                            topMargin=15*mm, bottomMargin=18*mm,
                            title=f"Offer {offer.get('offerNo', '')}")

    story = []

    # HEADER (naziv firme + tip dokumenta)
    header_row = [
        [_para(f"<b>{company.get('name', 'Aspidus')}</b>", styles['h1']),
         _para("FIRM CORPORATE OFFER", styles['right'])],
        [_para(f"{(company.get('address') or '').replace(chr(10), ', ')}<br/>Tax ID: {company.get('taxId', '')}", styles['small']),
         _para(f"Offer No.: <b>{offer.get('offerNo', '')}</b><br/>Date: {(offer.get('date') or '')[:10]}<br/>Valid until: <b>{offer.get('validUntil') or 'N/A'}</b>", styles['right'])]
    ]
    header_tbl = Table(header_row, colWidths=[100*mm, 80*mm])
    header_tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LINEBELOW', (0,1), (-1,1), 0.6, primary),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 8*mm))

    # PARTIES: FROM / TO
    to_lines = [
        f"<b>{buyer.get('companyName', '') or 'Buyer'}</b>",
        buyer.get('contact', {}).get('person', ''),
        (buyer.get('address', {}).get('street') or ''),
        f"{(buyer.get('address', {}).get('city') or '')} {(buyer.get('address', {}).get('country') or '')}".strip(),
        f"Tax ID: {buyer.get('taxId', '')}" if buyer.get('taxId') else '',
        f"Email: {buyer.get('contact', {}).get('email') or buyer.get('email', '')}" if (buyer.get('contact', {}).get('email') or buyer.get('email')) else ''
    ]
    parties = Table([
        [_para("<b>FROM:</b>", styles['label']), _para("<b>TO:</b>", styles['label'])],
        [_para(f"<b>{company.get('name', '')}</b><br/>{(company.get('address') or '').replace(chr(10), '<br/>')}<br/>Tax ID: {company.get('taxId', '')}", styles['body']),
         _para("<br/>".join([l for l in to_lines if l]), styles['body'])]
    ], colWidths=[90*mm, 90*mm])
    parties.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOX', (0,0), (0,-1), 0.4, colors.HexColor('#e7eaef')),
        ('BOX', (1,0), (1,-1), 0.4, colors.HexColor('#e7eaef')),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(parties)
    story.append(Spacer(1, 6*mm))

    # Za svaku stavku ubaci detaljnu spec kartu
    for idx, item in enumerate(items):
        prod = _fetch_product(item.get('productId'))
        supply_offers = prod.get('supplyOffers') or []
        # Ako ponuda ili stavka referiše konkretnu varijantu supply-a (indeks ili supplierId),
        # koristi tu varijantu — za PDF su relevantni SPECIFICNI podaci te varijante
        # (spec, pakovanje, sertifikati) jer se ista roba iz razlicitih zemalja
        # razlikuje po tim atributima.
        supply = {}
        vi = item.get('supplyOfferIndex')
        if isinstance(vi, int) and 0 <= vi < len(supply_offers):
            supply = supply_offers[vi]
        elif item.get('supplierId'):
            for so in supply_offers:
                if so.get('supplierId') == item.get('supplierId'):
                    supply = so; break
        if not supply and supply_offers:
            supply = supply_offers[0]

        spec_rows = [
            ['Product Name', prod.get('name') or item.get('productName', '')],
            ['HS Code', prod.get('hsCode', '')],
            ['SKU / Article', prod.get('sku', '')],
            ['Brand', prod.get('brand', '')],
            ['Origin', supply.get('country') or prod.get('origin', '')],
            ['Category', prod.get('category', '')],
            # Packaging: varijanta > ponuda > globalno na proizvodu
            ['Packaging', item.get('packaging') or supply.get('packaging') or offer.get('packaging') or prod.get('packaging', '')],
            ['Lead Time', offer.get('leadTime') or supply.get('leadTime') or prod.get('leadTime', '')],
            ['Incoterm', item.get('incoterm') or supply.get('incoterm') or offer.get('incoterm', '')],
        ]
        # Sertifikati iz varijante
        if supply.get('certificates'):
            spec_rows.append(['Certificates', supply.get('certificates', '')])
        # COA
        coa = prod.get('coaParams') or []
        if coa:
            coa_str = "; ".join(f"{c.get('name')}: {c.get('value')}" for c in coa if c.get('name'))
            spec_rows.append(['COA Parameters', coa_str])
        # Container
        logistics = prod.get('logistics') or {}
        if logistics.get('cap20') or logistics.get('cap40'):
            spec_rows.append(['Container Capacity',
                              f"20'FCL: {logistics.get('cap20') or '-'} MT   |   40'FCL: {logistics.get('cap40') or '-'} MT"])

        story.append(_para(f"<b>Item {idx+1}: {prod.get('name') or item.get('productName', '')}</b>", styles['h2']))
        # Spec table
        spec_data = [[_para(f"<b>{k}</b>", styles['small']), _para(str(v or '-'), styles['body'])] for k, v in spec_rows if v]
        if spec_data:
            spec_tbl = Table(spec_data, colWidths=[45*mm, 135*mm])
            spec_tbl.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#f4f6f9')),
                ('BOX', (0,0), (-1,-1), 0.4, colors.HexColor('#e7eaef')),
                ('INNERGRID', (0,0), (-1,-1), 0.3, colors.HexColor('#e7eaef')),
                ('LEFTPADDING', (0,0), (-1,-1), 6),
                ('RIGHTPADDING', (0,0), (-1,-1), 6),
                ('TOPPADDING', (0,0), (-1,-1), 4),
                ('BOTTOMPADDING', (0,0), (-1,-1), 4),
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ]))
            story.append(spec_tbl)
            story.append(Spacer(1, 3*mm))

        # Detaljna spec kao paragraph (ako je duga)
        # Prioritet: varijantno-specifična spec > opsta spec proizvoda
        variant_spec = supply.get('spec') if isinstance(supply, dict) else None
        detail_spec = variant_spec or prod.get('detailedSpec')
        if detail_spec:
            label = 'Detailed Specification'
            if variant_spec:
                label = f"Detailed Specification ({supply.get('country', '')})".strip(' ()')
            story.append(_para(f"<b>{label}</b>", styles['label']))
            story.append(_para(detail_spec, styles['body']))
            story.append(Spacer(1, 4*mm))

    # FINANCIJE — tabela stavki
    story.append(_para("<b>Commercial Terms</b>", styles['h2']))
    fin_head = [['Description', 'Quantity', 'Unit Price', 'Total']]
    fin_rows = []
    subtotal = 0.0
    for item in items:
        prod = _fetch_product(item.get('productId'))
        desc = prod.get('name') or item.get('productName') or 'Product'
        qty = float(item.get('quantity') or 0)
        price = float(item.get('price') or item.get('sellingPrice') or offer.get('sellingPrice') or 0)
        line = qty * price
        subtotal += line
        fin_rows.append([desc, f"{qty:,.2f} {item.get('unit', '')}",
                         _fmt_money(price, offer.get('currency', '')),
                         _fmt_money(line, offer.get('currency', ''))])
    for svc in offer.get('services') or []:
        subtotal += float(svc.get('price') or 0)
        fin_rows.append([f"Service: {svc.get('name', '')}", '-', '-', _fmt_money(svc.get('price'), offer.get('currency', ''))])

    fin_tbl = Table(fin_head + fin_rows,
                    colWidths=[80*mm, 30*mm, 35*mm, 35*mm])
    fin_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), primary),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
        ('ALIGN', (0,0), (0,-1), 'LEFT'),
        ('BOX', (0,0), (-1,-1), 0.4, colors.HexColor('#101828')),
        ('INNERGRID', (0,0), (-1,-1), 0.3, colors.HexColor('#e7eaef')),
        ('FONTSIZE', (0,1), (-1,-1), 9),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(fin_tbl)
    story.append(Spacer(1, 3*mm))

    # Sumarno
    total_tbl = Table([
        ['Subtotal', _fmt_money(subtotal, offer.get('currency', ''))],
        ['Grand Total', _fmt_money(subtotal, offer.get('currency', ''))],
    ], colWidths=[145*mm, 35*mm])
    total_tbl.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'RIGHT'),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,-1), (-1,-1), 12),
        ('TEXTCOLOR', (0,-1), (-1,-1), primary),
        ('LINEABOVE', (0,-1), (-1,-1), 1.0, primary),
        ('TOPPADDING', (0,-1), (-1,-1), 6),
        ('BOTTOMPADDING', (0,-1), (-1,-1), 6),
    ]))
    story.append(total_tbl)
    story.append(Spacer(1, 6*mm))

    # LOGISTIKA I PLAĆANJE
    logi_rows = []
    if offer.get('paymentTerms'): logi_rows.append(['Payment Terms', offer.get('paymentTerms')])
    if offer.get('pol'): logi_rows.append(['Port of Loading', offer.get('pol')])
    if offer.get('pod'): logi_rows.append(['Port of Discharge', offer.get('pod')])
    if offer.get('vessel'): logi_rows.append(['Vessel', offer.get('vessel')])
    if offer.get('containerNo'): logi_rows.append(['Container', offer.get('containerNo')])
    if offer.get('leadTime'): logi_rows.append(['Lead Time', offer.get('leadTime')])
    if offer.get('taxClause'): logi_rows.append(['Tax Clause', offer.get('taxClause')])
    if logi_rows:
        story.append(_para("<b>Logistics &amp; Payment</b>", styles['h2']))
        li_data = [[_para(f"<b>{k}</b>", styles['small']), _para(str(v), styles['body'])] for k, v in logi_rows]
        li_tbl = Table(li_data, colWidths=[45*mm, 135*mm])
        li_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#f4f6f9')),
            ('BOX', (0,0), (-1,-1), 0.4, colors.HexColor('#e7eaef')),
            ('INNERGRID', (0,0), (-1,-1), 0.3, colors.HexColor('#e7eaef')),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
            ('RIGHTPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        story.append(li_tbl)
        story.append(Spacer(1, 5*mm))

    if offer.get('notes'):
        story.append(_para("<b>Notes</b>", styles['h2']))
        story.append(_para(offer.get('notes', ''), styles['body']))
        story.append(Spacer(1, 5*mm))

    # FOOTER: legalna napomena i confidentiality
    story.append(Spacer(1, 4*mm))
    story.append(_para(
        "<font color='#667085'>This offer is confidential and intended solely for the recipient. "
        "Any unauthorized use, disclosure or distribution is prohibited. "
        f"Document generated by {company.get('name', 'Aspidus')} CRM.</font>",
        styles['small']
    ))

    def _footer(canvas, docObj):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#98a2b3'))
        page = canvas.getPageNumber()
        canvas.drawRightString(A4[0] - 15*mm, 10*mm, f"Page {page}")
        canvas.drawString(15*mm, 10*mm, f"{company.get('name', '')} · Offer {offer.get('offerNo', '')}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def save_offer_pdf_to_vault(offer):
    """Generise PDF ponude i cuva u shared_documents (vault) + fajl u uploads/.
    Vraca (doc_id, file_url) ili (None, None) na gresku."""
    try:
        pdf_bytes = build_offer_pdf(offer)
    except Exception as e:
        logger.error(f"Failed to generate offer PDF: {e}")
        return None, None

    # Fajl
    filename = f"offer_{offer.get('offerNo', 'unknown').replace('/', '_')}_{uuid.uuid4().hex[:8]}.pdf"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    try:
        with open(filepath, 'wb') as f:
            f.write(pdf_bytes)
    except Exception as e:
        logger.error(f"Failed to write PDF to disk: {e}")
        return None, None
    file_url = f"/uploads/{filename}"

    # Vault entry
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    doc = {
        'id': doc_id,
        'partnerId': offer.get('customerId'),
        'productId': offer.get('productId'),
        'docType': 'OFFER',
        'fileName': f"Offer_{offer.get('offerNo', '')}.pdf",
        'fileUrl': file_url,
        'sourceOfferId': offer.get('id'),
        'createdAt': now,
    }
    try:
        with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
            conn.execute("INSERT INTO shared_documents (id, data) VALUES (?, ?)",
                         (doc_id, json.dumps(doc)))
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to save PDF vault entry: {e}")
        # PDF fajl je već tu, ali bez vault entry-ja klijent ne bi mogao da ga preuzme
        return None, None

    return doc_id, file_url
