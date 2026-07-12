import json
import secrets
import time
import smtplib
import sqlite3
from email.mime.text import MIMEText
from flask import request, jsonify, abort
from config import DB_FILE
from utils import log_audit, login_required, decrypt_data
from . import (portal_bp, safe_parse, check_portal_rate_limit,
               create_portal_otp, verify_portal_otp, find_partner_by_token)

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
            partner['isPortalActive'] = True # Inicijalni status za Kill Switch
            c.execute('UPDATE partners SET data=? WHERE id=?', (json.dumps(partner), partner_id))
            conn.commit()
            action_log = ('EDIT', 'partners', f'Generated secure B2B portal token for partner ID: {partner_id}', False)

        return jsonify({"status": "success", "token": partner['portalToken'], "isPortalActive": partner.get('isPortalActive', True)})
    finally:
        if conn: conn.close()
        if action_log:
            log_audit(action_log[0], action_log[1], action_log[2], is_suspicious=action_log[3])


# ==========================================================
#  KILL SWITCH: opoziv / ponovno aktiviranje pristupa portalu
# ==========================================================
@portal_bp.route('/api/portal/access/<partner_id>', methods=['POST'])
@login_required
def set_portal_access(partner_id):
    """Admin/ovlašćeni korisnik uključuje ili isključuje pristup partnera portalu.
    Kada je isključen, svi postojeći tokeni/sesije prestaju da rade (Kill Switch)."""
    from flask import session
    role = session.get('role')
    if role != 'admin':
        # Provera 'partners_edit' permisije za ne-admin korisnike
        conn_p = sqlite3.connect(DB_FILE, timeout=30.0)
        try:
            cp = conn_p.cursor()
            cp.execute('SELECT permissions FROM users WHERE id=?', (session['user_id'],))
            prow = cp.fetchone()
        finally:
            conn_p.close()
        perms = decrypt_data(prow[0]) if prow and prow[0] else {}
        if not perms.get('partners_edit', False):
            log_audit('SECURITY', 'portal', 'Prevented unauthorized portal access toggle', is_suspicious=True)
            return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json(silent=True) or {}
    active = bool(data.get('active', False))

    conn = None
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA busy_timeout=30000;')
        c = conn.cursor()
        c.execute('SELECT data FROM partners WHERE id=?', (partner_id,))
        row = c.fetchone()
        if not row:
            return jsonify({"error": "Partner not found"}), 404
        partner = safe_parse(row[0])
        partner['isPortalActive'] = active

        # Ako opozivamo pristup, odmah gasimo aktivne memorijske sesije/OTP za taj token.
        token = partner.get('portalToken')
        if not active and token:
            from . import portal_auth_sessions, portal_otps
            portal_auth_sessions.pop(token, None)
            portal_otps.pop(token, None)

        c.execute('UPDATE partners SET data=? WHERE id=?', (json.dumps(partner), partner_id))
        conn.commit()
    finally:
        if conn: conn.close()

    log_audit('SECURITY', 'portal',
              f"Portal access {'ENABLED' if active else 'REVOKED'} for partner ID: {partner_id}",
              is_suspicious=(not active))
    return jsonify({"status": "success", "isPortalActive": active})


@portal_bp.route('/api/portal/auth/send_otp/<token>', methods=['POST'])
def send_otp(token):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if not check_portal_rate_limit(ip): abort(429)

    conn = None
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        conn.execute('PRAGMA journal_mode=WAL;')
        c = conn.cursor()

        # Nađi partnera bez kill-switch filtera da bismo mogli da logujemo opoziv.
        partner_id, partner = find_partner_by_token(c, token, enforce_active=False)
        if not partner:
            return jsonify({"error": "Invalid token"}), 403

        # Kill Switch provera
        if partner.get('isPortalActive', True) is False:
            log_audit('SECURITY', 'portal', f'Blocked OTP request for revoked portal access. Partner ID: {partner_id}', is_suspicious=True)
            return jsonify({"error": "Access Revoked. Please contact administrator."}), 403

        client_email = partner.get('contact', {}).get('email') or partner.get('email')
        otp = create_portal_otp(token)

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

    user_otp = (request.get_json(silent=True) or {}).get('otp')
    auth_key = verify_portal_otp(token, user_otp)
    if auth_key:
        return jsonify({"status": "success", "auth_key": auth_key})

    return jsonify({"error": "Invalid or expired OTP"}), 401
