import datetime
import json
import sqlite3
import re
import logging
from flask import Blueprint, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
from config import DB_FILE
from utils import log_audit, login_required, FirewallCache, bump_user_token_version, get_user_token_version, get_ip_info
from totp import (generate_secret, totp_verify, provisioning_uri,
                  generate_recovery_codes, verify_recovery_code)

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)

def is_strong_password(password):
    """Vojni standard: Min 12 karaktera, veliko + malo slovo, broj i specijalni znak.
    Ovim se pooštrava ranija provera (10 char + uppercase + broj) koja je puštala
    npr. 'Password12' — trivijalnu za rečničke napade."""
    if not isinstance(password, str): return False
    if len(password) < 12: return False
    if len(password) > 200: return False
    if not re.search(r"[A-Z]", password): return False
    if not re.search(r"[a-z]", password): return False
    if not re.search(r"[0-9]", password): return False
    if not re.search(r"[^A-Za-z0-9]", password): return False
    return True

@auth_bp.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "MALFORMED_REQUEST"}), 400
        
    username = data.get('username')
    password = data.get('password')
    location = data.get('location', '')
    device_info = data.get('device', 'UNKNOWN_DEVICE')
    totp_code = str(data.get('totp_code', '')).strip()
    recovery_code = str(data.get('recovery_code', '')).strip()
    
    # 1. STRIKTNA KONTROLA LOKACIJE
    if not location or ',' not in location:
        log_audit('SECURITY', 'system', f'Failed login, missing or empty GPS location. User: {username}', is_suspicious=True, location='DENIED')
        return jsonify({"error": "LOCATION_REQUIRED"}), 403
    
    # 2. Provera da li je IP blokiran
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if client_ip and ',' in client_ip: 
        client_ip = client_ip.split(',')[0].strip()
        
    if client_ip in FirewallCache.blacklist:
        log_audit('SECURITY', 'system', f'Blocked Blacklisted IP Attempt: {client_ip}. Device: {device_info}', is_suspicious=True, location=location)
        return jsonify({"error": "AUTH_ERROR"}), 401
    
    # 3. Ucitaj user-a DIREKTNO IZ SUPABASE (V24.0 — bez SQLite fallback-a)
    user = None
    try:
        import supabase_store as store
        user = store.get_user_by_username(username)
    except Exception as e:
        logger.error(f"LOGIN Supabase read failed for '{username}': {e}", exc_info=True)
        log_audit('CRITICAL_ERROR', 'system', f'Login failed — Supabase read: {e}',
                  is_suspicious=True, location=location)
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500

    # ROUND F: account-level lockout — nezavisno od IP blacklist-a.
    if user and user.get('locked_until'):
        try:
            _until = datetime.datetime.fromisoformat(str(user['locked_until']).replace('Z', '+00:00'))
            _now_dt = datetime.datetime.now(datetime.timezone.utc)
            if _until > _now_dt:
                remaining = int((_until - _now_dt).total_seconds())
                log_audit('SECURITY', 'system', f'Blocked login attempt on locked account: {username}',
                          is_suspicious=True, location=location)
                return jsonify({"error": "ACCOUNT_LOCKED", "retry_after_seconds": remaining}), 423
        except Exception:
            pass

    # Dijagnostika (samo u server log): razlog neuspeha, bez otkrivanja klijentu.
    if not user:
        logger.info(f"LOGIN: unknown username '{username}' from IP {client_ip}")
    elif not (user.get('password') and check_password_hash(user['password'], password)):
        logger.info(f"LOGIN: wrong password for '{username}' from IP {client_ip}")

    # 4. Uspešna provera lozinke
    if user and user.get('password') and check_password_hash(user['password'], password):
        # 4a. 2FA gate
        totp_secret_db = user.get('totp_secret')
        totp_enabled_db = 1 if user.get('totp_enabled') else 0
        totp_recovery_db = user.get('totp_recovery')
        if totp_enabled_db and totp_secret_db:
            if not totp_code and not recovery_code:
                log_audit('LOGIN', 'system', f'Password OK, waiting for 2FA code: {username}', location=location)
                return jsonify({"status": "totp_required",
                                "message": "Enter the 6-digit code from your Authenticator app, or a recovery code."}), 200
            ok = False
            if recovery_code:
                try:
                    recovery_list = json.loads(totp_recovery_db) if totp_recovery_db else []
                except Exception:
                    recovery_list = []
                matched, remaining = verify_recovery_code(recovery_list, recovery_code)
                if matched:
                    ok = True
                    try:
                        import supabase_store as _st
                        from data_layer import update as _upd
                        _upd('users', {'id': user['id']}, {'totp_recovery': json.dumps(remaining)})
                        log_audit('SECURITY', 'system',
                                  f'2FA login via recovery code (one used): {username}. Remaining: {len(remaining)}',
                                  is_suspicious=True, location=location)
                    except Exception:
                        logger.warning('recovery code update failed', exc_info=True)
            if not ok and totp_code:
                ok = totp_verify(totp_secret_db, totp_code)
            if not ok:
                log_audit('SECURITY', 'system', f'2FA failed: {username}', is_suspicious=True, location=location)
                return jsonify({"error": "TOTP_INVALID",
                                "message": "Invalid 2FA code. Try again or use a recovery code."}), 401
        session.permanent = True
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['role'] = user.get('role') or 'employee'
        session['login_time'] = datetime.datetime.now(datetime.timezone.utc).timestamp()

        session['login_ip'] = client_ip
        session['login_ua'] = request.user_agent.string if request.user_agent else "Unknown"
        session['login_ua_family'] = f"{request.user_agent.browser or ''}|{request.user_agent.platform or ''}"
        # Snimi aktuelnu token_version u sesiju; promena lozinke uveća broj u bazi
        # i sve stare sesije padnu na prvoj sledećoj zaštićenoj ruti.
        session['token_version'] = int(user.get('token_version', 1) or 1)

        if client_ip in FirewallCache.login_attempts:
            del FirewallCache.login_attempts[client_ip]

        # ANOMALY DETEKCIJA: iznenadna prijava iz druge zemlje. V24.0 — Supabase direktno.
        try:
            _, ip_location, _tz = get_ip_info(client_ip) if client_ip else ('', '', '')
            prev_country = (user.get('last_login_country') or '').strip()
            new_country = ''
            for _piece in [location, ip_location]:
                if _piece and ',' in _piece:
                    new_country = _piece.split(',')[-1].strip()
                    if new_country: break
            if prev_country and new_country and prev_country != new_country:
                log_audit('SECURITY', 'system',
                          f'ANOMALY: user {username} logged in from {new_country} — previous session was {prev_country}',
                          is_suspicious=True, location=location)
            if new_country:
                from data_layer import update as _upd
                _upd('users', {'id': user['id']}, {'last_login_country': new_country})
        except Exception:
            logger.warning('anomaly detection failed', exc_info=True)

        # ROUND F: per-session tracking + known-IP notification (best-effort)
        try:
            from routes.security_center import (_create_session_row, record_login_ip,
                                                send_new_ip_alert)
            session['session_id'] = _create_session_row(user['id'])
            if client_ip:
                is_new_ip = record_login_ip(user['id'], client_ip)
                if is_new_ip:
                    send_new_ip_alert(user['id'], client_ip)
        except Exception:
            logger.warning('session_row/known_ip tracking failed', exc_info=True)

        must_change = bool(user.get('must_change_password'))
        permissions = user.get('permissions') or {}
        if isinstance(permissions, str):
            try: permissions = json.loads(permissions)
            except Exception: permissions = {}

        full_details = f"Successful login. Device: {device_info}"
        log_audit('LOGIN', 'system', full_details, location=location)
        return jsonify({
            "status": "success",
            "user": {
                "id": user['id'], "username": user['username'],
                "role": user.get('role') or 'employee',
                "permissions": permissions,
                "signature": user.get('signature'),
            },
            "must_change_password": must_change,
        })
    
    # 5. Neuspešna prijava - beleženje pokušaja
    if client_ip not in FirewallCache.login_attempts:
        FirewallCache.login_attempts[client_ip] = []
    FirewallCache.login_attempts[client_ip].append(datetime.datetime.now(datetime.timezone.utc).timestamp())

    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    FirewallCache.login_attempts[client_ip] = [t for t in FirewallCache.login_attempts[client_ip] if now - t < 300]

    if len(FirewallCache.login_attempts[client_ip]) >= FirewallCache.settings.get('max_login', 10):
        FirewallCache.blacklist.add(client_ip)
        log_audit('SECURITY', 'firewall', f"Auto-blacklisted IP {client_ip} due to brutal force attempts.", is_suspicious=True, location=location)

    # ROUND F: account-level lockout — ako je user postojao (samo pogresna lozinka),
    # trag brojimo per-user. Nakon N neuspelih pokusaja (bilo iz kog IP-a) zakljucaj
    # nalog na M minuta. Admin i sam korisnik (magic-link) mogu da otkljucaju.
    if user:
        try:
            from routes.security_center import _get_policy
            policy = _get_policy()
            max_attempts = int(policy.get('max_login_attempts', 10))
            lockout_min = int(policy.get('lockout_minutes', 15))
            # Count recent failed logins iz audit_logs za ovog user-a (poslednjih 15 min)
            # V24.0: count failed attempts u Supabase audit_logs
            cutoff = (datetime.datetime.now(datetime.timezone.utc)
                      - datetime.timedelta(minutes=lockout_min)).isoformat().replace('+00:00', 'Z')
            _fail_count = 0
            try:
                from data_layer import select as _ds
                _all = _ds('audit_logs',
                           filters={'action': 'SECURITY', 'timestamp': ('gte', cutoff)},
                           limit=200) or []
                _pattern = f'Failed login attempt: {username}.'
                _fail_count = sum(1 for r in _all if _pattern in (r.get('details') or ''))
            except Exception:
                _fail_count = 0
            if _fail_count + 1 >= max_attempts:
                until_iso = (datetime.datetime.now(datetime.timezone.utc)
                             + datetime.timedelta(minutes=lockout_min)).isoformat().replace('+00:00', 'Z')
                try:
                    from data_layer import update as _upd
                    _upd('users', {'id': user['id']}, {'locked_until': until_iso})
                except Exception:
                    pass
                log_audit('SECURITY', 'system',
                          f'Auto-locked account {username} for {lockout_min}min after {_fail_count+1} failed attempts',
                          is_suspicious=True, location=location)
                # Emailuj user-a sa unlock magic-link-om (best-effort)
                try:
                    _email = user.get('email')
                    if _email:
                        from routes.security_center import _hash_token, _now_iso
                        import secrets as _sec
                        tok = _sec.token_urlsafe(48)
                        expires = (datetime.datetime.now(datetime.timezone.utc)
                                   + datetime.timedelta(hours=2)).isoformat().replace('+00:00', 'Z')
                        from data_layer import insert as _ins
                        try:
                            _ins('magic_login_tokens', {
                                'token': _hash_token(tok), 'user_id': user['id'],
                                'purpose': 'unlock', 'created_at': _now_iso(),
                                'expires_at': expires, 'request_ip': client_ip,
                            })
                        except Exception:
                            pass
                        base = request.host_url.rstrip('/')
                        link = f"{base}/login/magic?t={tok}"
                        from utils_email import send_email_now
                        send_email_now(
                            _email, 'Aspidus — Account locked, unlock link',
                            f"<p>Hi {username},</p>"
                            f"<p>Your account was temporarily locked after {_fail_count+1} failed sign-in attempts. "
                            f"It will unlock automatically in {lockout_min} minutes.</p>"
                            f"<p>To unlock immediately, click: <a href=\"{link}\">Unlock my account</a> (valid 2h).</p>"
                            f"<p>If this wasn't you, someone is trying to guess your password — change it after signing in.</p>",
                            body_type='html'
                        )
                except Exception:
                    pass
                return jsonify({"error": "ACCOUNT_LOCKED",
                                "retry_after_seconds": lockout_min * 60,
                                "message": "Too many failed attempts. Check your email for an unlock link."}), 423
        except Exception:
            pass

    log_audit('SECURITY', 'system', f'Failed login attempt: {username}. Device: {device_info}', is_suspicious=True, location=location)
    return jsonify({"error": "AUTH_ERROR"}), 401

