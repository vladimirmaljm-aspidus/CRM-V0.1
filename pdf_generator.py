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
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing

from utils import decrypt_data

logger = logging.getLogger(__name__)


def _sepa_epc_payload(beneficiary, iban, amount_eur, remittance='', bic='', reference=''):
    """Formira EPC069-12 v002 QR payload za SEPA 'Scan to Pay'.

    Struktura je 8-12 linija razdvojenih \\n:
        BCD          (service tag, fiksno)
        002          (verzija)
        1            (character set — 1 = UTF-8)
        SCT          (SEPA Credit Transfer)
        BIC          (opciono u v002)
        Beneficiary  (max 70 karaktera)
        IBAN         (obavezno, bez razmaka)
        EUR<amount>  (currency + amount, max EUR 999999999.99)
        Purpose      (opciono, 4 char SEPA purpose code)
        Reference    (opciono, structured creditor reference)
        Remittance   (max 140 chars — free-form)
        Information  (max 70 chars — additional beneficiary info)

    Bez amount-a QR vodi na "enter amount manually" screen na banka app-u.
    Bez IBAN-a payload je invalid — vratimo None.
    """
    iban = str(iban or '').replace(' ', '').replace('-', '').upper().strip()
    if not iban or len(iban) < 15:
        return None
    beneficiary = str(beneficiary or '')[:70].strip()
    if not beneficiary:
        return None
    lines = [
        'BCD',
        '002',
        '1',
        'SCT',
        str(bic or '').strip()[:11],
        beneficiary,
        iban,
    ]
    # Amount: samo EUR podržan u EPC069. Ako je 0 ili nedefinisan, ostavljamo prazno
    # → banka app tera korisnika da unese sam iznos.
    try:
        amt = float(amount_eur or 0)
        if amt > 0:
            lines.append(f'EUR{amt:.2f}')
        else:
            lines.append('')
    except (TypeError, ValueError):
        lines.append('')
    lines.append('')                                      # Purpose (opciono)
    lines.append(str(reference or '')[:35])               # Structured reference
    lines.append(str(remittance or '')[:140])             # Unstructured remittance
    # Total payload mora biti ≤ 331 bajt — trim ako je previše
    payload = '\n'.join(lines).encode('utf-8')
    if len(payload) > 331:
        # Skratimo remittance dok payload ne stane
        while len(payload) > 331 and lines[-1]:
            lines[-1] = lines[-1][:-5]
            payload = '\n'.join(lines).encode('utf-8')
    return payload.decode('utf-8')


def _build_epc_qr_drawing(beneficiary, iban, amount_eur, remittance='', bic='', reference='', size_mm=24):
    """Vraća ReportLab Drawing sa SEPA EPC QR kodom spreman za flow layout,
    ili None ako input nije dovoljan za validan EPC payload."""
    payload = _sepa_epc_payload(beneficiary, iban, amount_eur, remittance, bic, reference)
    if not payload:
        return None
    try:
        qr = QrCodeWidget(payload, barLevel='M')
        bounds = qr.getBounds()
        w = bounds[2] - bounds[0]
        h = bounds[3] - bounds[1]
        d = Drawing(size_mm * mm, size_mm * mm, transform=[
            (size_mm * mm) / w, 0, 0, (size_mm * mm) / h, 0, 0
        ])
        d.add(qr)
        return d
    except Exception:
        return None


def _bank_details_string(company, bank_idx=None):
    """Skuplja bankarske instrukcije iz podataka firme u jedan blok teksta.

    Novo (v2): ako je prosleđen bank_idx i company.bankAccounts[bank_idx] postoji,
    koristi TAJ specifičan bank račun (koji je admin izabrao pri kreiranju
    ponude preko dropdown-a "Buyer pays to"). Time se garantuje da klijent
    dobija tačne instrukcije za onaj cashflow račun koji admin očekuje da
    primi uplatu.

    Backward-compat: ako bankAccounts niz nije definisan ili bank_idx je None,
    koristi stara flat polja (bankName/accountNum/swift) da postojeće instance
    nastave da rade bez migracije podataka.
    """
    if not isinstance(company, dict):
        return ''

    # Prioritet 1: specifičan bank_idx iz bankAccounts niza (nova logika).
    # Ako je idx van opsega ili nevalidan, fallback na prvi (idx=0) umesto praznog stringa.
    bank_accounts = company.get('bankAccounts') or []
    if isinstance(bank_accounts, list) and bank_accounts:
        try:
            idx = int(bank_idx) if bank_idx is not None else 0
        except (TypeError, ValueError):
            idx = 0
        if idx < 0 or idx >= len(bank_accounts):
            idx = 0   # graceful fallback na primarnu banku
        b = bank_accounts[idx]
        if isinstance(b, dict):
            parts = []
            if b.get('bankName'):          parts.append(f"Bank: {b['bankName']}")
            if b.get('bankAddress'):       parts.append(str(b['bankAddress']))
            if b.get('accountNumber'):     parts.append(f"IBAN / Account: {b['accountNumber']}")
            if b.get('swiftCode'):         parts.append(f"SWIFT / BIC: {b['swiftCode']}")
            if b.get('correspondentBank'): parts.append(f"Correspondent bank: {b['correspondentBank']}")
            if b.get('currency'):          parts.append(f"Currency: {b['currency']}")
            if parts:
                return '\n'.join(parts)

    # Prioritet 2: legacy flat polja
    parts = []
    if company.get('bankName'):    parts.append(f"Bank: {company.get('bankName')}")
    if company.get('bankAddress'): parts.append(company.get('bankAddress'))
    if company.get('accountNum'):  parts.append(f"IBAN / Account: {company.get('accountNum')}")
    if company.get('swift'):       parts.append(f"SWIFT / BIC: {company.get('swift')}")
    if company.get('corrBank'):    parts.append(f"Correspondent bank: {company.get('corrBank')}")
    return '\n'.join(parts)


