import json
import secrets
import time
import sqlite3
from datetime import datetime, timezone
from flask import request, jsonify, abort
from config import DB_FILE
from utils import log_audit, login_required, decrypt_data
from . import (portal_bp, safe_parse, check_portal_rate_limit,
               create_portal_otp, verify_portal_otp, find_partner_by_token,
               find_partner_by_email, pending_email_sessions, log_portal_activity)

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

        # Profesionalan mejl. Provider: Resend / SendGrid / Postmark ako je admin
        # konfigurisao u Settings > OTP Delivery; inače legacy SMTP.
        # Ako je magic-link uključen, i on ide u istom mejlu kao alternativa OTP-u.
        email_sent = False
        if client_email:
            try:
                portal_url = request.url_root.rstrip('/') + f"/portal/{token}"

                # Magic-link (opciono, konfigurabilno)
                from mail_providers import magic_link_config
                ml_cfg = magic_link_config()
                magic_url = None
                if ml_cfg['enabled']:
                    from magic_link import mint
                    ml = mint(token, ttl_minutes=ml_cfg['ttl_min'])
                    magic_url = f"{portal_url}?ml={ml}"

                from utils_email import send_portal_otp
                ok, err = send_portal_otp(
                    client_email, partner.get('companyName', ''), otp, portal_url,
                    magic_url=magic_url,
                    magic_ttl_min=ml_cfg['ttl_min'] if ml_cfg['enabled'] else 0,
                )
                if ok:
                    email_sent = True
                    log_audit('COMMUNICATION', 'portal', f'OTP sent to {client_email}' +
                              (' (with magic-link)' if magic_url else ''), is_suspicious=False)
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

    payload = request.get_json(silent=True) or {}
    user_otp = payload.get('otp')
    location = str(payload.get('location', '')).strip()
    # GPS OBAVEZAN — isti standard kao CRM login. Blokira automated bots i
    # forsira klijenta da odobri deljenje lokacije preko browsera.
    if not location or ',' not in location:
        log_audit('SECURITY', 'portal', f'Portal login blocked: missing GPS location for token {token[:8]}...', is_suspicious=True)
        return jsonify({"error": "LOCATION_REQUIRED",
                        "message": "Precise location must be shared to access the portal."}), 403

    auth_key = verify_portal_otp(token, user_otp)
    if auth_key:
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        c = conn.cursor()
        partner_id, _ = find_partner_by_token(c, token, enforce_active=False)
        conn.close()
        # log_portal_activity već ubacuje IP geo; dodajemo GPS koordinate u details.
        log_portal_activity(partner_id, 'LOGIN_SUCCESS', f'OTP login (GPS: {location})')
        return jsonify({"status": "success", "auth_key": auth_key})

    return jsonify({"error": "Invalid or expired OTP"}), 401


# ==========================================================
#  MAGIC LINK — single-click sign-in alternative to OTP
# ==========================================================

@portal_bp.route('/api/portal/auth/consume_magic/<token>', methods=['POST'])
def consume_magic_link(token):
    """Verifikuje magic-link payload i vraća auth_key kao da je klijent
    predao ispravan OTP. Payload je potpisan sa SECRET_KEY, TTL 15min
    default (konfigurisano u Settings > OTP Delivery), single-use preko
    magic_link_used_jti registra."""
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip: ip = ip.split(',')[0].strip()
    if not check_portal_rate_limit(ip): abort(429)

    payload = request.get_json(silent=True) or {}
    ml = str(payload.get('ml', '')).strip()
    location = str(payload.get('location', '')).strip()
    if not location or ',' not in location:
        return jsonify({"error": "LOCATION_REQUIRED",
                        "message": "Precise location must be shared to access the portal."}), 403

    # Osnovna partner + kill-switch provera pre magic-link verifikacije
    conn = sqlite3.connect(DB_FILE, timeout=15)
    try:
        c = conn.cursor()
        partner_id, partner = find_partner_by_token(c, token, enforce_active=False)
        if not partner:
            return jsonify({"error": "Invalid token"}), 403
        if partner.get('isPortalActive', True) is False:
            log_audit('SECURITY', 'portal', f'Blocked magic-link login for revoked portal. Partner ID: {partner_id}', is_suspicious=True)
            return jsonify({"error": "Access Revoked. Contact administrator."}), 403
    finally:
        conn.close()

    from magic_link import verify
    ok, reason = verify(ml, expected_portal_token=token, client_ip=ip)
    if not ok:
        log_audit('SECURITY', 'portal',
                  f'Magic-link login rejected ({reason}) for partner {partner_id}',
                  is_suspicious=(reason in ('bad_signature', 'token_mismatch', 'already_used')))
        return jsonify({"error": "LINK_INVALID", "reason": reason,
                        "message": {
                            'expired': 'Link has expired — please request a new code.',
                            'already_used': 'This link was already used. Request a new code to sign in.',
                            'bad_signature': 'Invalid link. Request a new code.',
                            'token_mismatch': 'Link does not match this portal. Request a new code.',
                            'invalid_format': 'Link is malformed. Request a new code.',
                        }.get(reason, 'Link cannot be verified.')}), 401

    # Isti auth_key kao posle uspešnog OTP-a
    from . import portal_auth_sessions
    import secrets as _sec
    auth_key = _sec.token_urlsafe(32)
    portal_auth_sessions[token] = {'auth_key': auth_key, 'issued_at': datetime.now(timezone.utc).timestamp()}
    log_portal_activity(partner_id, 'LOGIN_SUCCESS', f'Magic-link login (GPS: {location})')
    return jsonify({"status": "success", "auth_key": auth_key})


