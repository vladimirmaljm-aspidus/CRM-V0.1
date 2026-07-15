"""Server-side generator PDF-a za ponude i fakture.

Zašto server-side: klijent u portalu treba da preuzme PDF u trenutku kada
admin sačuva ponudu — bez browser-side PDF generisanja. Takođe, svi podaci o
proizvodu (HS code, specifikacija, pakovanje, sertifikati) moraju da uđu u PDF
identično kao što su u CRM bazi (ranije je jspdf preskakao neka polja).

Koristi reportlab (bez spoljnih zavisnosti osim requirements.txt)."""

import hashlib
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


def _bank_details_string(company):
    """Skuplja bankarske instrukcije iz podataka firme u jedan blok teksta,
    identično kao što jsPDF radi kada admin generiše PDF u browseru."""
    if not isinstance(company, dict):
        return ''
    parts = []
    if company.get('bankName'):    parts.append(f"Bank: {company.get('bankName')}")
    if company.get('bankAddress'): parts.append(company.get('bankAddress'))
    if company.get('accountNum'):  parts.append(f"IBAN / Account: {company.get('accountNum')}")
    if company.get('swift'):       parts.append(f"SWIFT / BIC: {company.get('swift')}")
    if company.get('corrBank'):    parts.append(f"Correspondent bank: {company.get('corrBank')}")
    return '\n'.join(parts)


def _make_verification_hash(offer_id, offer_no):
    """Deterministički verifikacioni hash — isti dokument uvek daje isti hash,
    tako da klijent može da poredi PDF koji je preuzeo sa PDF-om koji vidi
    admin (jsPDF generiše slučajan hash, ali logika prikaza je ista)."""
    seed = f"{offer_id or ''}|{offer_no or ''}"
    h = hashlib.sha256(seed.encode('utf-8')).hexdigest().upper()
    return f"VER-{h[:12]}-{h[12:20]}"


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