def _extract_bank_iban_bic(company, bank_idx=None):
    """Vraća (iban, bic) tuple iz izabranog banka računa; koristi za SEPA QR.
    Format IBAN se normalizuje (bez razmaka), BIC uppercase."""
    if not isinstance(company, dict):
        return (None, None)
    bank_accounts = company.get('bankAccounts') or []
    if isinstance(bank_accounts, list) and bank_accounts:
        try:
            idx = int(bank_idx) if bank_idx is not None else 0
        except (TypeError, ValueError):
            idx = 0
        if idx < 0 or idx >= len(bank_accounts): idx = 0
        b = bank_accounts[idx]
        if isinstance(b, dict):
            iban = str(b.get('accountNumber') or '').replace(' ', '').upper()
            bic = str(b.get('swiftCode') or '').replace(' ', '').upper()
            return (iban or None, bic or None)
    iban = str(company.get('accountNum') or '').replace(' ', '').upper()
    bic = str(company.get('swift') or '').replace(' ', '').upper()
    return (iban or None, bic or None)


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
    """Ucitava podatke firme i settings (last invoice num, VAT itd).

    V24.1 SUPABASE-ONLY: čita iz `settings` tabele preko
    `supabase_store.get_setting`. Vrednosti su Fernet-šifrovani TEXT,
    pa ih propuštamo kroz `decrypt_data` da dobijemo dict.
    """
    try:
        import supabase_store as _store
        comp_raw = _store.get_setting('company')
        gen_raw = _store.get_setting('settings')
    except Exception as e:
        logger.warning(f'_fetch_company_and_settings: store read failed: {e}')
        return {}, {}
    company = decrypt_data(comp_raw) if comp_raw else {}
    settings = decrypt_data(gen_raw) if gen_raw else {}
    return (company if isinstance(company, dict) else {},
            settings if isinstance(settings, dict) else {})


def _fetch_partner(partner_id):
    """V24.1 SUPABASE-ONLY: čita partnera preko `supabase_store.get_entity`.

    Vraća rehidriran dict (top-level Supabase kolone + JSONB `data` spojeni).
    Ako je iz nekog razloga vraćen string (legacy), propušta se kroz decrypt_data.
    """
    if not partner_id: return {}
    try:
        import supabase_store as _store
        p = _store.get_entity('partners', partner_id)
        if p is None: return {}
        if isinstance(p, dict): return p
        if isinstance(p, str):
            d = decrypt_data(p)
            return d if isinstance(d, dict) else {}
        return {}
    except Exception as e:
        logger.warning(f'_fetch_partner({partner_id}) failed: {e}')
        return {}


