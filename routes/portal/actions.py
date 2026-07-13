import os
import sqlite3
import json
import secrets
import uuid
from datetime import datetime, timezone
from werkzeug.utils import secure_filename
from flask import request, jsonify, abort, send_from_directory, current_app, session
from config import DB_FILE, PORTAL_DB_FILE, PORTAL_UPLOAD_FOLDER, ALLOWED_EXTENSIONS
from utils import log_audit, login_required, encrypt_data, decrypt_data, is_safe_file_content
from . import (portal_bp, safe_parse, verify_portal_session, find_partner_by_token)


def require_portal_admin():
    """ISPRAVKA: admin rute za B2B portal (pregled/odobravanje KYC dokumenata sa
    bankovnim podacima, direktorima, UBO-ima, i odobravanje proizvoda partnera) su
    ranije imale SAMO @login_required, bez provere role ili permisije. Bilo koji
    ulogovan zaposleni - bez obzira na dodeljene permisije - mogao je da odobrava
    ili odbija KYC podneske i proizvode partnera. Sada zahtevamo admin rolu ili
    eksplicitnu 'partners_edit' permisiju, po uzoru na model permisija iz
    routes/data.py. Vraca None ako je pristup dozvoljen, ili Flask response ako nije."""
    if 'user_id' not in session:
        return jsonify({"error": "UNAUTHORIZED"}), 401
    role = session.get('role')
    if role == 'admin':
        return None
    import sqlite3
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    try:
        c = conn.cursor()
        c.execute('SELECT permissions FROM users WHERE id=?', (session['user_id'],))
        row = c.fetchone()
    finally:
        conn.close()
    perms = decrypt_data(row[0]) if row and row[0] else {}
    if perms.get('partners_edit', False):
        return None
    log_audit('SECURITY', 'portal', 'Prevented unauthorized access to portal admin endpoint', is_suspicious=True)
    return jsonify({"error": "Unauthorized"}), 403

def verify_portal_auth(token, auth_header):
    """Provera portal sesije (constant-time + TTL). Deleguje na centralizovanu logiku."""
    return verify_portal_session(token, auth_header)


def require_partner_view():
    """KYC/portal dokumenti (pasoši, bankovni podaci, UBO...) su compliance-osetljivi.
    Sme ih preuzeti admin ili korisnik sa nekom 'partners' view/edit permisijom.
    Vraća None ako je dozvoljeno, ili Flask response ako nije."""
    if 'user_id' not in session:
        return jsonify({"error": "UNAUTHORIZED"}), 401
    if session.get('role') == 'admin':
        return None
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    try:
        c = conn.cursor()
        c.execute('SELECT permissions FROM users WHERE id=?', (session['user_id'],))
        row = c.fetchone()
    finally:
        conn.close()
    perms = decrypt_data(row[0]) if row and row[0] else {}
    allowed_keys = ('partners_view_all', 'partners_view', 'partners_view_own', 'partners_edit')
    if any(perms.get(k, False) for k in allowed_keys):
        return None
    log_audit('SECURITY', 'portal', 'Prevented unauthorized KYC/portal document download', is_suspicious=True)
    return jsonify({"error": "Unauthorized"}), 403

@portal_bp.route('/api/portal/products/submit/<token>', methods=['POST'])
def submit_portal_product(token):
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_auth(token, auth_header): 
        abort(401)
    
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    c = conn.cursor()
    partner_id, partner = find_partner_by_token(c, token, enforce_active=True)
    conn.close()
    if not partner_id: abort(403)
    company_name = partner.get('companyName', 'Unknown')

    prod_data = request.json or {}

    conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
    cp = conn_p.cursor()

    # BEZBEDNOST: partner sme da izmeni SAMO sopstveni pending proizvod.
    # Ako je prosleđen id, mora pripadati ovom partneru; u suprotnom se
    # generiše nov server-side id. Ovo sprečava da partner (preko client-side id-a)
    # prepiše tuđi portal-proizvod ili, nakon odobrenja, postojeći proizvod u glavnoj bazi.
    client_id = prod_data.get('id')
    product_id = None
    if client_id:
        cp.execute("SELECT partner_id FROM portal_products WHERE id=?", (client_id,))
        owner = cp.fetchone()
        if owner and owner[0] == partner_id:
            product_id = client_id
    if not product_id:
        product_id = str(uuid.uuid4())
    prod_data['id'] = product_id

    created_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    cp.execute("INSERT OR REPLACE INTO portal_products (id, partner_id, data, status, created_at) VALUES (?, ?, ?, ?, ?)", (product_id, partner_id, json.dumps(prod_data), 'pending', created_at))
    conn_p.commit()
    conn_p.close()
    log_audit('EDIT', 'portal', f"Partner '{company_name}' submitted product: {prod_data.get('name')}", is_suspicious=False)
    return jsonify({"status": "success", "message": "Product securely staging for verification"})