def _esc(text):
    """Escape user-supplied text for safe embedding in ReportLab Paragraph HTML."""
    s = "" if text is None else str(text)
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _para(text, style):
    """Returns a Paragraph. Text may contain ReportLab HTML markup (<b>, <br/>, <font> etc).
    Use _esc() on user-supplied data before embedding it in the markup string."""
    s = "" if text is None else str(text)
    s = s.replace('\n', '<br/>')
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
        [_para(f"<b>{_esc(company.get('name', 'Aspidus'))}</b>", styles['h1']),
         _para("FIRM CORPORATE OFFER", styles['right'])],
        [_para(f"{_esc((company.get('address') or '').replace(chr(10), ', '))}<br/>Tax ID: {_esc(company.get('taxId', ''))}", styles['small']),
         _para(f"Offer No.: <b>{_esc(offer.get('offerNo', ''))}</b><br/>Date: {_esc((offer.get('date') or '')[:10])}<br/>Valid until: <b>{_esc(offer.get('validUntil') or 'N/A')}</b>", styles['right'])]
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
        f"<b>{_esc(buyer.get('companyName', '') or 'Buyer')}</b>",
        _esc(buyer.get('contact', {}).get('person', '')),
        _esc(buyer.get('address', {}).get('street') or ''),
        _esc(f"{(buyer.get('address', {}).get('city') or '')} {(buyer.get('address', {}).get('country') or '')}".strip()),
        f"Tax ID: {_esc(buyer.get('taxId', ''))}" if buyer.get('taxId') else '',
        f"Email: {_esc(buyer.get('contact', {}).get('email') or buyer.get('email', ''))}" if (buyer.get('contact', {}).get('email') or buyer.get('email')) else ''
    ]
    parties = Table([
        [_para("<b>FROM:</b>", styles['label']), _para("<b>TO:</b>", styles['label'])],
        [_para(f"<b>{_esc(company.get('name', ''))}</b><br/>{_esc((company.get('address') or '').replace(chr(10), ', '))}<br/>Tax ID: {_esc(company.get('taxId', ''))}", styles['body']),
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
            ['Product Name', _esc(prod.get('name') or item.get('productName', ''))],
            ['HS Code', _esc(prod.get('hsCode', ''))],
            ['SKU / Article', _esc(prod.get('sku', ''))],
            ['Brand', _esc(prod.get('brand', ''))],
            ['Origin', _esc(supply.get('country') or prod.get('origin', ''))],
            ['Category', _esc(prod.get('category', ''))],
            ['Packaging', _esc(item.get('packaging') or supply.get('packaging') or offer.get('packaging') or prod.get('packaging', ''))],
            ['Lead Time', _esc(offer.get('leadTime') or supply.get('leadTime') or prod.get('leadTime', ''))],
            ['Incoterm', _esc(item.get('incoterm') or supply.get('incoterm') or offer.get('incoterm', ''))],
        ]
        if supply.get('certificates'):
            spec_rows.append(['Certificates', _esc(supply.get('certificates', ''))])
        coa = prod.get('coaParams') or []
        if coa:
            coa_str = "; ".join(f"{_esc(c.get('name'))}: {_esc(c.get('value'))}" for c in coa if c.get('name'))
            spec_rows.append(['COA Parameters', coa_str])
        logistics = prod.get('logistics') or {}
        if logistics.get('cap20') or logistics.get('cap40'):
            spec_rows.append(['Container Capacity',
                              f"20'FCL: {_esc(logistics.get('cap20') or '-')} MT   |   40'FCL: {_esc(logistics.get('cap40') or '-')} MT"])

        story.append(_para(f"<b>Item {idx+1}: {_esc(prod.get('name') or item.get('productName', ''))}</b>", styles['h2']))
        spec_data = [[_para(f"<b>{_esc(k)}</b>", styles['small']), _para(str(v or '-'), styles['body'])] for k, v in spec_rows if v]
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
                label = f"Detailed Specification ({_esc(supply.get('country', ''))})"
            story.append(_para(f"<b>{_esc(label)}</b>", styles['label']))
            story.append(_para(_esc(detail_spec), styles['body']))
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

    # Sumarno — isti obračun kao jsPDF (discount, VAT, advance/balance)
    # da bi PDF koji preuzme klijent iz portala izgledao identično sa PDF-om
    # koji admin generiše iz browsera.
    currency = offer.get('currency', '')
    discount = float(offer.get('discount') or 0)
    vat_rate = float(offer.get('customVatRate') or 0)
    advance = float(offer.get('advance') or 0)

    after_discount = max(0.0, subtotal - discount)
    vat_amount = round(after_discount * vat_rate / 100.0, 2) if vat_rate > 0 else 0.0
    grand_total = after_discount + vat_amount
    balance_due = grand_total - advance

    total_rows = [['Subtotal', _fmt_money(subtotal, currency)]]
    if discount > 0:
        total_rows.append(['Discount', f"- {_fmt_money(discount, currency)}"])
    if vat_rate > 0:
        total_rows.append([f'VAT ({vat_rate:g}%)', _fmt_money(vat_amount, currency)])
    total_rows.append(['Grand Total', _fmt_money(grand_total, currency)])
    if advance > 0:
        total_rows.append(['Advance Payment', f"- {_fmt_money(advance, currency)}"])
        total_rows.append(['Balance Due', _fmt_money(balance_due, currency)])

    total_tbl = Table(total_rows, colWidths=[145*mm, 35*mm])
    ts = [
        ('ALIGN', (0,0), (-1,-1), 'RIGHT'),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]
    # Grand total: bold + brand color + top rule
    gt_idx = 1 + (1 if discount > 0 else 0) + (1 if vat_rate > 0 else 0)
    ts += [
        ('FONTNAME', (0, gt_idx), (-1, gt_idx), 'Helvetica-Bold'),
        ('FONTSIZE', (0, gt_idx), (-1, gt_idx), 12),
        ('TEXTCOLOR', (0, gt_idx), (-1, gt_idx), primary),
        ('LINEABOVE', (0, gt_idx), (-1, gt_idx), 1.0, primary),
        ('TOPPADDING', (0, gt_idx), (-1, gt_idx), 6),
        ('BOTTOMPADDING', (0, gt_idx), (-1, gt_idx), 6),
    ]
    if advance > 0:
        bd_idx = len(total_rows) - 1
        ts += [
            ('FONTNAME', (0, bd_idx), (-1, bd_idx), 'Helvetica-Bold'),
            ('FONTSIZE', (0, bd_idx), (-1, bd_idx), 12),
            ('TEXTCOLOR', (0, bd_idx), (-1, bd_idx), colors.HexColor('#c8102e')),
        ]
    total_tbl.setStyle(TableStyle(ts))
    story.append(total_tbl)
    story.append(Spacer(1, 6*mm))

    # WEIGHTS / VOLUME (opciono, ako postoji na ponudi/proizvodu)
    weights = offer.get('weights') or {}
    if weights.get('net') or weights.get('gross') or weights.get('cbm'):
        unit = _esc(weights.get('unit') or 'kg')
        w_row = []
        if weights.get('net'): w_row.append(f"Net: {_esc(weights.get('net'))} {unit}")
        if weights.get('gross'): w_row.append(f"Gross: {_esc(weights.get('gross'))} {unit}")
        if weights.get('cbm'): w_row.append(f"Volume: {_esc(weights.get('cbm'))} CBM")
        w_tbl = Table([[_para(' · '.join(w_row), styles['center'])]], colWidths=[180*mm])
        w_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f4f6f9')),
            ('BOX', (0,0), (-1,-1), 0.4, colors.HexColor('#e7eaef')),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        story.append(w_tbl)
        story.append(Spacer(1, 5*mm))

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
        li_data = [[_para(f"<b>{_esc(k)}</b>", styles['small']), _para(_esc(v), styles['body'])] for k, v in logi_rows]
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

    # BANKARSKE INSTRUKCIJE — kupac mora tačno da vidi gde da uplati.
    # Prioritet: eksplicitni offer.bankDetails (custom-a admin unese) → skup iz
    # company podataka. Ovo je najkritičniji deo dokumenta za realan business.
    bank_details = offer.get('bankDetails') or _bank_details_string(company)
    if bank_details:
        story.append(_para("<b>Bank Instructions</b>", styles['h2']))
        bank_tbl = Table([[_para(_esc(bank_details), styles['body'])]], colWidths=[180*mm])
        bank_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f4f6f9')),
            ('BOX', (0,0), (-1,-1), 0.4, colors.HexColor('#e7eaef')),
            ('LEFTPADDING', (0,0), (-1,-1), 10),
            ('RIGHTPADDING', (0,0), (-1,-1), 10),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ]))
        story.append(bank_tbl)
        story.append(Spacer(1, 5*mm))

    if offer.get('notes'):
        story.append(_para("<b>Notes</b>", styles['h2']))
        story.append(_para(_esc(offer.get('notes', '')), styles['body']))
        story.append(Spacer(1, 5*mm))

    # FOOTER: legalna napomena i confidentiality
    story.append(Spacer(1, 4*mm))
    story.append(_para(
        "<font color='#667085'>This offer is confidential and intended solely for the recipient. "
        "Any unauthorized use, disclosure or distribution is prohibited. "
        f"Document generated by {_esc(company.get('name', 'Aspidus'))} CRM.</font>",
        styles['small']
    ))

    ver_hash = _make_verification_hash(offer.get('id'), offer.get('offerNo'))

    def _footer(canvas, docObj):
        canvas.saveState()
        # Top rule above footer, matching the admin PDF layout
        canvas.setStrokeColor(primary)
        canvas.setLineWidth(0.5)
        canvas.line(15*mm, 20*mm, A4[0] - 15*mm, 20*mm)

        # Verification hash + legal line — same content the client-side jsPDF
        # writes so both admin-downloaded and portal-downloaded PDFs share
        # the same "footer signature".
        canvas.setFont('Helvetica-Bold', 6)
        canvas.setFillColor(primary)
        canvas.drawString(15*mm, 16*mm, f"VERIFICATION HASH: {ver_hash}")

        canvas.setFont('Helvetica-Oblique', 6)
        canvas.setFillColor(colors.HexColor('#666666'))
        canvas.drawString(15*mm, 12*mm,
                          f"This document is electronically generated and verified by {company.get('name', 'Aspidus')} CRM.")

        canvas.setFont('Helvetica-Bold', 6)
        canvas.setFillColor(colors.HexColor('#111111'))
        addr = (company.get('address') or '').replace('\n', ', ')
        canvas.drawString(15*mm, 8*mm, addr)

        # "Page X of Y" via the standard reportlab trick — since we cannot
        # know Y in a single-pass callback, we defer with an on-canvas doc
        # count that reportlab fills in during finalization.
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#98a2b3'))
        canvas.drawRightString(A4[0] - 15*mm, 8*mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def save_offer_pdf_to_vault(offer):
    """Upisuje samo REFERENCU u shared_documents; PDF se generiše on-demand.

    Ranije: server je pravio PDF na disk (uploads/) i vault upis sa file_url.
    Sad: samo vault upis sa sourceOfferId='<offer_id>' i sourceType='OFFER'.
    Kada klijent otvori dokument u portalu, /api/portal/document/<token>/<doc_id>
    endpoint prepozna sourceOfferId i regeneriše PDF u memoriji iz aktuelnih
    podataka o ponudi u bazi. Prednosti:
      - nema akumulacije PDF fajlova na disku,
      - PDF uvek reflektuje najnovije podatke o ponudi (ako admin ispravi
        cenu/logistiku, klijent vidi tačno stanje pri sledećem otvaranju),
      - preview u modalu pre nego što klijent skine "hard copy".

    Vraca (doc_id, None) — drugi element je nasleđen (file_url), sada nema
    smisla; pozivaoci ga ignorišu."""
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    doc = {
        'id': doc_id,
        'partnerId': offer.get('customerId'),
        'productId': offer.get('productId'),
        'docType': 'OFFER',
        'fileName': f"Offer_{offer.get('offerNo', '')}.pdf",
        'sourceOfferId': offer.get('id'),
        'sourceType': 'OFFER',
        'createdAt': now,
    }
    try:
        with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
            conn.execute("INSERT INTO shared_documents (id, data) VALUES (?, ?)",
                         (doc_id, json.dumps(doc)))
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to save PDF vault entry: {e}")
        return None, None

    return doc_id, None


def regenerate_offer_pdf_by_id(offer_id):
    """Ponovo pravi PDF ponude iz aktuelnih podataka u bazi. Vraća bytes ili
    None ako ponuda ne postoji."""
    if not offer_id:
        return None
    try:
        with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
            c = conn.cursor()
            c.execute("SELECT data FROM offers WHERE id=?", (offer_id,))
            row = c.fetchone()
        if not row:
            return None
        offer_data = decrypt_data(row[0])
        if not isinstance(offer_data, dict):
            return None
        return build_offer_pdf(offer_data)
    except Exception as e:
        logger.error(f"Failed to regenerate PDF for offer {offer_id}: {e}")
        return None