def _fetch_product(product_id):
    """V24.1 SUPABASE-ONLY: čita proizvod preko `supabase_store.get_entity`."""
    if not product_id: return {}
    try:
        import supabase_store as _store
        p = _store.get_entity('products', product_id)
        if p is None: return {}
        if isinstance(p, dict): return p
        if isinstance(p, str):
            d = decrypt_data(p)
            return d if isinstance(d, dict) else {}
        return {}
    except Exception as e:
        logger.warning(f'_fetch_product({product_id}) failed: {e}')
        return {}


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
    """Profesionalni tipografski sistem — svi paragrafi izlaze iz jedne palete.

    Skalno: h1 26pt > h2 15pt > h3 11pt > body 9.5pt > small 8pt.
    Boje: text-900 #101828 (naslov), text-700 #344054 (body),
          text-500 #667085 (helper), border #e7eaef, brand se prosleđuje kasnije.
    Font stack: Helvetica (ugrađen), koji je bezbedno serviran svuda bez
    dodatnih zavisnosti; naslovi su Helvetica-Bold.
    """
    styles = getSampleStyleSheet()
    return {
        'h1': ParagraphStyle('H1', parent=styles['Heading1'], fontName='Helvetica-Bold',
                             fontSize=26, textColor=colors.HexColor('#101828'),
                             leading=30, spaceAfter=2, leftIndent=0),
        'h2': ParagraphStyle('H2', parent=styles['Heading2'], fontName='Helvetica-Bold',
                             fontSize=15, textColor=colors.HexColor('#101828'),
                             leading=18, spaceAfter=6, spaceBefore=12),
        'h3': ParagraphStyle('H3', parent=styles['Heading3'], fontName='Helvetica-Bold',
                             fontSize=11, textColor=colors.HexColor('#101828'),
                             leading=14, spaceAfter=3, spaceBefore=6),
        'body': ParagraphStyle('Body', parent=styles['Normal'], fontName='Helvetica',
                               fontSize=9.5, textColor=colors.HexColor('#344054'), leading=13),
        'bodyStrong': ParagraphStyle('BodyStrong', parent=styles['Normal'], fontName='Helvetica-Bold',
                                     fontSize=9.5, textColor=colors.HexColor('#101828'), leading=13),
        'small': ParagraphStyle('Small', parent=styles['Normal'], fontName='Helvetica',
                                fontSize=8, textColor=colors.HexColor('#667085'), leading=10),
        'micro': ParagraphStyle('Micro', parent=styles['Normal'], fontName='Helvetica',
                                fontSize=6.5, textColor=colors.HexColor('#98a2b3'), leading=8.5),
        'label': ParagraphStyle('Label', parent=styles['Normal'], fontName='Helvetica-Bold',
                                fontSize=7, textColor=colors.HexColor('#667085'),
                                leading=10),
        'right': ParagraphStyle('Right', parent=styles['Normal'], fontName='Helvetica',
                                fontSize=9.5, textColor=colors.HexColor('#101828'),
                                alignment=TA_RIGHT, leading=13),
        'rightSmall': ParagraphStyle('RightSmall', parent=styles['Normal'], fontName='Helvetica',
                                     fontSize=8, textColor=colors.HexColor('#667085'),
                                     alignment=TA_RIGHT, leading=11),
        'center': ParagraphStyle('Center', parent=styles['Normal'], fontName='Helvetica',
                                 fontSize=9.5, alignment=TA_CENTER, leading=13),
    }


def _normalize_address(entity):
    """Vraća (street_line, city_country_line) iz partnera/kompanije bez obzira
    na to da li je address string ili dict. Ovim se izbegava AttributeError
    kroz ceo PDF pipeline."""
    if not isinstance(entity, dict):
        return '', ''
    addr = entity.get('address')
    if isinstance(addr, dict):
        street = str(addr.get('street', '') or '')
        city = str(addr.get('city', '') or entity.get('city', '') or '')
        country = str(addr.get('country', '') or entity.get('country', '') or '')
    else:
        street = str(addr or '')
        city = str(entity.get('city', '') or '')
        country = str(entity.get('country', '') or '')
    tail = ' '.join(filter(None, [city, country])).strip()
    return street, tail


def _normalize_contact(entity):
    """Vraća (email, phone, person) bez obzira na oblik contact polja."""
    if not isinstance(entity, dict):
        return '', '', ''
    c = entity.get('contact')
    c = c if isinstance(c, dict) else {}
    email = str(c.get('email', '') or entity.get('email', '') or '')
    phone = str(c.get('phone', '') or entity.get('phone', '') or '')
    person = str(c.get('person', '') or entity.get('contactPerson', '') or '')
    return email, phone, person


