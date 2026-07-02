import os
import sqlite3
import json
import secrets
import uuid
from datetime import datetime, timezone
from werkzeug.utils import secure_filename
from flask import request, jsonify, abort, send_from_directory, current_app
from config import DB_FILE, PORTAL_DB_FILE, PORTAL_UPLOAD_FOLDER, ALLOWED_EXTENSIONS
from utils import log_audit, login_required, encrypt_data, decrypt_data, is_safe_file_content
from . import portal_bp, portal_auth_sessions, safe_parse

def verify_portal_auth(token, auth_header):
    """Pomoćna funkcija za proveru memorijske sesije i validnosti tokena"""
    if not token or not auth_header or portal_auth_sessions.get(token) != auth_header:
        return False
    return True

@portal_bp.route('/api/portal/products/submit/<token>', methods=['POST'])
def submit_portal_product(token):
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_auth(token, auth_header): 
        abort(401)
    
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    c = conn.cursor()
    c.execute("SELECT id, data FROM partners")
    partner_id, company_name = None, "Unknown"
    for r in c.fetchall():
        p_data = safe_parse(r[1])
        if p_data.get('portalToken') == token:
            partner_id, company_name = r[0], p_data.get('companyName', 'Unknown')
            break
    conn.close()
    if not partner_id: abort(403)
    
    prod_data = request.json
    product_id = prod_data.get('id') or str(uuid.uuid4())
    prod_data['id'] = product_id
    
    conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
    cp = conn_p.cursor()
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
    c.execute("SELECT id, data FROM partners")
    partner_id = None
    company_name = "Unknown"
    for r in c.fetchall():
        p_data = safe_parse(r[1])
        if p_data.get('portalToken') == token:
            partner_id = r[0]
            company_name = p_data.get('companyName', 'Unknown')
            break
    
    if not partner_id:
        conn.close()
        abort(403)
        
    demand_data = request.json
    
    # Striktna sanitizacija ulaza
    product_name = str(demand_data.get("productName", "")).strip()[:100]
    if not product_name:
        product_name = "Unspecified Commodity"
        
    demand_id = str(uuid.uuid4())
    demand_obj = {
        "id": demand_id,
        "customerId": partner_id,
        "productName": product_name,
        "quantity": float(demand_data.get("quantity") or 0),
        "targetPrice": float(demand_data.get("targetPrice") or 0),
        "notes": str(demand_data.get("notes", "")).strip()[:1000],
        "date": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
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
        
    kyc_data = request.json
    partner_id = kyc_data.get('partner_id')
    if not partner_id: abort(400)
    
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
        "consent": bool(kyc_data.get('consent', False))
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
    conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
    cp = conn_p.cursor()
    cp.execute("SELECT partner_id, data FROM kyc_submissions WHERE id=?", (sub_id,))
    row = cp.fetchone()
    
    if not row:
        conn_p.close()
        return jsonify({"error": "Submission not found"}), 404
        
    partner_id = row[0]
    kyc_data = decrypt_data(row[1])
    
    # Mark as approved in portal DB
    if 'status' not in kyc_data: kyc_data['status'] = 'approved'
    cp.execute("UPDATE kyc_submissions SET data=? WHERE id=?", (encrypt_data(kyc_data), sub_id))
    conn_p.commit()
    conn_p.close()

    conn_m = sqlite3.connect(DB_FILE, timeout=30.0)
    cm = conn_m.cursor()
    cm.execute("SELECT data FROM partners WHERE id=?", (partner_id,))
    p_row = cm.fetchone()
    
    if p_row:
        partner = safe_parse(p_row[0])
        partner['companyName'] = kyc_data.get('companyName') or partner.get('companyName')
        partner['taxId'] = kyc_data.get('taxId') or partner.get('taxId')
        partner['regNo'] = kyc_data.get('regNo') or partner.get('regNo')
        partner['website'] = kyc_data.get('website') or partner.get('website')
        
        if 'address' not in partner: partner['address'] = {}
        partner['address']['street'] = kyc_data.get('regAddr') or partner['address'].get('street')
        
        if 'banking' not in partner: partner['banking'] = {}
        partner['banking']['bankName'] = kyc_data.get('bankName')
        partner['banking']['iban'] = kyc_data.get('bankIban')
        partner['banking']['swift'] = kyc_data.get('bankSwift')

        if 'kyc' not in partner: partner['kyc'] = {}
        partner['kyc']['status'] = 'approved'
        partner['kyc']['directors'] = kyc_data.get('directors', [])
        partner['kyc']['ubos'] = kyc_data.get('ubos', [])
        partner['kyc']['aml'] = kyc_data.get('aml', {})
        
        cm.execute("UPDATE partners SET data=? WHERE id=?", (json.dumps(partner), partner_id))
        conn_m.commit()
        log_audit('APPROVE', 'kyc', f"Merged KYC submission into CRM profile for {partner.get('companyName')}", is_suspicious=False)
        
    conn_m.close()
    return jsonify({"status": "success", "message": "KYC Data safely merged to official CRM profile."})

@portal_bp.route('/portal_uploads/<filename>')
@login_required
def serve_portal_uploads(filename):
    return send_from_directory(current_app.config['PORTAL_UPLOAD_FOLDER'], secure_filename(filename))