@auth_bp.route('/api/auth/logout', methods=['POST'])
def logout():
    if 'login_time' in session:
        duration_seconds = int(datetime.datetime.now(datetime.timezone.utc).timestamp() - session['login_time'])
        h, remainder = divmod(duration_seconds, 3600)
        m, s = divmod(remainder, 60)
        log_audit('LOGOUT', 'system', f'Logout successful. Session duration: {h}h {m}m {s}s | Total seconds: {duration_seconds}')
    session.clear()
    return jsonify({"status": "success"})

@auth_bp.route('/api/auth/me', methods=['GET'])
def me():
    if 'user_id' in session:
        import supabase_store as store
        u = store.get_user_by_id(session['user_id']) or {}
        perms = u.get('permissions') or {}
        if isinstance(perms, str):
            try: perms = json.loads(perms)
            except Exception: perms = {}
        notif = u.get('notif_prefs') or {}
        if isinstance(notif, str):
            try: notif = json.loads(notif)
            except Exception: notif = {}
        return jsonify({
            "user": {
                "id": session['user_id'],
                "username": session['username'],
                "role": session['role'],
                "permissions": perms,
                "signature": u.get('signature'),
                "full_name": u.get('full_name') or '',
                "email":     u.get('email') or '',
                "phone":     u.get('phone') or '',
                "notif_prefs": notif,
            }
        })
    return jsonify({"error": "UNAUTHORIZED"}), 401