def _pdf_metadata(kind, doc_no, company, party_name=''):
    """Konzistentan set PDF metapodataka koji se native embed-uje u dokument.
    Sve moderne Windows/macOS/Linux PDF čitači pokazuju ove vrednosti u
    Properties dijalogu (File → Properties)."""
    company_name = str(company.get('name', 'Aspidus') if isinstance(company, dict) else 'Aspidus')
    kind_label = {'offer': 'Commercial Offer', 'invoice': 'Invoice',
                  'proforma': 'Proforma Invoice'}.get(kind.lower(), kind.title())
    title = f"{kind_label} {doc_no}".strip()
    subject = f"{kind_label} issued by {company_name}"
    if party_name:
        subject += f" for {party_name}"
    keywords = ', '.join(filter(None, [
        kind_label, company_name, party_name,
        str(doc_no or ''), 'Aspidus CRM', 'trade document'
    ]))
    return {
        'title': title,
        'author': company_name,
        'subject': subject,
        'keywords': keywords,
        'creator': f'{company_name} — Aspidus CRM',
        'producer': 'Aspidus CRM PDF Engine (ReportLab)',
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
    # Native PDF metapodaci — pokazuju se u File → Properties svakog PDF čitača.
    # NAPOMENA: SimpleDocTemplate ne prihvata `creator` ni `producer` direktno,
    # ali author/title/subject/keywords hvata; ostatak dodajemo posle build-a
    # kroz canvas metadata callback ako je potrebno.
    _meta = _pdf_metadata('offer', offer.get('offerNo', ''), company,
                          party_name=(buyer.get('companyName') or buyer.get('name') or ''))
    # V23.1 — human-readable label used by header + footer callback
    kind_label = 'Commercial Offer'
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        # V23.1: uvecani margini da footer/header ne udara u sadrzaj:
        #  top    28mm — daje 12mm za logo+firma header + 5mm gap
        #  bottom 28mm — daje 20mm za QR + address + page num + confidentiality
        topMargin=28*mm, bottomMargin=28*mm,
        title=_meta['title'],
        author=_meta['author'],
        subject=_meta['subject'],
        keywords=_meta['keywords'],
        creator=_meta['creator'],
        # invariant=True čini output DETERMINISTIC — nema random /ID u trailer-u,
        # nema wall-clock timestamp-a. Bez ovoga SHA-256 istog PDF-a se menja pri
        # svakom generisanju, što lomi integrity verification workflow.
        invariant=True,
    )

    story = []

    # V23.1 — HEADER JE VEC NA CANVASU (svaka stranica). Ovde samo naslov
    # dokumenta i osnovni info (Valid until, Currency) da se ne duplira firm ime.
    story.append(_para(f"<b>{kind_label.upper()}</b> · No. <b>{_esc(offer.get('offerNo', ''))}</b>", styles['h1']))
    story.append(_para(
        f"<font color='#475569'>Date: {_esc((offer.get('date') or '')[:10])} "
        f"&nbsp;·&nbsp; Valid until: <b>{_esc(offer.get('validUntil') or 'N/A')}</b> "
        f"&nbsp;·&nbsp; Currency: <b>{_esc(offer.get('currency') or 'EUR')}</b></font>",
        styles['small']))
    story.append(Spacer(1, 5*mm))

    # PARTIES: FROM / TO — koristi normalizatore da su string i dict oblici
    # partner.address / partner.contact bezbedno pokriveni.
    buyer_street, buyer_geo = _normalize_address(buyer)
    buyer_email, buyer_phone, buyer_person = _normalize_contact(buyer)
    to_lines = [
        f"<b>{_esc(buyer.get('companyName') or buyer.get('name') or 'Buyer')}</b>",
        _esc(buyer_person),
        _esc(buyer_street),
        _esc(buyer_geo),
        f"Tax ID: {_esc(buyer.get('taxId', ''))}" if buyer.get('taxId') else '',
        f"Email: {_esc(buyer_email)}" if buyer_email else '',
        f"Phone: {_esc(buyer_phone)}" if buyer_phone else '',
    ]
    company_street, company_geo = _normalize_address(company)
    from_lines = [
        f"<b>{_esc(company.get('name', ''))}</b>",
        _esc(company_street),
        _esc(company_geo),
        f"Tax ID: {_esc(company.get('taxId', ''))}" if company.get('taxId') else '',
        f"Reg. No.: {_esc(company.get('regNumber', ''))}" if company.get('regNumber') else '',
        f"VAT: {_esc(company.get('vatNumber', ''))}" if company.get('vatNumber') else '',
    ]
    parties = Table([
        [_para("FROM (SELLER)", styles['label']), _para("TO (BUYER)", styles['label'])],
        [_para("<br/>".join([l for l in from_lines if l]), styles['body']),
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
    # Prioritet:
    #  1. offer.bankDetails (custom admin string — override svega)
    #  2. Ako offer ima paymentBankIdx, koristi taj specifičan račun iz
    #     company.bankAccounts[idx] (izbor admin-a preko dropdown-a).
    #  3. Fallback na primarnu banku firme (bankAccounts[0] ili legacy flat polja).
    # Time je garantovano da ono što admin izabere u UI-u tačno stigne do klijenta.
    bank_details = offer.get('bankDetails') or _bank_details_string(company, offer.get('paymentBankIdx'))
    if bank_details:
        story.append(_para("<b>Bank Instructions</b>", styles['h2']))
        iban, bic = _extract_bank_iban_bic(company, offer.get('paymentBankIdx'))
        qr_drawing = None
        # Za offer/invoice: fetch total i currency
        total_val = offer.get('grandTotal') or offer.get('totalWithTax') or offer.get('total') or 0
        currency = str(offer.get('currency') or 'USD').upper()
        if iban and currency == 'EUR':
            beneficiary = company.get('name', '') or 'Company'
            remit = f"Offer {offer.get('offerNo', '')}" if offer.get('offerNo') else ''
            qr_drawing = _build_epc_qr_drawing(
                beneficiary=beneficiary,
                iban=iban,
                amount_eur=total_val,
                remittance=remit[:140],
                bic=bic or '',
                size_mm=26,
            )
        if qr_drawing is not None:
            # Split u 2 kolone: bank text levo, QR desno sa 'Scan to Pay' label
            qr_label = _para(
                "<b><font size='8' color='#065f46'>📱 SEPA Scan to Pay</font></b><br/>"
                "<font size='7' color='#6b7280'>Open your banking app and scan this QR to auto-fill the transfer.</font>",
                styles['center']
            )
            bank_tbl = Table(
                [[_para(_esc(bank_details), styles['body']), [qr_drawing, qr_label]]],
                colWidths=[140*mm, 40*mm]
            )
            bank_tbl.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f4f6f9')),
                ('BOX', (0,0), (-1,-1), 0.4, colors.HexColor('#e7eaef')),
                ('LINEBEFORE', (1,0), (1,-1), 0.4, colors.HexColor('#e7eaef')),
                ('LEFTPADDING', (0,0), (-1,-1), 10),
                ('RIGHTPADDING', (0,0), (-1,-1), 10),
                ('TOPPADDING', (0,0), (-1,-1), 8),
                ('BOTTOMPADDING', (0,0), (-1,-1), 8),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('ALIGN', (1,0), (1,-1), 'CENTER'),
            ]))
        else:
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
    company_addr_str = ((company_street + (', ' + company_geo if company_geo else ''))
                        if (company_street or company_geo) else str(company.get('address') or ''))

    def _apply_native_metadata(canvas):
        """Ubacuje `Producer` i osigurava ostale metapodatke direktno u PDF
        catalog. SimpleDocTemplate ne prihvata `producer` kao kwarg, pa se
        ova vrednost mora setovati preko canvas API-ja pre saveState-a."""
        try:
            canvas.setTitle(_meta['title'])
            canvas.setAuthor(_meta['author'])
            canvas.setSubject(_meta['subject'])
            canvas.setKeywords(_meta['keywords'])
            canvas.setCreator(_meta['creator'])
            # Producer polje se u reportlab-u postavlja preko _doc.info.producer
            if hasattr(canvas, '_doc') and hasattr(canvas._doc, 'info'):
                canvas._doc.info.producer = _meta['producer']
        except Exception:
            logger.debug('PDF metadata: setter failed', exc_info=True)

    # V23.1 — UNIFIKOVAN header + footer na SVAKOJ stranici. Verifikacioni hash
    # ide u PDF metadata (setSubject/setKeywords), ne kao veliki tekst.
    # Logo (iz company.logoDataUrl / logoUrl) + naziv firme na vrhu.
    # QR na dnu LEVO da ne udara u broj strane koji je DESNO.
    company_website = str(company.get('website') or '')
    company_tax_id  = str(company.get('taxId') or '')
    company_logo    = company.get('logoDataUrl') or company.get('logoUrl') or ''

    # Pripremi keywords sa VER hash-om (metadata only — searchable ali ne velik na stranici)
    _meta['keywords'] = (_meta.get('keywords') or '') + f' verification:{ver_hash}'
    _meta['subject']  = f"{_meta.get('subject','')} · {ver_hash}"

    def _decode_logo(canvas, x, y, max_h=12*mm):
        """Pokusa da nacrta logo. Vraca stvarnu sirinu koju je logo zauzeo, u mm.
        Prihvata data:image/png;base64,… ili public URL."""
        try:
            from reportlab.lib.utils import ImageReader
            import base64, io
            src = company_logo.strip()
            if not src:
                return 0
            img_data = None
            if src.startswith('data:'):
                # data:image/png;base64,....
                comma = src.find(',')
                if comma > 0:
                    img_data = base64.b64decode(src[comma+1:])
            elif src.startswith(('http://', 'https://')):
                import urllib.request
                with urllib.request.urlopen(src, timeout=3) as resp:
                    img_data = resp.read()
            if not img_data:
                return 0
            img = ImageReader(io.BytesIO(img_data))
            iw, ih = img.getSize()
            if ih == 0:
                return 0
            ratio = iw / ih
            draw_h = max_h
            draw_w = draw_h * ratio
            # Ograniceno na max 40mm sirine
            if draw_w > 40*mm:
                draw_w = 40*mm
                draw_h = draw_w / ratio
            canvas.drawImage(img, x, y, width=draw_w, height=draw_h,
                             mask='auto', preserveAspectRatio=True)
            return draw_w
        except Exception:
            logger.debug('logo render failed', exc_info=True)
            return 0

    def _page_header(canvas):
        """Header na SVAKOJ stranici: logo (levo) + naziv firme + Doc # (desno)."""
        canvas.saveState()
        # Blago obojena traka header-a
        # canvas.setFillColor(colors.HexColor('#f8fafc'))
        # canvas.rect(0, A4[1] - 26*mm, A4[0], 26*mm, fill=1, stroke=0)

        # Logo (top-left)
        logo_w = _decode_logo(canvas, 15*mm, A4[1] - 20*mm, max_h=12*mm)

        # Company name (levo, posle logoa) + slogan
        text_x = 15*mm + logo_w + (3*mm if logo_w else 0)
        canvas.setFont('Helvetica-Bold', 11)
        canvas.setFillColor(colors.HexColor('#101828'))
        canvas.drawString(text_x, A4[1] - 12*mm, str(company.get('name', 'Aspidus')))
        if company_website:
            canvas.setFont('Helvetica', 7)
            canvas.setFillColor(colors.HexColor('#667085'))
            canvas.drawString(text_x, A4[1] - 17*mm, company_website)

        # Doc info (desno)
        canvas.setFont('Helvetica-Bold', 9)
        canvas.setFillColor(colors.HexColor('#4f46e5'))
        canvas.drawRightString(A4[0] - 15*mm, A4[1] - 12*mm,
                               f"{kind_label} · {offer.get('offerNo', '')}")
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#667085'))
        canvas.drawRightString(A4[0] - 15*mm, A4[1] - 17*mm,
                               f"Date: {(offer.get('date') or '')[:10]}")

        # Hairline ispod header-a
        canvas.setStrokeColor(colors.HexColor('#e5e7eb'))
        canvas.setLineWidth(0.5)
        canvas.line(15*mm, A4[1] - 22*mm, A4[0] - 15*mm, A4[1] - 22*mm)
        canvas.restoreState()

    def _footer(canvas, docObj):
        _apply_native_metadata(canvas)
        _page_header(canvas)  # header ide na svakoj stranici
        canvas.saveState()

        # Hairline iznad footer-a
        canvas.setStrokeColor(colors.HexColor('#e5e7eb'))
        canvas.setLineWidth(0.5)
        canvas.line(15*mm, 24*mm, A4[0] - 15*mm, 24*mm)

        # QR — bottom LEFT (nekad je bio desno, sudarao se sa page N). Sada
        # ide u donji-levi ugao, page number ostaje donji-desni.
        try:
            verify_host = str(company.get('publicHost') or '').rstrip('/')
            if not verify_host:
                try:
                    from flask import request as _flask_req, has_request_context
                    if has_request_context():
                        verify_host = _flask_req.host_url.rstrip('/')
                except Exception:
                    pass
            if not verify_host:
                verify_host = str(company.get('portalHost') or 'https://aspidus.io').rstrip('/')
            verify_url = f'{verify_host}/verify/{ver_hash}'
            qr = QrCodeWidget(verify_url, barLevel='L')
            b = qr.getBounds()
            w, h = b[2] - b[0], b[3] - b[1]
            size = 14 * mm
            d = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
            d.add(qr)
            from reportlab.graphics import renderPDF
            renderPDF.draw(d, canvas, 15*mm, 6*mm)
            canvas.setFont('Helvetica', 5.5)
            canvas.setFillColor(colors.HexColor('#98a2b3'))
            canvas.drawString(15*mm + size + 2*mm, 9*mm, "Verify authenticity")
            canvas.drawString(15*mm + size + 2*mm, 6.5*mm, "Scan the QR →")
        except Exception:
            logger.debug('verify QR failed', exc_info=True)

        # Company memorandum blok — centrirano
        canvas.setFont('Helvetica-Bold', 7.5)
        canvas.setFillColor(colors.HexColor('#101828'))
        canvas.drawCentredString(A4[0]/2, 20*mm, str(company.get('name', 'Aspidus')))
        canvas.setFont('Helvetica', 6.5)
        canvas.setFillColor(colors.HexColor('#475569'))
        _addr_line = company_addr_str
        if _addr_line:
            canvas.drawCentredString(A4[0]/2, 16.5*mm, _addr_line)
        _meta_line_bits = []
        if company_website: _meta_line_bits.append(company_website)
        if company_tax_id:  _meta_line_bits.append(f"Tax ID: {company_tax_id}")
        email_l = str(company.get('email') or '')
        phone_l = str(company.get('phone') or '')
        if email_l: _meta_line_bits.append(email_l)
        if phone_l: _meta_line_bits.append(phone_l)
        if _meta_line_bits:
            canvas.drawCentredString(A4[0]/2, 13*mm, '  ·  '.join(_meta_line_bits))

        # Confidentiality (sitno na dnu)
        canvas.setFont('Helvetica-Oblique', 5.5)
        canvas.setFillColor(colors.HexColor('#98a2b3'))
        canvas.drawCentredString(A4[0]/2, 4*mm,
            "Confidential — electronically generated document. Verification hash embedded in PDF metadata.")

        # Page N — desno dole (jasno odvojen od QR koji je levo)
        canvas.setFont('Helvetica-Bold', 8)
        canvas.setFillColor(colors.HexColor('#475569'))
        canvas.drawRightString(A4[0] - 15*mm, 9*mm,
                               f"Page {canvas.getPageNumber()}")
        # Issue timestamp — deterministic (odgovara offer.date, ne datetime.now())
        stamp = (offer.get('date') or offer.get('createdAt') or '')[:16]
        canvas.setFont('Helvetica', 6)
        canvas.setFillColor(colors.HexColor('#98a2b3'))
        canvas.drawRightString(A4[0] - 15*mm, 6.5*mm, f"Issued: {stamp}" if stamp else "")

        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def _sha256_bytes(data):
    """SHA-256 nad proizvoljnim bajtovima, hex encoded u uppercase."""
    return hashlib.sha256(data).hexdigest().upper()


