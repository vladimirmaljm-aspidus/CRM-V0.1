import sqlite3
import json
from flask import request, jsonify, abort, render_template
from config import DB_FILE, PORTAL_DB_FILE
from utils import decrypt_data, log_audit
from . import portal_bp, portal_auth_sessions, safe_parse, check_portal_rate_limit

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
        
        # DODATO: Kill Switch provera
        if partner.get('isPortalActive', True) is False:
            log_audit('SECURITY', 'portal', f'Blocked data access for revoked portal. Partner ID: {partner_id}', is_suspicious=True)
            return jsonify({"error": "Access Revoked"}), 403
        
        # SYSTEM PERMISSIONS: Dozvoljavamo pristup i novom tabu za dokumente
        permissions = partner.get("portalPermissions", ["shipments", "offers", "kyc", "goods", "profile", "rfq", "documents"])
        
        safe_partner = {
            "id": partner_id, 
            "companyName": partner.get("companyName"),
            "contactPerson": partner.get("contact", {}).get("person", ""), 
            "kycStatus": partner.get("kyc", {}).get("status", "pending"),
            "email": partner.get("contact", {}).get("email") or partner.get("email", ""),
            "permissions": permissions
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
            
        c.execute("SELECT data FROM offers")
        safe_offers = []
        for o_row in c.fetchall():
            off = safe_parse(o_row[0])
            if off.get('customerId') == partner_id:
                safe_offers.append({
                    "id": off.get("id"),
                    "offerNo": off.get("offerNo"), 
                    "date": off.get("date"), 
                    "validUntil": off.get("validUntil"),
                    "productName": products_map.get(off.get("productId"), "Commodity"), 
                    "quantity": off.get("quantity"),
                    "unit": off.get("unit"), 
                    "price": off.get("sellingPrice") or off.get("price"), 
                    "currency": off.get("currency"), 
                    "incoterm": off.get("incoterm")
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
        
        conn_p.close()
            
        return jsonify({
            "partner": safe_partner, 
            "company": safe_company, 
            "deals": sorted(safe_deals, key=lambda x: x.get('createdAt') or '', reverse=True), 
            "offers": sorted(safe_offers, key=lambda x: x.get('date') or '', reverse=True),
            "my_demands": sorted(my_demands, key=lambda x: x.get('date') or '', reverse=True),
            "documents": documents,
            "latest_kyc": latest_kyc,
            "my_products": my_products
        })
    finally:
        if conn: conn.close()

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
        partner_id, partner_record = None, None
        for r in c.fetchall():
            p_data = safe_parse(r[1])
            if p_data.get('portalToken') == token:
                partner_id, partner_record = r[0], p_data
                break
                
        if not partner_record: return jsonify({"error": "Partner not found"}), 404
            
        old_email = partner_record.get('contact', {}).get('email') or partner_record.get('email', 'N/A')
        if 'contact' not in partner_record: partner_record['contact'] = {}
        partner_record['contact']['email'] = partner_record['email'] = new_email
        
        c.execute("UPDATE partners SET data=? WHERE id=?", (json.dumps(partner_record), partner_id))
        conn.commit()
        log_audit('EDIT', 'portal', f"Partner updated contact email from {old_email} to {new_email}", is_suspicious=False)
        return jsonify({"status": "success", "message": "Profile updated successfully"})
    finally:
        if conn: conn.close()