@auth_bp.route('/api/auth/change_password', methods=['POST'])
@login_required
def change_password():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "MALFORMED_REQUEST"}), 400
        
    new_password = data.get('new_password')
    if not new_password: 
        return jsonify({"error": "EMPTY_PASSWORD"}), 400
    
    if not is_strong_password(new_password):
        return jsonify({"error": "WEAK_PASSWORD"}), 400

    # HIBP breach check — odbija lozinke poznate iz curenja podataka. K-anonymity:
    # samo prvih 5 char SHA-1 hasha ide na haveibeenpwned.com; puna lozinka ne
    # napušta server. Ako je HIBP servis dole, propuštamo (fail-open).
    try:
        from security_ext import is_password_pwned
        pwned, hits = is_password_pwned(new_password, min_hits=1)
        if pwned:
            log_audit('SECURITY', 'users',
                      f'Password change blocked — new password found in {hits} known breaches. User: {session.get("username","?")}',
                      is_suspicious=True)
            return jsonify({
                "error": "PWNED_PASSWORD",
                "message": f"This password appears in {hits} known data breaches. Please choose a different one.",
                "hits": hits,
            }), 400
    except Exception:
        pass

    # ROUND F: password history — zabrani reuse poslednjih N lozinki
    try:
        from routes.security_center import check_password_reuse, add_password_history
        if check_password_reuse(session['user_id'], new_password):
            return jsonify({
                "error": "PASSWORD_REUSED",
                "message": "This password matches one you've used recently. Please choose a different one.",
            }), 400
    except Exception:
        pass

    try:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
        pw_hash = generate_password_hash(new_password, method='scrypt:32768:8:1')
        # V24.0: direktno u Supabase, bez SQLite
        import supabase_store as _store
        _store.update_user_password(session['user_id'], pw_hash, now_iso)
        try:
            add_password_history(session['user_id'], pw_hash)
        except Exception:
            pass
    except Exception:
        logger.error('change_password failed', exc_info=True)
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500

    # Invalidate SVE prethodne sesije (uključujući trenutnu) — korisnik mora ponovo
    # da se prijavi novom lozinkom. Ovim se hvataju napadi "kradja sesijskog cookie-a
    # pa promena lozinke ostaje trajna" — stari cookie odmah prestaje da radi.
    bump_user_token_version(session['user_id'])
    session.clear()

    log_audit('EDIT', 'users', 'User successfully changed their own password. All sessions invalidated.')
    return jsonify({"status": "success", "message": "Password changed. Please log in again."})