def save_offer_pdf_to_vault(offer):
    """Snima referencu na dokument u shared_documents + BINDING HASH nad
    kanonizovanim podacima ponude.

    Binding hash je SHA-256 nad JSON snapshot-om ključnih polja koja
    definišu tekst dokumenta: cena, količine, kupac, uslovi plaćanja,
    incoterm, banke, itd. Ovim se garantuje kriptografska veza:
      * ako klijent kasnije pošalje potpisan PDF, mi ga generišemo iz iste
        ponude i uporedimo binding hash → identičan = sadržaj netaknut;
      * pored toga sam PDF fajl dobija svoj content hash (SHA-256 nad
        bajtovima) koji se upisuje u shared_documents.pdfContentHash pri
        prvom generisanju; svaka izmena PDF-a mesto tog hash-a više se
        neće poklopiti.

    Vraca (doc_id, None)."""
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    # 1) Generiši PDF u memoriji i izračunaj content hash
    try:
        pdf_bytes = build_offer_pdf(offer)
        content_hash = _sha256_bytes(pdf_bytes)
    except Exception as e:
        logger.error(f"Failed to build PDF for hash: {e}", exc_info=True)
        content_hash = None

    # 2) Kanonizovan snapshot za binding hash — samo POLJA KOJA IMAJU
    #    TEKSTUALNU REFLEKSIJU U DOKUMENTU. Redosled je fiksan.
    canonical = {
        'offerId': offer.get('id'),
        'offerNo': offer.get('offerNo'),
        'date': offer.get('date'),
        'validUntil': offer.get('validUntil'),
        'customerId': offer.get('customerId'),
        'productId': offer.get('productId'),
        'quantity': offer.get('quantity'),
        'unit': offer.get('unit'),
        'sellingPrice': offer.get('sellingPrice') or offer.get('price'),
        'currency': offer.get('currency'),
        'incoterm': offer.get('incoterm'),
        'paymentTerms': offer.get('paymentTerms'),
        'pol': offer.get('pol'), 'pod': offer.get('pod'),
        'items': offer.get('items') or [],
        'paymentBankIdx': offer.get('paymentBankIdx'),
    }
    binding_seed = json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode('utf-8')
    binding_hash = _sha256_bytes(binding_seed)

    # Rezerviši ili preuzimi postojeći broj iz document_register-a. Ovo garantuje
    # da nijedan dokument ne zaobiđe sekvencijalnu numeraciju, čak i ako je
    # offer.offerNo bio postavljen ranije iz drugog izvora.
    #
    # V24.1 SUPABASE-ONLY: sav pristup ide preko `data_layer` sa snake_case
    # kolonama (doc_type, year, seq, doc_number, entity_id, revision, …).
    # UNIQUE(doc_type, year, seq) constraint (definisan u supabase_schema.sql)
    # hvata race condition — ako dva procesa istovremeno pokušaju da rezervišu
    # isti seq, drugi dobija Postgres unique_violation (23505) i fallback-uje
    # na lookup po entity_id.
    doc_register_number = None
    doc_register_revision = 0
    try:
        from data_layer import select_one as _dl_select_one, select as _dl_select, insert as _dl_insert
        # 1) Postojeća rezervacija (revision=0) za ovu ponudu?
        existing = _dl_select_one(
            'document_register',
            {'doc_type': ('eq', 'offer'),
             'entity_id': ('eq', offer.get('id')),
             'revision': ('eq', 0)},
            columns='doc_number,revision',
        )
        if existing:
            doc_register_number = existing.get('doc_number')
            doc_register_revision = int(existing.get('revision') or 0)
        else:
            # 2) Atomski rezerviši novi seq — `SELECT MAX(seq)` pa INSERT.
            # Postoje mala race: ako između SELECT-a i INSERT-a drugi proces
            # uzme isti seq, INSERT pukne na UNIQUE(doc_type, year, seq) i
            # fallbackujemo na lookup po entity_id (koji je sigurno kreiran).
            year = datetime.now(timezone.utc).year
            seq = 1
            try:
                max_rows = _dl_select(
                    'document_register',
                    filters={'doc_type': ('eq', 'offer'),
                             'year': ('eq', year)},
                    order='-seq', limit=1,
                ) or []
                if max_rows:
                    seq = int(max_rows[0].get('seq') or 0) + 1
            except Exception:
                pass
            doc_register_number = f"OFF-{seq:03d}/{year}"
            new_doc_id = f"dreg_{uuid.uuid4().hex[:12]}"
            try:
                _dl_insert('document_register', {
                    'id': new_doc_id,
                    'doc_type': 'offer',
                    'year': year,
                    'seq': seq,
                    'doc_number': doc_register_number,
                    'entity_id': offer.get('id'),
                    'revision': 0,
                    'issued_at': now,
                    'issued_by': 'system',
                })
            except Exception as ie:
                # 23505 = postgres unique_violation na (doc_type, year, seq).
                # Drugi proces je rezervisao taj seq — vraćamo se na lookup.
                msg = str(ie).lower()
                if '23505' in msg or 'duplicate' in msg or 'unique' in msg:
                    fb = _dl_select_one(
                        'document_register',
                        {'doc_type': ('eq', 'offer'),
                         'entity_id': ('eq', offer.get('id'))},
                        columns='doc_number',
                    )
                    if fb:
                        doc_register_number = fb.get('doc_number')
                else:
                    raise
    except Exception as e:
        logger.warning(f'document_register reserve failed: {e}')

    doc = {
        'id': doc_id,
        'partnerId': offer.get('customerId'),
        'productId': offer.get('productId'),
        'docType': 'OFFER',
        'fileName': f"Offer_{offer.get('offerNo', '')}.pdf",
        'sourceOfferId': offer.get('id'),
        'sourceType': 'OFFER',
        'createdAt': now,
        # Registar polja — koji je oficijelni broj i revizija
        'registerNumber': doc_register_number,
        'registerRevision': doc_register_revision,
        # Integrity fields
        'pdfContentHash': content_hash,
        'bindingHash': binding_hash,
        'shortVerification': _make_verification_hash(offer.get('id'), offer.get('offerNo')),
        'hashAlgorithm': 'SHA-256',
    }
    try:
        # V24.1 SUPABASE-ONLY: shared_documents čuvamo preko `data_layer.insert`.
        # `partner_id` je top-level kolona (vidi SUPPORTED_TABLES u supabase_merge.py);
        # sve ostale JSON polje (docType, fileName, productId, …) idu u JSONB `data`.
        from data_layer import insert as _dl_insert
        _dl_insert('shared_documents', {
            'id': doc_id,
            'partner_id': offer.get('customerId'),
            'data': doc,
        })
    except Exception as e:
        logger.error(f"Failed to save PDF vault entry: {e}")
        return None, None

    return doc_id, None


def regenerate_offer_pdf_by_id(offer_id):
    """Ponovo pravi PDF ponude iz aktuelnih podataka u bazi. Vraća bytes ili
    None ako ponuda ne postoji.

    V24.1 SUPABASE-ONLY: čita ponudu preko `supabase_store.get_entity('offers', id)`
    što vraća rehidriran dict (top-level kolone + JSONB `data` spojeni).
    Defanzivno propušta string kroz `decrypt_data` za legacy redove koji su
    možda ostali iz pre-Supabase ere (Fernet ciphertext sačuvan kao JSON string).
    """
    if not offer_id:
        return None
    try:
        import supabase_store as _store
        offer_data = _store.get_entity('offers', offer_id)
        if offer_data is None:
            return None
        if isinstance(offer_data, str):
            # Legacy fallback — ne treba u novom toku, ali ne skida se da
            # nikad ne pukne na importu starih redova iz v23.0 migracije.
            offer_data = decrypt_data(offer_data)
        if not isinstance(offer_data, dict):
            return None
        return build_offer_pdf(offer_data)
    except Exception as e:
        logger.error(f"Failed to regenerate PDF for offer {offer_id}: {e}")
        return None