@portal_bp.route('/api/portal/rfq/submit/<token>', methods=['POST'])
def submit_rfq(token):
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_auth(token, auth_header): 
        abort(401)
    
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    c = conn.cursor()
    partner_id, partner = find_partner_by_token(c, token, enforce_active=True)
    if not partner_id:
        conn.close()
        abort(403)
    company_name = partner.get('companyName', 'Unknown')

    demand_data = request.json or {}
    
    # Striktna sanitizacija ulaza
    product_name = str(demand_data.get("productName", "")).strip()[:100]
    if not product_name:
        product_name = "Unspecified Commodity"
        
    demand_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    demand_obj = {
        "id": demand_id,
        # Pišemo OBA polja (customerId za portal, buyerId za CRM prikaz) kako bi
        # RFQ sa portala bio ispravno povezan sa klijentom u oba sistema. Ranije je
        # portal pisao samo customerId, a CRM lista potražnje čita buyerId, pa se
        # kupac prikazivao prazan.
        "customerId": partner_id,
        "buyerId": partner_id,
        "productId": None,
        "isNewProduct": True,
        "productName": product_name,
        "quantity": float(demand_data.get("quantity") or 0),
        "targetPrice": float(demand_data.get("targetPrice") or 0),
        "notes": str(demand_data.get("notes", "")).strip()[:1000],
        "date": now_iso,
        # createdAt je polje koje CRM prikaz koristi za datum; date ostaje zbog portala.
        "createdAt": now_iso,
        "status": "pending",
        "source": "B2B Portal"
    }
    
    c.execute("INSERT INTO demands (id, data) VALUES (?, ?)", (demand_id, json.dumps(demand_obj)))
    conn.commit()
    conn.close()
    log_audit('CREATE', 'demands', f"New RFQ for {product_name} submitted via portal by partner ID: {partner_id} ({company_name})", is_suspicious=False)
    return jsonify({"status": "success", "message": "Request for Quote securely submitted."})

@portal_bp.route('/api/portal/admin/products', methods=['GET'])
@login_required
def admin_get_portal_products():
    denied = require_portal_admin()
    if denied: return denied
    conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
    cp = conn_p.cursor()
    cp.execute("SELECT id, partner_id, data, status, created_at FROM portal_products ORDER BY created_at DESC")
    rows = cp.fetchall()
    conn_p.close()
    
    conn_m = sqlite3.connect(DB_FILE, timeout=30.0)
    cm = conn_m.cursor()
    cm.execute("SELECT id, data FROM partners")
    partners_map = {r[0]: safe_parse(r[1]).get('companyName', 'Unknown Partner') for r in cm.fetchall()}
    conn_m.close()
    
    return jsonify([{"id": r[0], "partner_id": r[1], "partner_name": partners_map.get(r[1], 'Unknown Partner'), "data": json.loads(r[2]), "status": r[3], "created_at": r[4]} for r in rows])

@portal_bp.route('/api/portal/admin/products/review/<product_id>', methods=['POST'])
@login_required
def admin_review_portal_product(product_id):
    denied = require_portal_admin()
    if denied: return denied
    action = request.json.get('action')
    conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
    cp = conn_p.cursor()
    cp.execute("SELECT partner_id, data FROM portal_products WHERE id=?", (product_id,))
    row = cp.fetchone()
    
    if not row:
        conn_p.close()
        return jsonify({"error": "Product target not found"}), 404
        
    partner_id, raw_data = row
    prod_data = json.loads(raw_data)
    
    if action == 'approve':
        cp.execute("UPDATE portal_products SET status='approved' WHERE id=?", (product_id,))
        conn_p.commit()
        
        conn_m = sqlite3.connect(DB_FILE, timeout=30.0)
        cm = conn_m.cursor()
        prod_data['id'] = product_id
        prod_data['isPartnerApproved'] = True 
        if 'supplyOffers' in prod_data and len(prod_data['supplyOffers']) > 0:
            for offer in prod_data['supplyOffers']: offer['supplierId'] = partner_id
                
        cm.execute("INSERT OR REPLACE INTO products (id, data) VALUES (?, ?)", (product_id, json.dumps(prod_data)))
        conn_m.commit()
        conn_m.close()
        log_audit('APPROVE', 'portal', f"Admin approved custom product configuration '{prod_data.get('name')}'", is_suspicious=False)
    else:
        cp.execute("UPDATE portal_products SET status='rejected' WHERE id=?", (product_id,))
        conn_p.commit()
        log_audit('REJECT', 'portal', f"Admin rejected product suggestion '{prod_data.get('name')}'", is_suspicious=False)
        
    conn_p.close()
    return jsonify({"status": "success", "message": "Operation processed successfully"})