@auth_bp.route('/api/auth/logout_all', methods=['POST'])
@login_required
def logout_all_sessions():
    """Admin ili sam korisnik može da izbaci sve sesije za dati user_id.
    Ako user_id nije prosleđen, primenjuje se na sebe."""
    payload = request.get_json(silent=True) or {}
    target_id = (payload.get('user_id') or session['user_id']).strip()
    if target_id != session['user_id'] and session.get('role') != 'admin':
        log_audit('SECURITY', 'users', f'Prevented unauthorized logout_all for {target_id}', is_suspicious=True)
        return jsonify({"error": "Unauthorized"}), 403
    bump_user_token_version(target_id)
    log_audit('SECURITY', 'users', f'All sessions invalidated for user {target_id}', is_suspicious=False)
    if target_id == session['user_id']:
        session.clear()
    return jsonify({"status": "success"})

@auth_bp.route('/api/auth/signature', methods=['POST'])
@login_required
def set_signature():
    """Postavlja/uklanja LIČNI potpis trenutno ulogovanog korisnika.
    Svaki korisnik može da menja isključivo svoj potpis (izvodi se iz sesije),
    čime se garantuje da na dokumentima može stajati samo sopstveni potpis."""
    data = request.get_json(silent=True) or {}
    sig = data.get('signatureUrl')

    # Dozvoljena je samo interna putanja do uploadovanog fajla (ne proizvoljan URL),
    # ili prazna vrednost (uklanjanje potpisa).
    if sig not in (None, ''):
        sig = str(sig).strip()
        if not sig.startswith('/uploads/') or '..' in sig or len(sig) > 256:
            return jsonify({"error": "INVALID_SIGNATURE_PATH"}), 400
    else:
        sig = None

    try:
        # V24.0: direktno u Supabase
        from data_layer import update as _upd
        _upd('users', {'id': session['user_id']}, {'signature': sig})
    except Exception:
        logger.error('set_signature failed', exc_info=True)
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500

    log_audit('EDIT', 'users', 'User updated their personal signature.' if sig else 'User removed their personal signature.')
    return jsonify({"status": "success", "signature": sig})


