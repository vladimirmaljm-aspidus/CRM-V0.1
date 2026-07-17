import sqlite3
import json
from flask import request, jsonify, abort, render_template
from config import DB_FILE, PORTAL_DB_FILE
from utils import decrypt_data, log_audit
from . import (portal_bp, safe_parse, check_portal_rate_limit,
               verify_portal_session, find_partner_by_token, log_portal_activity)

@portal_bp.route('/portal/login', methods=['GET'])
@portal_bp.route('/portal/', methods=['GET'])
def portal_login_page():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip: ip = ip.split(',')[0].strip()
    if not check_portal_rate_limit(ip):
        abort(429, description="DDoS Protection: Rate limit exceeded.")
    conn = None
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key='company'")
        row = c.fetchone()
        company = decrypt_data(row[0]) if row else {}
        if not isinstance(company, dict): company = {}
    finally:
        if conn: conn.close()
    return render_template('portal_login.html',
                           company_name=company.get('name', 'Aspidus'),
                           company_logo=company.get('logoUrl') or company.get('logoDataUrl', ''),
                           brand_color=company.get('brandColor', '#2563eb'))


@portal_bp.route('/portal/<token>', methods=['GET'])
def view_portal(token):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip: ip = ip.split(',')[0].strip()
    if not check_portal_rate_limit(ip):
        abort(429, description="DDoS Protection: Rate limit exceeded.")
    return render_template('portal.html', token=token)