# ==========================================================
#  EMAIL-BASED LOGIN (no token URL needed)
# ==========================================================

@portal_bp.route('/api/portal/auth/login', methods=['POST'])
def portal_login_request():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip: ip = ip.split(',')[0].strip()
    if not check_portal_rate_limit(ip): abort(429)

    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()

    if not email or '@' not in email or '.' not in email:
        return jsonify({"error": "Please enter a valid email address."}), 400

    partner_id, partner = find_partner_by_email(email)

    # BEZBEDNOST: uvek vraćamo istu poruku bez obzira da li je nalog nađen,
    # blokiran ili nema tokena. Ranije su različiti error kodovi (404 vs 403
    # vs 200) omogućavali napadaču da enumeracijom otkrije koje su email
    # adrese registrovane u sistemu. Sve interne razlike se i dalje beleže
    # u audit trag za analitiku, ali klijent vidi jedinstvenu poruku.
    generic_msg = "If this email is registered, a verification code has been sent."
    session_id = secrets.token_hex(16)

    if not partner:
        log_portal_activity(None, 'LOGIN_FAILED', f'Unregistered email attempt: {email}')
        log_audit('SECURITY', 'portal', f'Portal login attempt with unregistered email: {email}', is_suspicious=True)
        return jsonify({"status": "success", "session_id": session_id, "message": generic_msg})

    if partner.get('isPortalActive', True) is False:
        log_portal_activity(partner_id, 'LOGIN_BLOCKED', f'Portal access revoked for {email}')
        log_audit('SECURITY', 'portal', f'Login attempt on revoked portal for {email}', is_suspicious=True)
        return jsonify({"status": "success", "session_id": session_id, "message": generic_msg})

    token = partner.get('portalToken')
    if not token:
        log_audit('SECURITY', 'portal', f'Portal login for {email} but no token configured', is_suspicious=True)
        return jsonify({"status": "success", "session_id": session_id, "message": generic_msg})

    otp = create_portal_otp(token)

    email_sent = False
    try:
        from utils_email import send_portal_otp
        portal_url = request.url_root.rstrip('/') + f"/portal/{token}"
        ok, err = send_portal_otp(email, partner.get('companyName', ''), otp, portal_url)
        if ok:
            email_sent = True
            log_portal_activity(partner_id, 'OTP_SENT', f'OTP sent to {email} via email login')
    except Exception:
        pass

    # Ispis OTP u konzolu je moguć samo u dev/no-SMTP okruženju i pomaže admin dijagnostici;
    # u produkciji SMTP mora biti podešen i tada se OTP ne štampa u log.
    if not email_sent:
        print(f"\n[DEV] B2B PORTAL OTP: {otp} (For: {partner.get('companyName')})\n")

    pending_email_sessions[session_id] = {
        'token': token, 'partner_id': partner_id, 'email': email,
        'expires': time.time() + 300
    }

    return jsonify({"status": "success", "session_id": session_id, "message": generic_msg})


@portal_bp.route('/api/portal/auth/login/verify', methods=['POST'])
def portal_login_verify():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip: ip = ip.split(',')[0].strip()
    if not check_portal_rate_limit(ip): abort(429)

    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    user_otp = data.get('otp')
    location = str(data.get('location', '')).strip()
    if not location or ',' not in location:
        log_audit('SECURITY', 'portal', 'Portal login blocked: missing GPS location', is_suspicious=True)
        return jsonify({"error": "LOCATION_REQUIRED",
                        "message": "Precise location must be shared to access the portal."}), 403

    pending = pending_email_sessions.get(session_id)
    if not pending or pending.get('expires', 0) < time.time():
        pending_email_sessions.pop(session_id, None)
        return jsonify({"error": "Session expired. Please request a new code."}), 401

    token = pending['token']
    auth_key = verify_portal_otp(token, user_otp)
    if auth_key:
        pending_email_sessions.pop(session_id, None)
        log_portal_activity(pending['partner_id'], 'LOGIN_SUCCESS', f'Email login from {pending["email"]} (GPS: {location})')
        log_audit('LOGIN', 'portal', f'Portal email login: {pending["email"]} (GPS: {location})', is_suspicious=False)
        return jsonify({"status": "success", "auth_key": auth_key, "token": token})

    return jsonify({"error": "Invalid or expired verification code."}), 401


# ==========================================================
#  TEST-ONLY HOOK — omogućava automatizovanim E2E testovima da
#  pročitaju trenutni OTP bez SMTP-a. Aktivan SAMO kada je env
#  TEST_MODE=1 postavljen; u produkciji vraća 404 (kao da endpointa nema).
# ==========================================================
import os as _os_test
@portal_bp.route('/api/portal/testonly/last_otp/<token>', methods=['GET'])
def testonly_last_otp(token):
    if _os_test.getenv('TEST_MODE', '0') != '1':
        return jsonify({"error": "not_found"}), 404
    from . import portal_otps
    rec = portal_otps.get(token)
    if not rec:
        return jsonify({"otp": None}), 200
    return jsonify({"otp": rec.get('otp')}), 200


@portal_bp.route('/api/portal/testonly/last_email_session_otp', methods=['GET'])
def testonly_last_email_session_otp():
    """Vraća OTP za poslednju pending email-sesiju (email flow ne koristi token)."""
    if _os_test.getenv('TEST_MODE', '0') != '1':
        return jsonify({"error": "not_found"}), 404
    email = (request.args.get('email') or '').strip().lower()
    for sid, rec in list(pending_email_sessions.items()):
        if rec.get('email', '').lower() == email:
            return jsonify({"session_id": sid, "otp": rec.get('otp')})
    return jsonify({"otp": None}), 200