# ==========================================================
#  2FA / TOTP endpoints
# ==========================================================

@auth_bp.route('/api/auth/totp/setup_start', methods=['POST'])
@login_required
def totp_setup_start():
    """Korak 1: generiše novi TOTP secret i vraća ga korisniku sa provisioning
    URI-jem za QR skeniranje. Secret se NE UPISUJE u bazu odmah — prvo mora da
    korisnik potvrdi da može da generiše validan kod iz svog Authenticator app-a
    preko /totp/setup_confirm. Time se sprečava da korisnik izgubi pristup jer
    je skenirao QR pa app pomerio ekran pre nego što je proverio da radi."""
    uid = session['user_id']
    username = session.get('username', 'user')
    # Ako korisnik već ima uključen TOTP, prvo mora da ga isključi
    with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
        c = conn.cursor()
        c.execute("SELECT totp_enabled FROM users WHERE id=?", (uid,))
        row = c.fetchone()
        if row and int(row[0] or 0) == 1:
            return jsonify({"error": "ALREADY_ENABLED",
                            "message": "2FA is already enabled. Disable it first if you want to re-enroll."}), 400

    secret = generate_secret()
    # Issuer name — vidi se u Authenticator app-u pored korisničkog imena
    issuer = 'Aspidus CRM'
    uri = provisioning_uri(secret, username, issuer)
    # Vratimo secret klijentu — on ga privremeno čuva u memoriji dok završava setup.
    # Baza NE PAMTI ovaj secret dok korisnik ne potvrdi u sledećem koraku.
    log_audit('SECURITY', 'auth', f'2FA setup started for {username}', is_suspicious=False)
    return jsonify({
        "status": "success",
        "secret": secret,
        "provisioning_uri": uri,
        "issuer": issuer,
        "account": username,
    })