@portal_bp.route('/api/portal/data/<token>', methods=['GET'])
def get_portal_data(token):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip: ip = ip.split(',')[0].strip()
    if not check_portal_rate_limit(ip): abort(429)
    
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_session(token, auth_header):
        return jsonify({"error": "Authentication required", "require_otp": True}), 401

    conn = None
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        conn.execute('PRAGMA journal_mode=WAL;')
        c = conn.cursor()

        # find_partner_by_token sprovodi Kill Switch (opozvan portal -> None)
        partner_id, partner = find_partner_by_token(c, token, enforce_active=True)
        if not partner:
            log_audit('SECURITY', 'portal', f'Blocked data access (invalid or revoked portal token)', is_suspicious=True)
            return jsonify({"error": "Access Denied"}), 403

        # SYSTEM PERMISSIONS: Dozvoljavamo pristup i novom tabu za dokumente
        permissions = partner.get("portalPermissions", ["shipments", "offers", "kyc", "goods", "profile", "rfq", "documents"])
        
        # Odbrambeni normalizatori: 'address' i 'contact' su nekad string, nekad dict.
        # Bez ovoga, staro pretpostavljanje partner['address'].get(...) baca
        # AttributeError kada je vrednost string, što je uzrok 500 na /api/portal/data.
        _addr = partner.get("address")
        _contact = partner.get("contact")
        _kyc = partner.get("kyc")
        _addr_dict = _addr if isinstance(_addr, dict) else {}
        _addr_str = _addr if isinstance(_addr, str) else _addr_dict.get("street", "")
        _contact_dict = _contact if isinstance(_contact, dict) else {}
        _kyc_dict = _kyc if isinstance(_kyc, dict) else {}

        safe_partner = {
            "id": partner_id,
            "companyName": partner.get("companyName") or partner.get("name") or "",
            "contactPerson": _contact_dict.get("person", "") or partner.get("contactPerson", ""),
            "kycStatus": _kyc_dict.get("status", "pending"),
            "kycReviewNote": _kyc_dict.get("reviewNote", ""),
            "kycReviewedAt": _kyc_dict.get("reviewedAt", ""),
            "email": _contact_dict.get("email") or partner.get("email", "") or "",
            "phone": _contact_dict.get("phone") or partner.get("phone", "") or "",
            "address": {
                "street": _addr_str,
                "city": _addr_dict.get("city", "") or partner.get("city", "") or "",
                "country": _addr_dict.get("country", "") or partner.get("country", "") or "",
            },
            "permissions": permissions
        }
        
        c.execute("SELECT data FROM products")
        products_map = {safe_parse(r[0])['id']: safe_parse(r[0])['name'] for r in c.fetchall()}
        
        c.execute("SELECT value FROM settings WHERE key='company'")
        comp_row = c.fetchone()
        
        company_data = decrypt_data(comp_row[0]) if comp_row else {}
        company = company_data if isinstance(company_data, dict) else {}
        safe_company = {
            "name": company.get("name", "Aspidus"),
            # Frontend CRM cuva logo u logoDataUrl (settings modal); podržavamo oba naziva.
            "logoUrl": company.get("logoUrl") or company.get("logoDataUrl") or "",
            "brandColor": company.get("brandColor", "#2563eb"),
            "address": company.get("address", ""),
            "taxId": company.get("taxId", ""),
            "website": company.get("website", "")
        }
        
        c.execute("SELECT data FROM deals")
        safe_deals = []
        for d_row in c.fetchall():
            deal = safe_parse(d_row[0])
            # Podržavamo i staro ime polja (customerId) i novo (buyerId) zbog kompatibilnosti
            if deal.get('buyerId') == partner_id or deal.get('customerId') == partner_id:
                safe_deals.append({
                    "id": deal.get("id"),
                    "contractId": deal.get("contractId"), 
                    "productName": products_map.get(deal.get("productId"), "Commodity"),
                    "quantity": deal.get("quantity"), 
                    "unit": deal.get("unit"), 
                    "status": deal.get("status"), 
                    "createdAt": deal.get("createdAt"),
                    "logistics": {
                        "pol": deal.get("logistics", {}).get("pol", "TBA"), 
                        "pod": deal.get("logistics", {}).get("pod", "TBA"),
                        "vessel": deal.get("logistics", {}).get("vessel", "TBA"), 
                        "blNumber": deal.get("logistics", {}).get("blNumber", "TBA"),
                        "shipmentDate": deal.get("logistics", {}).get("shipmentDate", "TBA")
                    }
                })
            
        # Ucitaj pun katalog proizvoda za enrichment ponude detaljima (HS code, spec, pakovanje...)
        c.execute("SELECT data FROM products")
        products_full_map = {}
        for pr in c.fetchall():
            pd = safe_parse(pr[0])
            if isinstance(pd, dict) and pd.get('id'):
                products_full_map[pd['id']] = pd

        c.execute("SELECT data FROM offers")
        safe_offers = []
        for o_row in c.fetchall():
            off = safe_parse(o_row[0])
            if off.get('customerId') == partner_id:
                # Enrichment: iz proizvoda ubaci HS code, spec, origin - klijent vidi pun opis ponude.
                pid = off.get("productId")
                prod = products_full_map.get(pid, {}) if pid else {}
                supply = (prod.get('supplyOffers') or [{}])[0] if prod else {}
                # Ako je konvertovana ili klijent vec odgovorio, nose relevantne meta.
                safe_offers.append({
                    "id": off.get("id"),
                    "offerNo": off.get("offerNo"),
                    "date": off.get("date"),
                    "validUntil": off.get("validUntil"),
                    "productName": products_map.get(pid, "Commodity"),
                    "quantity": off.get("quantity"),
                    "unit": off.get("unit"),
                    "price": off.get("sellingPrice") or off.get("price"),
                    "currency": off.get("currency"),
                    "incoterm": off.get("incoterm"),
                    "hsCode": off.get("hsCode") or prod.get('hsCode'),
                    "productSpec": prod.get('detailedSpec') or prod.get('shortDescription'),
                    "detailedSpec": off.get('detailedSpec') or prod.get('detailedSpec'),
                    "packaging": off.get("packaging") or prod.get('packaging'),
                    "paymentTerms": off.get("paymentTerms"),
                    "pol": off.get("pol"), "pod": off.get("pod"),
                    "vessel": off.get("vessel"),
                    "leadTime": off.get("leadTime") or supply.get('leadTime') if supply else None,
                    "origin": off.get("origin") or supply.get('country') if supply else None,
                    "productOrigin": supply.get('country') if supply else None,
                    "notes": off.get("notes"),
                    "documentId": off.get("documentId") or off.get("pdfDocumentId"),
                    "clientStatus": off.get("clientStatus"),
                    "clientAcceptedAt": off.get("clientAcceptedAt"),
                    "convertedDealId": off.get("convertedDealId")
                })
                
        c.execute("SELECT data FROM demands")
        my_demands = []
        for dem_row in c.fetchall():
            dem = safe_parse(dem_row[0])
            if dem.get('customerId') == partner_id:
                my_demands.append({
                    "id": dem.get("id"), 
                    "productName": dem.get("productName", "Commodity"),
                    "quantity": dem.get("quantity"), 
                    "targetPrice": dem.get("targetPrice"), 
                    "status": dem.get("status", "pending"), 
                    "date": dem.get("date")
                })

        # =========================================================
        # FAZA 2: INTEGRACIJA TREZORA DOKUMENATA (DOCUMENT VAULT)
        # =========================================================
        c.execute("SELECT data FROM shared_documents")
        documents = []
        for doc_row in c.fetchall():
            d = safe_parse(doc_row[0])
            if d.get('partnerId') == partner_id:
                documents.append(d)
        documents.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
        
        conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
        cp = conn_p.cursor()
        
        cp.execute("SELECT data FROM kyc_submissions WHERE partner_id=? ORDER BY submitted_at DESC LIMIT 1", (partner_id,))
        kyc_row = cp.fetchone()
        latest_kyc = decrypt_data(kyc_row[0]) if kyc_row else None
        
        cp.execute("SELECT id, data, status, created_at FROM portal_products WHERE partner_id=? ORDER BY created_at DESC", (partner_id,))
        my_products = [{"id": p_row[0], "data": safe_parse(p_row[1]), "status": p_row[2], "created_at": p_row[3]} for p_row in cp.fetchall()]

        # Moji zahtevi za izmenu profila
        my_profile_requests = []
        try:
            cp.execute("SELECT id, data, status, submitted_at, reviewed_at FROM profile_change_requests WHERE partner_id=? ORDER BY submitted_at DESC LIMIT 10", (partner_id,))
            my_profile_requests = [
                {"id": r[0], "changes": safe_parse(r[1]), "status": r[2], "submitted_at": r[3], "reviewed_at": r[4]}
                for r in cp.fetchall()
            ]
        except Exception:
            pass

        conn_p.close()

        # Meta o promenama od klijentovog poslednjeg pogleda (za badge "novo").
        # Klijent u browseru pamti last_seen; server samo vraća sirove timestamp-ove.
        return jsonify({
            "partner": safe_partner,
            "company": safe_company,
            "deals": sorted(safe_deals, key=lambda x: x.get('createdAt') or '', reverse=True),
            "offers": sorted(safe_offers, key=lambda x: x.get('date') or '', reverse=True),
            "my_demands": sorted(my_demands, key=lambda x: x.get('date') or '', reverse=True),
            "documents": documents,
            "latest_kyc": latest_kyc,
            "my_products": my_products,
            "my_profile_requests": my_profile_requests
        })
    finally:
        if conn: conn.close()

