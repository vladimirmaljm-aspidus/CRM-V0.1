import json
import secrets
import time
import sqlite3
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
        is_new_token = False
        if 'portalToken' not in partner or not partner['portalToken']:
            partner['portalToken'] = secrets.token_urlsafe(32)
            partner['isPortalActive'] = True
            is_new_token = True
            c.execute('UPDATE partners SET data=? WHERE id=?', (json.dumps(partner), partner_id))
            conn.commit()
            action_log = ('EDIT', 'partners', f'Generated secure B2B portal token for partner ID: {partner_id}', False)

        token = partner['portalToken']
        portal_url = request.url_root.rstrip('/') + f"/portal/{token}"

        if is_new_token:
            client_email = partner.get('contact', {}).get('email') or partner.get('email')
            if client_email:
                try:
                    from utils_email import send_portal_welcome
                    send_portal_welcome(client_email, partner.get('companyName', ''), portal_url)
                    log_audit('COMMUNICATION', 'portal', f'Welcome email sent to {client_email} for partner {partner_id}', is_suspicious=False)
                except Exception as e:
                    log_audit('ERROR', 'portal', f'Failed to send welcome email: {e}', is_suspicious=False)

        return jsonify({"status": "success", "token": token, "isPortalActive": partner.get('isPortalActive', True)})
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

        # Profesionalan mejl (HTML šablon sa logom firme + confidentiality footer).
        email_sent = False
        if client_email:
            try:
                from utils_email import send_portal_otp
                portal_url = request.url_root.rstrip('/') + f"/portal/{token}"
                ok, err = send_portal_otp(client_email, partner.get('companyName', ''), otp, portal_url)
                if ok:
                    email_sent = True
                    log_audit('COMMUNICATION', 'portal', f'OTP sent via professional email to {client_email}', is_suspicious=False)
                else:
                    log_audit('ERROR', 'portal', f'OTP email send failed to {client_email}: {err}', is_suspicious=False)
            except Exception as e:
                log_audit('ERROR', 'portal', f'OTP email send exception: {e}', is_suspicious=False)

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
