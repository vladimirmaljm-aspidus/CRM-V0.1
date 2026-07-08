import json
import secrets
import time
import smtplib
import sqlite3
from email.mime.text import MIMEText
from flask import request, jsonify, abort
from config import DB_FILE
from utils import log_audit, login_required, decrypt_data
from . import portal_bp, portal_otps, portal_auth_sessions, safe_parse, check_portal_rate_limit

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
            partner['isPortalActive'] = True # DODATO: Inicijalni status za Kill Switch
            c.execute('UPDATE partners SET data=? WHERE id=?', (json.dumps(partner), partner_id))
            conn.commit()
            action_log = ('EDIT', 'partners', f'Generated secure B2B portal token for partner ID: {partner_id}', False)
            
        return jsonify({"status": "success", "token": partner['portalToken']})
    finally:
        if conn: conn.close()
        if action_log:
            log_audit(action_log[0], action_log[1], action_log[2], is_suspicious=action_log[3])

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
            
        # DODATO: Kill Switch provera
        if partner.get('isPortalActive', True) is False:
            log_audit('SECURITY', 'portal', f'Blocked OTP request for revoked portal access. Partner ID: {partner.get("id", "Unknown")}', is_suspicious=True)
            return jsonify({"error": "Access Revoked. Please contact administrator."}), 403
            
        client_email = partner.get('contact', {}).get('email') or partner.get('email')
        otp = str(secrets.randbelow(900000) + 100000)
        portal_otps[token] = {'otp': otp, 'expires': time.time() + 300} 
        
        email_sent = False
        c.execute("SELECT value FROM settings WHERE key='comms_settings'")
        smtp_row = c.fetchone()
        
        if smtp_row and client_email:
            settings = decrypt_data(smtp_row[0]) 
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