import re as _re
import uuid as _uuid
from datetime import datetime as _dt, timezone as _tz

_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

@portal_bp.route('/api/portal/profile/update/<token>', methods=['POST'])
def submit_profile_change_request(token):
    """Klijent portala predlaže izmenu svojih kontakt podataka (email, telefon,
    kontakt osoba, adresa). Izmena se NE primenjuje odmah — čuva se kao 'pending'
    zahtev; admin je odobrava iz CRM-a, tek onda se preslikava u partner profil.
    Time se sprečava da klijent bez nadzora promeni komunikacione kanale."""
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_session(token, auth_header): abort(401)

    payload = request.get_json(silent=True) or {}

    # Sanitizacija i validacija — samo dozvoljena polja
    email = str(payload.get('email', '')).strip()[:150]
    phone = str(payload.get('phone', '')).strip()[:50]
    person = str(payload.get('contactPerson', '')).strip()[:150]
    street = str(payload.get('street', '')).strip()[:200]
    city = str(payload.get('city', '')).strip()[:100]
    country = str(payload.get('country', '')).strip()[:100]
    note = str(payload.get('note', '')).strip()[:500]

    if email and not _EMAIL_RE.match(email):
        return jsonify({"error": "INVALID_EMAIL_FORMAT"}), 400
    if not any([email, phone, person, street, city, country]):
        return jsonify({"error": "NO_CHANGES_PROVIDED"}), 400

    conn = None
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        c = conn.cursor()
        partner_id, partner_record = find_partner_by_token(c, token, enforce_active=True)
        if not partner_record:
            return jsonify({"error": "Partner not found"}), 404
    finally:
        if conn: conn.close()

    changes = {}
    if email: changes['email'] = email
    if phone: changes['phone'] = phone
    if person: changes['contactPerson'] = person
    if street: changes['street'] = street
    if city: changes['city'] = city
    if country: changes['country'] = country
    if note: changes['note'] = note

    req_id = _uuid.uuid4().hex
    now_iso = _dt.now(_tz.utc).isoformat().replace('+00:00', 'Z')

    conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
    try:
        conn_p.execute('PRAGMA journal_mode=WAL;')
        conn_p.execute(
            "INSERT INTO profile_change_requests (id, partner_id, data, status, submitted_at) VALUES (?, ?, ?, ?, ?)",
            (req_id, partner_id, json.dumps(changes), 'pending', now_iso)
        )
        conn_p.commit()
    finally:
        conn_p.close()

    log_audit('CREATE', 'portal', f'Profile change request submitted by partner {partner_id}: {list(changes.keys())}', is_suspicious=False)
    return jsonify({"status": "success", "message": "Zahtev je poslat administratoru na odobrenje.", "request_id": req_id})