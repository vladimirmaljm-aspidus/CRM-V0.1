import os
import sqlite3
import json
import secrets
import time
import uuid
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone
from werkzeug.utils import secure_filename
from flask import Blueprint, request, jsonify, render_template, abort, send_from_directory, current_app, session
from config import DB_FILE, PORTAL_DB_FILE, PORTAL_UPLOAD_FOLDER
from utils import log_audit, login_required, allowed_file, FirewallCache, encrypt_data, decrypt_data, is_safe_file_content

portal_bp = Blueprint('portal', __name__)

# [ANTI-DDOS I OTP MEMORIJA]
portal_otps = {}
portal_auth_sessions = {}

def check_portal_rate_limit(ip):
    if ip in FirewallCache.whitelist: return True
    now = time.time()
    FirewallCache.portal_attempts[ip] = [t for t in FirewallCache.portal_attempts.get(ip, []) if now - t < 60]
    if len(FirewallCache.portal_attempts.get(ip, [])) > 50: return False
    FirewallCache.portal_attempts.setdefault(ip, []).append(now)
    return True

def init_portal_db():
    conn = None
    try:
        conn = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
        conn.execute('PRAGMA journal_mode=WAL;')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS kyc_submissions 
                     (id TEXT PRIMARY KEY, partner_id TEXT, token TEXT, data JSON, submitted_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS portal_products 
                     (id TEXT PRIMARY KEY, partner_id TEXT, data JSON, status TEXT, created_at TEXT)''')
        conn.commit()
    finally:
        if conn: conn.close()

init_portal_db()

# SAFE PARSE: Automatski spašava sistem od pucanja ako naiđe na stare kriptovane podatke u bazi
def safe_parse(data_str):
    try:
        return json.loads(data_str)
    except:
        return decrypt_data(data_str)

# 1. Generisanje sigurnosnog linka za portal (Poziva se iz CRM-a)
@portal_bp.route('/api/portal/generate/<partner_id>', methods=['POST'])
@login_required
def generate_portal_link(partner_id):
    conn = None
    action_log = None
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA busy_timeout=30000;')
        c = conn.cursor()
        
        c.execute('SELECT data FROM partners WHERE id=?', (partner_id,))
        row = c.fetchone()
        if not row: return jsonify({"error": "Partner not found"}), 404
            
        partner = safe_parse(row[0])
        if 'portalToken' not in partner or not partner['portalToken']:
            partner['portalToken'] = secrets.token_urlsafe(32)
            c.execute('UPDATE partners SET data=? WHERE id=?', (json.dumps(partner), partner_id))
            conn.commit()
            action_log = ('EDIT', 'partners', f'Generated secure B2B portal token for partner ID: {partner_id}', False)
            
        return jsonify({"status": "success", "token": partner['portalToken']})
    finally:
        if conn: conn.close()
        if action_log:
            log_audit(action_log[0], action_log[1], action_log[2], is_suspicious=action_log[3])

# 2. Prikaz HTML interfejsa B2B Portala
@portal_bp.route('/portal/<token>', methods=['GET'])
def view_portal(token):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip: ip = ip.split(',')[0].strip()
    if not check_portal_rate_limit(ip): 
        abort(429, description="DDoS Protection: Rate limit exceeded.")
    return render_template('portal.html', token=token)

# 3. Kreiranje i slanje OTP koda na mejl klijenta
@portal_bp.route('/api/portal/auth/send_otp/<token>', methods=['POST'])
def send_otp(token):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if not check_portal_rate_limit(ip): abort(429)
    
    conn = None
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        conn.execute('PRAGMA journal_mode=WAL;')
        c = conn.cursor()
        
        c.execute("SELECT data FROM partners")
        partner = None
        for r in c.fetchall():
            p_data = safe_parse(r[0])
            if p_data.get('portalToken') == token:
                partner = p_data
                break
                
        if not partner:
            return jsonify({"error": "Invalid token"}), 403
            
        # Preuzimanje e-mail adrese (gde god da se nalazi u JSON strukturi)
        client_email = partner.get('contact', {}).get('email') or partner.get('email')
        otp = str(secrets.randbelow(900000) + 100000)
        portal_otps[token] = {'otp': otp, 'expires': time.time() + 300} 
        
        email_sent = False
        c.execute("SELECT value FROM settings WHERE key='comms_settings'")
        smtp_row = c.fetchone()
        
        if smtp_row and client_email:
            settings = decrypt_data(smtp_row[0]) 
            
            # Bezbedna provera strukture pre konekcije na SMTP server
            if isinstance(settings, dict):
                smtp_server = settings.get('smtpServer')
                smtp_port = int(settings.get('smtpPort', 587))
                smtp_user = settings.get('smtpUser')
                smtp_pass = settings.get('smtpPass')
                smtp_security = settings.get('smtpSecurity', 'tls')
                sender_name = settings.get('senderName', 'Aspidus CRM')
                sender_email = settings.get('senderEmail', smtp_user)
                
                if smtp_server and smtp_user and smtp_pass:
                    try:
                        email_body = (
                            f"Your security code for Aspidus B2B Portal is / Vaš sigurnosni kod za pristup Aspidus B2B Portalu je: {otp}\n\n"
                            f"This code expires in 5 minutes. / Ovaj kod ističe za 5 minuta."
                        )
                        msg = MIMEText(email_body, 'plain')
                        msg['Subject'] = f"{sender_name} - B2B Portal OTP Security Code"
                        msg['From'] = f"{sender_name} <{sender_email}>"
                        msg['To'] = client_email
                        
                        # Pametno mapiranje protokola i portova
                        if smtp_security == 'ssl' or smtp_port == 465:
                            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15)
                        else:
                            server = smtplib.SMTP(smtp_server, smtp_port, timeout=15)
                            if smtp_security != 'none':
                                server.starttls()
                                
                        server.login(smtp_user, smtp_pass)
                        server.send_message(msg)
                        server.quit()
                        email_sent = True
                        log_audit('COMMUNICATION', 'portal', f'OTP sent via Email to {client_email}', is_suspicious=False)
                    except Exception as e:
                        print("SMTP Error sending OTP:", e)

        print(f"\n========================================================")
        print(f"🔒 B2B PORTAL LOGIN OTP CODE: {otp} (For: {partner.get('companyName')})")
        print(f"========================================================\n")
        
        if email_sent:
            return jsonify({"status": "success", "message": f"OTP sent to client email / OTP poslat na klijentov email ({client_email})."})
        elif client_email:
            return jsonify({"status": "success", "message": "SMTP Greška: Pročitajte OTP iz konzole."})
        else:
            return jsonify({"status": "success", "message": "Klijent nema email! Pročitajte kod iz konzole."})
    finally:
        if conn: conn.close()

# 4. Verifikacija unesenog OTP koda
@portal_bp.route('/api/portal/auth/verify_otp/<token>', methods=['POST'])
def verify_otp(token):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if not check_portal_rate_limit(ip): abort(429)
    
    user_otp = request.json.get('otp')
    if token in portal_otps and portal_otps[token]['otp'] == user_otp and portal_otps[token]['expires'] > time.time():
        auth_key = secrets.token_hex(32)
        portal_auth_sessions[token] = auth_key
        del portal_otps[token]
        return jsonify({"status": "success", "auth_key": auth_key})
    
    return jsonify({"error": "Invalid or expired OTP"}), 401

# 5. Dohvatanje apsolutno svih podataka za Portal prikaz (Ponude, Pošiljke, Podaci, Proizvodi)
@portal_bp.route('/api/portal/data/<token>', methods=['GET'])
def get_portal_data(token):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip: ip = ip.split(',')[0].strip()
    if not check_portal_rate_limit(ip): abort(429)
    
    auth_header = request.headers.get('X-Portal-Auth')
    if not token or not auth_header or portal_auth_sessions.get(token) != auth_header: 
        return jsonify({"error": "Authentication required", "require_otp": True}), 401
        
    conn = None
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        conn.execute('PRAGMA journal_mode=WAL;')
        c = conn.cursor()
        
        c.execute("SELECT id, data FROM partners")
        partner_id = None
        partner = None
        for r in c.fetchall():
            p_data = safe_parse(r[1])
            if p_data.get('portalToken') == token:
                partner_id = r[0]
                partner = p_data
                break
                
        if not partner: return jsonify({"error": "Access Denied"}), 403
        
        safe_partner = {
            "id": partner_id, "companyName": partner.get("companyName"),
            "contactPerson": partner.get("contact", {}).get("person", ""), "kycStatus": partner.get("kyc", {}).get("status", "pending"),
            "email": partner.get("contact", {}).get("email") or partner.get("email", "")
        }
        
        c.execute("SELECT data FROM products")
        products_map = {safe_parse(r[0])['id']: safe_parse(r[0])['name'] for r in c.fetchall()}
        
        c.execute("SELECT value FROM settings WHERE key='company'")
        comp_row = c.fetchone()
        
        company_data = decrypt_data(comp_row[0]) if comp_row else {}
        company = company_data if isinstance(company_data, dict) else {}
        safe_company = { "name": company.get("name", "Aspidus DMCC"), "logoUrl": company.get("logoUrl", "") }
        
        c.execute("SELECT data FROM deals")
        safe_deals = []
        for d_row in c.fetchall():
            deal = safe_parse(d_row[0])
            if deal.get('buyerId') == partner_id:
                safe_deals.append({
                    "contractId": deal.get("contractId"), "productName": products_map.get(deal.get("productId"), "Commodity"),
                    "quantity": deal.get("quantity"), "unit": deal.get("unit"), "status": deal.get("status"), "createdAt": deal.get("createdAt"),
                    "logistics": {
                        "pol": deal.get("logistics", {}).get("pol", "TBA"), "pod": deal.get("logistics", {}).get("pod", "TBA"),
                        "vessel": deal.get("logistics", {}).get("vessel", "TBA"), "blNumber": deal.get("logistics", {}).get("blNumber", "TBA"),
                        "shipmentDate": deal.get("logistics", {}).get("shipmentDate", "TBA")
                    }
                })
            
        c.execute("SELECT data FROM offers")
        safe_offers = []
        for o_row in c.fetchall():
            off = safe_parse(o_row[0])
            if off.get('customerId') == partner_id:
                safe_offers.append({
                    "offerNo": off.get("offerNo"), "date": off.get("date"), "validUntil": off.get("validUntil"),
                    "productName": products_map.get(off.get("productId"), "Commodity"), "quantity": off.get("quantity"),
                    "unit": off.get("unit"), "price": off.get("sellingPrice"), "currency": off.get("currency"), "incoterm": off.get("incoterm")
                })
        
        # Učitavanje KYC Trezora i Robe iz specifične portal baze
        conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
        cp = conn_p.cursor()
        cp.execute("SELECT data FROM kyc_submissions WHERE partner_id=? ORDER BY submitted_at DESC LIMIT 1", (partner_id,))
        kyc_row = cp.fetchone()
        latest_kyc = decrypt_data(kyc_row[0]) if kyc_row else None
        
        cp.execute("SELECT id, data, status, created_at FROM portal_products WHERE partner_id=? ORDER BY created_at DESC", (partner_id,))
        my_products = []
        for p_row in cp.fetchall():
            my_products.append({
                "id": p_row[0],
                "data": json.loads(p_row[1]),
                "status": p_row[2],
                "created_at": p_row[3]
            })
        conn_p.close()
            
        return jsonify({
            "partner": safe_partner, 
            "company": safe_company, 
            "deals": sorted(safe_deals, key=lambda x: x['createdAt'] or '', reverse=True), 
            "offers": sorted(safe_offers, key=lambda x: x['date'] or '', reverse=True),
            "latest_kyc": latest_kyc,
            "my_products": my_products
        })
    finally:
        if conn: conn.close()

# 6. Promena E-maila partnera direktno unutar portala
@portal_bp.route('/api/portal/profile/update/<token>', methods=['POST'])
def update_portal_profile(token):
    auth_header = request.headers.get('X-Portal-Auth')
    if not token or not auth_header or portal_auth_sessions.get(token) != auth_header: abort(401)
    
    data = request.json
    new_email = data.get('email')
    if not new_email: return jsonify({"error": "Email structure missing"}), 400
    
    conn = None
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        c = conn.cursor()
        c.execute("SELECT id, data FROM partners")
        partner_id = None
        partner_record = None
        for r in c.fetchall():
            p_data = safe_parse(r[1])
            if p_data.get('portalToken') == token:
                partner_id = r[0]
                partner_record = p_data
                break
                
        if not partner_record:
            return jsonify({"error": "Partner not found"}), 404
            
        old_email = partner_record.get('contact', {}).get('email') or partner_record.get('email', 'N/A')
        
        if 'contact' not in partner_record: partner_record['contact'] = {}
        partner_record['contact']['email'] = new_email
        partner_record['email'] = new_email
        
        c.execute("UPDATE partners SET data=? WHERE id=?", (json.dumps(partner_record), partner_id))
        conn.commit()
        
        log_audit('EDIT', 'portal', f"Partner {partner_record.get('companyName')} updated contact email from {old_email} to {new_email}", is_suspicious=False)
        return jsonify({"status": "success", "message": "Email updated successfully"})
    finally:
        if conn: conn.close()

# 7. Unos nove robe sa B2B Portala u privremenu bazu (Pending)
@portal_bp.route('/api/portal/products/submit/<token>', methods=['POST'])
def submit_portal_product(token):
    auth_header = request.headers.get('X-Portal-Auth')
    if not token or not auth_header or portal_auth_sessions.get(token) != auth_header: abort(401)
    
    conn = None
    try:
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
        
        if not partner_id: abort(403)
        
        prod_data = request.json
        product_id = prod_data.get('id') or str(uuid.uuid4())
        prod_data['id'] = product_id
        
        conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
        cp = conn_p.cursor()
        created_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        
        cp.execute("INSERT OR REPLACE INTO portal_products (id, partner_id, data, status, created_at) VALUES (?, ?, ?, ?, ?)",
                   (product_id, partner_id, json.dumps(prod_data), 'pending', created_at))
        conn_p.commit()
        conn_p.close()
        
        log_audit('EDIT', 'portal', f"Partner '{company_name}' updated/submitted product variant: {prod_data.get('name')}", is_suspicious=False)
        return jsonify({"status": "success", "message": "Product securely staging for verification"})
    finally:
        if conn: conn.close()

# 8. ADMINISTRACIJA: Pregled svih proizvoda koje su uneli partneri (Učitava CRM)
@portal_bp.route('/api/portal/admin/products', methods=['GET'])
@login_required
def admin_get_portal_products():
    conn_p = None
    conn_m = None
    try:
        conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
        cp = conn_p.cursor()
        cp.execute("SELECT id, partner_id, data, status, created_at FROM portal_products ORDER BY created_at DESC")
        rows = cp.fetchall()
        
        conn_m = sqlite3.connect(DB_FILE, timeout=30.0)
        cm = conn_m.cursor()
        cm.execute("SELECT id, data FROM partners")
        partners_map = {}
        for r in cm.fetchall():
            p_data = safe_parse(r[1])
            partners_map[r[0]] = p_data.get('companyName', 'Unknown Partner')
        
        products = []
        for r in rows:
            products.append({
                "id": r[0], "partner_id": r[1], "partner_name": partners_map.get(r[1], 'Unknown Partner'),
                "data": json.loads(r[2]), "status": r[3], "created_at": r[4]
            })
        return jsonify(products)
    finally:
        if conn_p: conn_p.close()
        if conn_m: conn_m.close()

# 9. ADMINISTRACIJA: Odobravanje ili odbijanje robe (Prebacivanje u glavnu bazu)
@portal_bp.route('/api/portal/admin/products/review/<product_id>', methods=['POST'])
@login_required
def admin_review_portal_product(product_id):
    action = request.json.get('action')
    conn_p = None
    conn_m = None
    try:
        conn_p = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
        cp = conn_p.cursor()
        cp.execute("SELECT partner_id, data FROM portal_products WHERE id=?", (product_id,))
        row = cp.fetchone()
        
        if not row:
            return jsonify({"error": "Product target not found"}), 404
            
        partner_id, raw_data = row
        prod_data = json.loads(raw_data)
        
        if action == 'approve':
            cp.execute("UPDATE portal_products SET status='approved' WHERE id=?", (product_id,))
            conn_p.commit()
            
            conn_m = sqlite3.connect(DB_FILE, timeout=30.0)
            cm = conn_m.cursor()
            
            # MAGIJA POVEZIVANJA: Postavljamo podatke tako da proizvod izgleda tačno onako kako CRM očekuje
            prod_data['id'] = product_id
            prod_data['isPartnerApproved'] = True # Tagujemo da znamo da je dodato iz portala
            
            # Ubacujemo tačan ID partnera u sekciju ponude kako bi CRM znao ko prodaje ovo
            if 'supplyOffers' in prod_data and len(prod_data['supplyOffers']) > 0:
                for offer in prod_data['supplyOffers']:
                    offer['supplierId'] = partner_id
                    
            cm.execute("INSERT OR REPLACE INTO products (id, data) VALUES (?, ?)", (product_id, json.dumps(prod_data)))
            conn_m.commit()
            
            log_audit('APPROVE', 'portal', f"Admin approved custom product configuration '{prod_data.get('name')}' into central master DB", is_suspicious=False)
        else:
            cp.execute("UPDATE portal_products SET status='rejected' WHERE id=?", (product_id,))
            conn_p.commit()
            log_audit('REJECT', 'portal', f"Admin rejected product suggestion '{prod_data.get('name')}' from partner framework", is_suspicious=False)
            
        return jsonify({"status": "success", "message": f"Operation processed successfully"})
    finally:
        if conn_p: conn_p.close()
        if conn_m: conn_m.close()

# 10. Otpremanje fajlova iz portala (Sigurnosna deep-scan provera je uključena)
@portal_bp.route('/api/portal/upload/<token>', methods=['POST'])
def portal_upload(token):
    auth_header = request.headers.get('X-Portal-Auth')
    if not token or not auth_header or portal_auth_sessions.get(token) != auth_header: abort(401)
        
    if 'file' not in request.files: return jsonify({"error": "No file part"}), 400
    
    files = request.files.getlist('file')
    urls = []
    
    for file in files:
        if file.filename == '': continue
        if file and is_safe_file_content(file, file.filename):
            ext = file.filename.rsplit('.', 1)[1].lower()
            new_filename = f"kyc_{secrets.token_hex(8)}.{ext}"
            filepath = os.path.join(PORTAL_UPLOAD_FOLDER, secure_filename(new_filename))
            file.save(filepath)
            urls.append(f"/portal_uploads/{new_filename}")
        else:
            log_audit('SECURITY', 'portal', f'Malicious file payload blocked from partner upload: {file.filename}', is_suspicious=True)
            
    if not urls: return jsonify({"error": "Security Block: No valid files allowed or uploaded."}), 400
    return jsonify({"status": "success", "urls": urls})

# 11. Slanje KYC podataka u bezbednosni trezor
@portal_bp.route('/api/portal/kyc/submit/<token>', methods=['POST'])
def submit_kyc(token):
    auth_header = request.headers.get('X-Portal-Auth')
    if not token or not auth_header or portal_auth_sessions.get(token) != auth_header: abort(401)
        
    kyc_data = request.json
    partner_id = kyc_data.get('partner_id')
    if not partner_id or not token: abort(400)
    
    conn = None
    try:
        conn = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
        conn.execute('PRAGMA journal_mode=WAL;')
        c = conn.cursor()
        submission_id = str(uuid.uuid4())
        submitted_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        
        c.execute('''INSERT INTO kyc_submissions (id, partner_id, token, data, submitted_at) VALUES (?, ?, ?, ?, ?)''', 
                  (submission_id, partner_id, token, encrypt_data(kyc_data), submitted_at))
        conn.commit()
        
        log_audit('EDIT', 'portal', f"Partner configuration payload updated inside air-gapped vault", is_suspicious=False)
        return jsonify({"status": "success", "message": "KYC Data securely submitted to Vault."})
    except Exception as e: 
        return jsonify({"error": "Database error"}), 500
    finally:
        if conn: conn.close()

# 12. Prikaz KYC podataka (Iz CRM baze kad zatreba pregled)
@portal_bp.route('/api/portal/admin/submissions/<partner_id>', methods=['GET'])
@login_required
def get_kyc_submissions(partner_id):
    conn = None
    try:
        conn = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
        c = conn.cursor()
        c.execute("SELECT id, data, submitted_at FROM kyc_submissions WHERE partner_id=? ORDER BY submitted_at DESC", (partner_id,))
        subs = [{"id": r[0], "data": decrypt_data(r[1]), "submitted_at": r[2]} for r in c.fetchall()]
        return jsonify(subs)
    except Exception as e: 
        return jsonify({"error": "DB error"}), 500
    finally:
        if conn: conn.close()

# 13. Preuzimanje i slanje bezbednih upload-ovanih fajlova
@portal_bp.route('/portal_uploads/<filename>')
@login_required
def serve_portal_uploads(filename):
    safe_filename = secure_filename(filename)
    try:
        log_audit('DOWNLOAD', 'portal', f'Preuzet KYC dokument klijenta sa servera: {safe_filename}', is_suspicious=False)
        return send_from_directory(current_app.config['PORTAL_UPLOAD_FOLDER'], safe_filename)
    except Exception:
        return jsonify({"error": "Fajl ne postoji / File not found"}), 404