@portal_bp.route('/api/portal/upload/<token>', methods=['POST'])
def portal_upload(token):
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_auth(token, auth_header): 
        abort(401)
        
    urls = []
    for file in request.files.getlist('file'):
        if file.filename == '':
            continue
            
        # Dodatna sigurnost: Provera ekstenzije i Magic Numbers (Bajt inspekcija fajla)
        if '.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS:
            if is_safe_file_content(file, file.filename):
                ext = file.filename.rsplit('.', 1)[1].lower()
                new_filename = f"doc_{uuid.uuid4().hex}.{ext}"
                save_path = os.path.join(PORTAL_UPLOAD_FOLDER, secure_filename(new_filename))
                file.save(save_path)
                urls.append(f"/portal_uploads/{new_filename}")
            else:
                log_audit('SECURITY', 'portal_actions', f'Malicious file upload attempt detected: {file.filename}', is_suspicious=True)
                
    if not urls: 
        return jsonify({"error": "Security Block: No valid or safe files uploaded."}), 400
        
    return jsonify({"status": "success", "urls": urls})

@portal_bp.route('/api/portal/kyc/submit/<token>', methods=['POST'])
def submit_kyc(token):
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_auth(token, auth_header): 
        abort(401)
        
    kyc_data = request.json or {}

    # BEZBEDNOST: partner_id se izvodi iz TOKENA (autoritativno), a ne iz payload-a.
    # Ranije je klijent slao partner_id i mogao da podnese KYC za tuđi profil.
    conn_id = sqlite3.connect(DB_FILE, timeout=30.0)
    try:
        c_id = conn_id.cursor()
        partner_id, _partner = find_partner_by_token(c_id, token, enforce_active=True)
    finally:
        conn_id.close()
    if not partner_id: abort(403)

    # 1. Osnovna sanitizacija kyc podataka
    clean_data = {
        "companyName": str(kyc_data.get('companyName', '')).strip()[:150],
        "regNo": str(kyc_data.get('regNo', '')).strip()[:50],
        "taxId": str(kyc_data.get('taxId', '')).strip()[:50],
        "website": str(kyc_data.get('website', '')).strip()[:100],
        "industry": str(kyc_data.get('industry', '')).strip()[:100],
        "regAddr": str(kyc_data.get('regAddr', '')).strip()[:200],
        "opAddr": str(kyc_data.get('opAddr', '')).strip()[:200],
        "bankName": str(kyc_data.get('bankName', '')).strip()[:100],
        "bankIban": str(kyc_data.get('bankIban', '')).strip()[:50],
        "bankSwift": str(kyc_data.get('bankSwift', '')).strip()[:20],
        "bankAddr": str(kyc_data.get('bankAddr', '')).strip()[:200],
        "corrBank": str(kyc_data.get('corrBank', '')).strip()[:100],
        "turnover": str(kyc_data.get('turnover', '')).strip()[:50],
        "sourceOfFunds": str(kyc_data.get('sourceOfFunds', '')).strip()[:150],
        "directors": kyc_data.get('directors', [])[:10],
        "ubos": kyc_data.get('ubos', [])[:10],
        "aml": kyc_data.get('aml', {}),
        "submitterName": str(kyc_data.get('submitterName', '')).strip()[:100],
        "submitterTitle": str(kyc_data.get('submitterTitle', '')).strip()[:100],
        "consent": bool(kyc_data.get('consent', False)),
        "files": kyc_data.get('files', {}) # DODATO: Prihvatanje putanja do uploadovanih dokumenata
    }
    
    if not clean_data['consent']:
        return jsonify({"error": "Explicit consent is legally required."}), 400
    
    conn = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
    c = conn.cursor()
    c.execute('''INSERT INTO kyc_submissions (id, partner_id, token, data, submitted_at) VALUES (?, ?, ?, ?, ?)''', 
              (str(uuid.uuid4()), partner_id, token, encrypt_data(clean_data), datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')))
    conn.commit()
    conn.close()
    log_audit('EDIT', 'portal', f"Partner {clean_data.get('companyName')} payload securely encrypted inside air-gapped vault", is_suspicious=False)
    return jsonify({"status": "success", "message": "KYC Data securely submitted to Vault."})

@portal_bp.route('/api/portal/admin/submissions/all', methods=['GET'])
@login_required
def get_all_kyc_submissions():
    denied = require_portal_admin()
    if denied: return denied
    conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
    cp = conn_p.cursor()
    cp.execute("SELECT id, partner_id, data, submitted_at FROM kyc_submissions ORDER BY submitted_at DESC")
    rows = cp.fetchall()
    conn_p.close()
    
    conn_m = sqlite3.connect(DB_FILE, timeout=30.0)
    cm = conn_m.cursor()
    cm.execute("SELECT id, data FROM partners")
    partners_map = {r[0]: safe_parse(r[1]).get('companyName', 'Unknown') for r in cm.fetchall()}
    conn_m.close()

    subs = [{"id": r[0], "partner_id": r[1], "partner_name": partners_map.get(r[1], 'Unknown'), "data": decrypt_data(r[2]), "submitted_at": r[3]} for r in rows]
    return jsonify(subs)

@portal_bp.route('/api/portal/admin/submissions/approve/<sub_id>', methods=['POST'])
@login_required
def approve_kyc_submission(sub_id):
    """Odobrava KYC podnesak i MERGE-uje sve podatke u partner profil (banking,
    directors, UBOs, AML, fajlovi, tax/reg brojevi, adresa). Dodatno prihvata
    riskLevel i notes iz forme i beleži ih u partner.kyc + partner.activities.
    Šalje email potvrdu klijentu (profesionalni šablon)."""
    denied = require_portal_admin()
    if denied: return denied

    payload = request.get_json(silent=True) or {}
    risk_level = str(payload.get('riskLevel', 'medium')).strip()
    notes = str(payload.get('notes', '')).strip()[:2000]

    conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
    cp = conn_p.cursor()
    cp.execute("SELECT partner_id, data FROM kyc_submissions WHERE id=?", (sub_id,))
    row = cp.fetchone()

    if not row:
        conn_p.close()
        return jsonify({"error": "Submission not found"}), 404

    partner_id = row[0]
    kyc_data = decrypt_data(row[1])

    # Označi kao odobreno u portal bazi (za istoriju)
    if 'status' not in kyc_data: kyc_data['status'] = 'approved'
    kyc_data['reviewedAt'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    kyc_data['reviewedBy'] = session.get('username', 'admin')
    cp.execute("UPDATE kyc_submissions SET data=? WHERE id=?", (encrypt_data(kyc_data), sub_id))
    conn_p.commit()
    conn_p.close()

    conn_m = sqlite3.connect(DB_FILE, timeout=30.0)
    cm = conn_m.cursor()
    cm.execute("SELECT data FROM partners WHERE id=?", (partner_id,))
    p_row = cm.fetchone()

    if not p_row:
        conn_m.close()
        return jsonify({"error": "Partner not found"}), 404

    partner = safe_parse(p_row[0])
    partner['companyName'] = kyc_data.get('companyName') or partner.get('companyName')
    partner['taxId'] = kyc_data.get('taxId') or partner.get('taxId')
    partner['regNo'] = kyc_data.get('regNo') or partner.get('regNo')
    partner['website'] = kyc_data.get('website') or partner.get('website')
    partner['industry'] = kyc_data.get('industry') or partner.get('industry')

    if 'address' not in partner or not isinstance(partner['address'], dict): partner['address'] = {}
    if kyc_data.get('regAddr'): partner['address']['street'] = kyc_data.get('regAddr')
    if kyc_data.get('opAddr'): partner['address']['operationalAddress'] = kyc_data.get('opAddr')

    if 'banking' not in partner or not isinstance(partner['banking'], dict): partner['banking'] = {}
    if kyc_data.get('bankName'): partner['banking']['bankName'] = kyc_data.get('bankName')
    if kyc_data.get('bankIban'): partner['banking']['iban'] = kyc_data.get('bankIban')
    if kyc_data.get('bankSwift'): partner['banking']['swift'] = kyc_data.get('bankSwift')
    if kyc_data.get('bankAddr'): partner['banking']['bankAddress'] = kyc_data.get('bankAddr')
    if kyc_data.get('corrBank'): partner['banking']['correspondentBank'] = kyc_data.get('corrBank')

    if 'kyc' not in partner or not isinstance(partner['kyc'], dict): partner['kyc'] = {}
    partner['kyc']['status'] = 'approved'
    partner['kyc']['riskLevel'] = risk_level
    partner['kyc']['notes'] = notes
    partner['kyc']['reviewedAt'] = kyc_data['reviewedAt']
    partner['kyc']['reviewedBy'] = kyc_data['reviewedBy']
    partner['kyc']['directors'] = kyc_data.get('directors', [])
    partner['kyc']['ubos'] = kyc_data.get('ubos', [])
    partner['kyc']['aml'] = kyc_data.get('aml', {})
    partner['kyc']['files'] = kyc_data.get('files', {})
    partner['kyc']['turnover'] = kyc_data.get('turnover')
    partner['kyc']['sourceOfFunds'] = kyc_data.get('sourceOfFunds')
    partner['kyc']['submitterName'] = kyc_data.get('submitterName')
    partner['kyc']['submitterTitle'] = kyc_data.get('submitterTitle')

    # Aktivnost za audit trag u CRM-u
    if 'activities' not in partner or not isinstance(partner['activities'], list): partner['activities'] = []
    partner['activities'].insert(0, {
        'id': uuid.uuid4().hex,
        'date': kyc_data['reviewedAt'],
        'type': 'KYC Approved',
        'note': f"KYC odobren by {kyc_data['reviewedBy']}. Risk: {risk_level}." + (f" Notes: {notes}" if notes else "")
    })

    cm.execute("UPDATE partners SET data=? WHERE id=?", (json.dumps(partner), partner_id))
    conn_m.commit()
    conn_m.close()
    log_audit('APPROVE', 'kyc', f"KYC merged into CRM for {partner.get('companyName')} (risk: {risk_level})", is_suspicious=False)

    # Profesionalan email klijentu
    client_email = partner.get('contact', {}).get('email') or partner.get('email')
    if client_email:
        try:
            from utils_email import send_kyc_approved
            token = partner.get('portalToken', '')
            portal_url = request.url_root.rstrip('/') + f"/portal/{token}" if token else request.url_root
            send_kyc_approved(client_email, partner.get('companyName', ''), portal_url)
        except Exception as e:
            log_audit('ERROR', 'kyc', f"Failed to send KYC approval email: {e}", is_suspicious=False)

    return jsonify({"status": "success", "message": "KYC data merged to CRM profile.", "kyc": partner.get('kyc', {})})


@portal_bp.route('/api/portal/admin/submissions/request_update/<sub_id>', methods=['POST'])
@login_required
def request_kyc_update(sub_id):
    """Označava KYC kao 'update_requested' — klijent u portalu vidi banner sa
    porukom da admin traži dopunu podataka. Šalje email sa razlogom."""
    denied = require_portal_admin()
    if denied: return denied

    payload = request.get_json(silent=True) or {}
    note = str(payload.get('notes', '')).strip()[:2000]
    risk_level = str(payload.get('riskLevel', 'medium')).strip()

    conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
    cp = conn_p.cursor()
    cp.execute("SELECT partner_id, data FROM kyc_submissions WHERE id=?", (sub_id,))
    row = cp.fetchone()
    if not row:
        conn_p.close()
        return jsonify({"error": "Submission not found"}), 404
    partner_id = row[0]
    kyc_data = decrypt_data(row[1])
    kyc_data['status'] = 'update_requested'
    kyc_data['reviewNote'] = note
    kyc_data['reviewedAt'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    kyc_data['reviewedBy'] = session.get('username', 'admin')
    cp.execute("UPDATE kyc_submissions SET data=? WHERE id=?", (encrypt_data(kyc_data), sub_id))
    conn_p.commit()
    conn_p.close()

    # Update partner.kycStatus za portal banner
    conn_m = sqlite3.connect(DB_FILE, timeout=30.0)
    cm = conn_m.cursor()
    cm.execute("SELECT data FROM partners WHERE id=?", (partner_id,))
    p_row = cm.fetchone()
    partner = None
    if p_row:
        partner = safe_parse(p_row[0])
        if 'kyc' not in partner or not isinstance(partner['kyc'], dict): partner['kyc'] = {}
        partner['kyc']['status'] = 'update_requested'
        partner['kyc']['reviewNote'] = note
        partner['kyc']['riskLevel'] = risk_level
        if 'activities' not in partner or not isinstance(partner['activities'], list): partner['activities'] = []
        partner['activities'].insert(0, {
            'id': uuid.uuid4().hex,
            'date': kyc_data['reviewedAt'],
            'type': 'KYC Update Requested',
            'note': note or 'Additional information required.'
        })
        cm.execute("UPDATE partners SET data=? WHERE id=?", (json.dumps(partner), partner_id))
        conn_m.commit()
    conn_m.close()

    log_audit('EDIT', 'kyc', f"KYC update requested for {(partner or {}).get('companyName', partner_id)}: {note[:100]}", is_suspicious=False)

    # Email klijentu
    client_email = (partner or {}).get('contact', {}).get('email') or (partner or {}).get('email') if partner else None
    if client_email:
        try:
            from utils_email import send_kyc_update_requested
            token = (partner or {}).get('portalToken', '') if partner else ''
            portal_url = request.url_root.rstrip('/') + f"/portal/{token}" if token else request.url_root
            send_kyc_update_requested(client_email, (partner or {}).get('companyName', ''), portal_url, note)
        except Exception as e:
            log_audit('ERROR', 'kyc', f"Failed to send KYC update-requested email: {e}", is_suspicious=False)

    return jsonify({"status": "success", "message": "Client notified — additional information requested."})


@portal_bp.route('/api/portal/admin/submissions/reject/<sub_id>', methods=['POST'])
@login_required
def reject_kyc_submission(sub_id):
    """Odbija KYC. Ne merge-uje podatke; partner.kycStatus = 'rejected'."""
    denied = require_portal_admin()
    if denied: return denied

    payload = request.get_json(silent=True) or {}
    note = str(payload.get('notes', '')).strip()[:2000]

    conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
    cp = conn_p.cursor()
    cp.execute("SELECT partner_id, data FROM kyc_submissions WHERE id=?", (sub_id,))
    row = cp.fetchone()
    if not row:
        conn_p.close()
        return jsonify({"error": "Submission not found"}), 404
    partner_id = row[0]
    kyc_data = decrypt_data(row[1])
    kyc_data['status'] = 'rejected'
    kyc_data['reviewNote'] = note
    kyc_data['reviewedAt'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    kyc_data['reviewedBy'] = session.get('username', 'admin')
    cp.execute("UPDATE kyc_submissions SET data=? WHERE id=?", (encrypt_data(kyc_data), sub_id))
    conn_p.commit()
    conn_p.close()

    conn_m = sqlite3.connect(DB_FILE, timeout=30.0)
    cm = conn_m.cursor()
    cm.execute("SELECT data FROM partners WHERE id=?", (partner_id,))
    p_row = cm.fetchone()
    partner = None
    if p_row:
        partner = safe_parse(p_row[0])
        if 'kyc' not in partner or not isinstance(partner['kyc'], dict): partner['kyc'] = {}
        partner['kyc']['status'] = 'rejected'
        partner['kyc']['reviewNote'] = note
        if 'activities' not in partner or not isinstance(partner['activities'], list): partner['activities'] = []
        partner['activities'].insert(0, {
            'id': uuid.uuid4().hex,
            'date': kyc_data['reviewedAt'],
            'type': 'KYC Rejected',
            'note': note or 'KYC rejected.'
        })
        cm.execute("UPDATE partners SET data=? WHERE id=?", (json.dumps(partner), partner_id))
        conn_m.commit()
    conn_m.close()

    log_audit('REJECT', 'kyc', f"KYC rejected for {(partner or {}).get('companyName', partner_id)}", is_suspicious=True)
    return jsonify({"status": "success", "message": "KYC submission rejected."})

@portal_bp.route('/portal_uploads/<filename>')
@login_required
def serve_portal_uploads(filename):
    denied = require_partner_view()
    if denied: return denied
    log_audit('DOWNLOAD', 'portal', f'KYC/portal document downloaded: {secure_filename(filename)}', is_suspicious=False)
    return send_from_directory(current_app.config['PORTAL_UPLOAD_FOLDER'], secure_filename(filename))


# ==========================================================
#  PROFILE CHANGE REQUESTS — admin lista / odobrenje / odbijanje
# ==========================================================

@portal_bp.route('/api/portal/admin/profile_requests', methods=['GET'])
@login_required
def admin_list_profile_requests():
    """Vraća sve pending zahteve za izmenu partnerskog profila (email, telefon, adresa)."""
    denied = require_portal_admin()
    if denied: return denied
    status_filter = request.args.get('status')  # 'pending', 'approved', 'rejected', ili None za sve

    conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
    conn_m = sqlite3.connect(DB_FILE, timeout=30.0)
    try:
        cp = conn_p.cursor()
        if status_filter:
            cp.execute("SELECT id, partner_id, data, status, submitted_at, reviewed_at FROM profile_change_requests WHERE status=? ORDER BY submitted_at DESC", (status_filter,))
        else:
            cp.execute("SELECT id, partner_id, data, status, submitted_at, reviewed_at FROM profile_change_requests ORDER BY submitted_at DESC")
        rows = cp.fetchall()
        cm = conn_m.cursor()
        cm.execute("SELECT id, data FROM partners")
        partners_map = {}
        for pr in cm.fetchall():
            pd = safe_parse(pr[1])
            partners_map[pr[0]] = {
                'name': pd.get('companyName', 'Unknown'),
                'currentEmail': pd.get('contact', {}).get('email') or pd.get('email', ''),
                'currentPhone': pd.get('contact', {}).get('phone') or pd.get('phone', ''),
                'currentPerson': pd.get('contact', {}).get('person', ''),
                'currentStreet': pd.get('address', {}).get('street', ''),
                'currentCity': pd.get('address', {}).get('city', ''),
                'currentCountry': pd.get('address', {}).get('country', '')
            }
    finally:
        conn_p.close(); conn_m.close()

    result = []
    for r in rows:
        pinfo = partners_map.get(r[1], {'name': 'Unknown'})
        result.append({
            "id": r[0], "partner_id": r[1], "partner_name": pinfo.get('name'),
            "current": {k: v for k, v in pinfo.items() if k.startswith('current')},
            "changes": json.loads(r[2]) if r[2] else {},
            "status": r[3], "submitted_at": r[4], "reviewed_at": r[5]
        })
    return jsonify(result)


@portal_bp.route('/api/portal/admin/profile_requests/<req_id>/review', methods=['POST'])
@login_required
def admin_review_profile_request(req_id):
    """Odobrava ili odbija zahtev za izmenu profila. Na odobrenje primenjuje
    tražene izmene na partnerski profil u CRM bazi i beleži audit trag."""
    denied = require_portal_admin()
    if denied: return denied
    action = (request.get_json(silent=True) or {}).get('action', '').lower()
    if action not in ('approve', 'reject'):
        return jsonify({"error": "INVALID_ACTION"}), 400

    conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
    try:
        cp = conn_p.cursor()
        cp.execute("SELECT partner_id, data, status FROM profile_change_requests WHERE id=?", (req_id,))
        row = cp.fetchone()
        if not row:
            return jsonify({"error": "REQUEST_NOT_FOUND"}), 404
        if row[2] != 'pending':
            return jsonify({"error": "ALREADY_REVIEWED"}), 400
        partner_id = row[0]
        changes = json.loads(row[1]) if row[1] else {}
        now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        reviewer = session.get('username', 'admin')

        if action == 'approve':
            # Primeni na partner zapis
            conn_m = sqlite3.connect(DB_FILE, timeout=30.0)
            try:
                cm = conn_m.cursor()
                cm.execute("SELECT data FROM partners WHERE id=?", (partner_id,))
                prow = cm.fetchone()
                if not prow:
                    return jsonify({"error": "PARTNER_NOT_FOUND"}), 404
                partner = safe_parse(prow[0])
                if 'contact' not in partner: partner['contact'] = {}
                if 'address' not in partner: partner['address'] = {}
                summary = []
                if 'email' in changes:
                    old = partner.get('contact', {}).get('email') or partner.get('email', '')
                    partner['contact']['email'] = changes['email']; partner['email'] = changes['email']
                    summary.append(f"email: {old} → {changes['email']}")
                if 'phone' in changes:
                    old = partner.get('contact', {}).get('phone') or partner.get('phone', '')
                    partner['contact']['phone'] = changes['phone']; partner['phone'] = changes['phone']
                    summary.append(f"phone: {old} → {changes['phone']}")
                if 'contactPerson' in changes:
                    old = partner.get('contact', {}).get('person', '')
                    partner['contact']['person'] = changes['contactPerson']
                    summary.append(f"person: {old} → {changes['contactPerson']}")
                if 'street' in changes:
                    partner['address']['street'] = changes['street']
                    summary.append(f"street → {changes['street']}")
                if 'city' in changes:
                    partner['address']['city'] = changes['city']
                    summary.append(f"city → {changes['city']}")
                if 'country' in changes:
                    partner['address']['country'] = changes['country']
                    summary.append(f"country → {changes['country']}")
                cm.execute("UPDATE partners SET data=? WHERE id=?", (json.dumps(partner), partner_id))
                conn_m.commit()
            finally:
                conn_m.close()
            cp.execute("UPDATE profile_change_requests SET status='approved', reviewed_at=?, reviewed_by=? WHERE id=?", (now_iso, reviewer, req_id))
            conn_p.commit()
            log_audit('APPROVE', 'portal', f"Approved profile change for partner {partner_id}: {', '.join(summary)}", is_suspicious=False)
            return jsonify({"status": "success", "message": "Zahtev odobren i primenjen.", "applied": changes})
        else:
            cp.execute("UPDATE profile_change_requests SET status='rejected', reviewed_at=?, reviewed_by=? WHERE id=?", (now_iso, reviewer, req_id))
            conn_p.commit()
            log_audit('REJECT', 'portal', f"Rejected profile change request {req_id} for partner {partner_id}", is_suspicious=False)
            return jsonify({"status": "success", "message": "Zahtev odbijen."})
    finally:
        conn_p.close()


# ==========================================================
#  PDF DOWNLOAD IZ PORTALA — sa obaveznim audit tragom
# ==========================================================

@portal_bp.route('/api/portal/document/<token>/<doc_id>', methods=['GET'])
def portal_download_document(token, doc_id):
    """Klijent portala preuzima svoj dokument (ponuda/faktura/dogovor). Svaki
    download se beleži u audit dnevnik sa nazivom firme i datumom, kako bi u
    slučaju spora postojao trag da je klijent preuzeo dokument."""
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_session(token, auth_header):
        return jsonify({"error": "UNAUTHORIZED"}), 401

    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    try:
        c = conn.cursor()
        partner_id, partner = find_partner_by_token(c, token, enforce_active=True)
        if not partner_id:
            return jsonify({"error": "FORBIDDEN"}), 403

        # Dokument mora pripadati OVOM partneru (sprečava enumeraciju tuđih doc_id-eva).
        c.execute("SELECT data FROM shared_documents WHERE id=?", (doc_id,))
        row = c.fetchone()
        if not row:
            return jsonify({"error": "DOCUMENT_NOT_FOUND"}), 404
        doc = safe_parse(row[0])
        if doc.get('partnerId') != partner_id:
            log_audit('SECURITY', 'portal', f'Blocked cross-partner document access attempt: doc {doc_id} by {partner_id}', is_suspicious=True)
            return jsonify({"error": "FORBIDDEN"}), 403
    finally:
        conn.close()

    file_url = doc.get('fileUrl') or ''
    # file_url je oblika '/uploads/<uuid>.pdf' — preuzmi fajl iz odgovarajućeg foldera
    filename = os.path.basename(file_url)
    safe_name = secure_filename(filename)
    if not safe_name:
        return jsonify({"error": "INVALID_FILE"}), 400

    # Odaberi folder na osnovu prefiksa; podržava i /portal_uploads/ i /uploads/
    if file_url.startswith('/portal_uploads/'):
        folder = current_app.config['PORTAL_UPLOAD_FOLDER']
    else:
        folder = current_app.config['UPLOAD_FOLDER']

    company = partner.get('companyName', 'Unknown')
    log_audit(
        'DOWNLOAD', 'portal',
        f"Client '{company}' downloaded document '{doc.get('fileName', safe_name)}' (type: {doc.get('docType', 'Document')}) via portal",
        is_suspicious=False
    )
    return send_from_directory(folder, safe_name, as_attachment=True, download_name=doc.get('fileName') or safe_name)


# ==========================================================
#  PORTAL: prihvatanje ponude od strane klijenta
# ==========================================================

@portal_bp.route('/api/portal/offers/accept/<token>/<offer_id>', methods=['POST'])
def portal_accept_offer(token, offer_id):
    """Klijent u portalu potvrđuje prihvatanje ponude. Ponuda dobija status
    'client_accepted' + timestamp. Admin u CRM-u tada može jednim klikom
    ('Kreiraj dil') da konvertuje ponudu u dil."""
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_session(token, auth_header):
        return jsonify({"error": "UNAUTHORIZED"}), 401

    payload = request.get_json(silent=True) or {}
    action = str(payload.get('action', 'accept')).lower()  # accept, decline
    note = str(payload.get('note', '')).strip()[:500]

    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    try:
        c = conn.cursor()
        partner_id, partner = find_partner_by_token(c, token, enforce_active=True)
        if not partner_id:
            return jsonify({"error": "FORBIDDEN"}), 403

        c.execute("SELECT data FROM offers WHERE id=?", (offer_id,))
        row = c.fetchone()
        if not row:
            return jsonify({"error": "OFFER_NOT_FOUND"}), 404
        offer = safe_parse(row[0])
        # Ponuda mora pripadati OVOM partneru
        if offer.get('customerId') != partner_id:
            log_audit('SECURITY', 'portal', f'Blocked cross-partner offer accept attempt: offer {offer_id} by {partner_id}', is_suspicious=True)
            return jsonify({"error": "FORBIDDEN"}), 403

        now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        if action == 'accept':
            offer['clientStatus'] = 'accepted'
            offer['clientAcceptedAt'] = now_iso
            offer['clientNote'] = note
            log_action = 'APPROVE'
        elif action == 'decline':
            offer['clientStatus'] = 'declined'
            offer['clientDeclinedAt'] = now_iso
            offer['clientNote'] = note
            log_action = 'REJECT'
        else:
            return jsonify({"error": "INVALID_ACTION"}), 400

        c.execute("UPDATE offers SET data=? WHERE id=?", (json.dumps(offer), offer_id))
        conn.commit()
    finally:
        conn.close()

    log_audit(log_action, 'portal', f"Client '{partner.get('companyName')}' {action}ed offer {offer.get('offerNo', offer_id)}", is_suspicious=False)
    return jsonify({"status": "success", "clientStatus": offer.get('clientStatus'), "at": offer.get('clientAcceptedAt') or offer.get('clientDeclinedAt')})


# ==========================================================
#  CRM DASHBOARD: brojači pending stavki iz portala (za notifikacije)
# ==========================================================

@portal_bp.route('/api/portal/admin/pending_counts', methods=['GET'])
@login_required
def admin_portal_pending_counts():
    """Vraća brojeve pending stavki iz portala (KYC, roba, izmene profila, RFQ)
    kako bi CRM dashboard prikazao admin badge/upozorenja."""
    denied = require_portal_admin()
    if denied: return denied

    counts = {"kyc": 0, "products": 0, "profile_requests": 0, "rfqs": 0}
    try:
        conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
        cp = conn_p.cursor()
        # KYC: broj partnera koji imaju bar 1 nepregledanu prijavu (nema explicit 'reviewed' zastavice u schemi;
        # tretiramo najsvežiju prijavu po partneru kao aktivnu — ovde vraćamo ukupan broj submisija bez merge-a).
        cp.execute("SELECT COUNT(*) FROM kyc_submissions")
        counts["kyc"] = cp.fetchone()[0] or 0
        cp.execute("SELECT COUNT(*) FROM portal_products WHERE status='pending'")
        counts["products"] = cp.fetchone()[0] or 0
        try:
            cp.execute("SELECT COUNT(*) FROM profile_change_requests WHERE status='pending'")
            counts["profile_requests"] = cp.fetchone()[0] or 0
        except sqlite3.OperationalError:
            counts["profile_requests"] = 0
        conn_p.close()
    except Exception:
        pass

    # RFQ (potraživnje) iz portala u glavnoj bazi
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        c = conn.cursor()
        c.execute("SELECT data FROM demands")
        rfq_pending = 0
        for r in c.fetchall():
            d = safe_parse(r[0])
            if d.get('source') == 'B2B Portal' and d.get('status') == 'pending':
                rfq_pending += 1
        counts["rfqs"] = rfq_pending
        conn.close()
    except Exception:
        pass

    counts["total"] = sum(counts.values())
    return jsonify(counts)