@auth_bp.route('/api/auth/totp/setup_confirm', methods=['POST'])
@login_required
def totp_setup_confirm():
    """Korak 2: korisnik unese kod iz svog Authenticator app-a + secret koji je
    dobio u prethodnom koraku. Ako se kod poklopi, upisujemo secret u bazu i
    generišemo 8 recovery kodova. Recovery kodovi se vraćaju SAMO OVDE (jednom),
    plain-text, i korisnik mora da ih sačuva. U bazi se čuvaju samo hasovi."""
    data = request.get_json(silent=True) or {}
    secret = str(data.get('secret', '')).strip()
    code = str(data.get('code', '')).strip()
    if not secret or not code:
        return jsonify({"error": "MISSING_INPUT"}), 400
    if not totp_verify(secret, code):
        return jsonify({"error": "INVALID_CODE",
                        "message": "The 6-digit code does not match. Check your Authenticator app time and try again."}), 400

    plain_codes, hashed_codes = generate_recovery_codes(count=8)
    uid = session['user_id']
    try:
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            c = conn.cursor()
            c.execute("UPDATE users SET totp_secret=?, totp_enabled=1, totp_recovery=? WHERE id=?",
                      (secret, json.dumps(hashed_codes), uid))
            conn.commit()
    except Exception:
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500

    log_audit('SECURITY', 'auth', f'2FA enabled for {session.get("username")}', is_suspicious=False)
    return jsonify({
        "status": "success",
        "message": "2FA is now active. Save the recovery codes below in a safe place — they will not be shown again.",
        "recovery_codes": plain_codes,
    })


@auth_bp.route('/api/auth/totp/disable', methods=['POST'])
@login_required
def totp_disable():
    """Korisnik isključuje 2FA. Zahteva trenutnu lozinku + validan TOTP kod
    (ili recovery kod) kao dvostruku zaštitu — da niko ko slučajno provali
    sesiju ne može da olabavi bezbednost naloga."""
    data = request.get_json(silent=True) or {}
    password = data.get('password', '')
    code = str(data.get('code', '')).strip()
    if not password or not code:
        return jsonify({"error": "MISSING_INPUT",
                        "message": "Password and current 2FA code required."}), 400

    uid = session['user_id']
    with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
        c = conn.cursor()
        c.execute("SELECT password, totp_secret, totp_recovery FROM users WHERE id=?", (uid,))
        row = c.fetchone()
    if not row: return jsonify({"error": "USER_NOT_FOUND"}), 404
    if not check_password_hash(row[0], password):
        return jsonify({"error": "WRONG_PASSWORD"}), 401
    # Prihvatamo i TOTP kod i recovery kod
    ok = totp_verify(row[1], code) if row[1] else False
    if not ok and row[2]:
        try:
            recovery_list = json.loads(row[2])
        except Exception:
            recovery_list = []
        matched, _rest = verify_recovery_code(recovery_list, code)
        ok = matched
    if not ok:
        return jsonify({"error": "INVALID_CODE"}), 401

    try:
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            c = conn.cursor()
            c.execute("UPDATE users SET totp_secret=NULL, totp_enabled=0, totp_recovery=NULL WHERE id=?", (uid,))
            conn.commit()
    except Exception:
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500

    log_audit('SECURITY', 'auth', f'2FA disabled for {session.get("username")}', is_suspicious=True)
    return jsonify({"status": "success", "message": "2FA is now disabled."})


@auth_bp.route('/api/auth/totp/status', methods=['GET'])
@login_required
def totp_status():
    """Klijent proverava da li je 2FA uključeno na svom nalogu."""
    uid = session['user_id']
    with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
        c = conn.cursor()
        c.execute("SELECT totp_enabled, totp_recovery FROM users WHERE id=?", (uid,))
        row = c.fetchone()
    if not row: return jsonify({"enabled": False, "recovery_codes_remaining": 0})
    remaining = 0
    if row[1]:
        try: remaining = len(json.loads(row[1]))
        except Exception: pass
    return jsonify({"enabled": bool(int(row[0] or 0)), "recovery_codes_remaining": remaining})