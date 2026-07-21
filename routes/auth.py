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
    
    # 3. Konekcija na bazu i provera korisnika
    user = None
    try:
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            conn.execute('PRAGMA busy_timeout=30000;')
            c = conn.cursor()
            c.execute('SELECT id, username, password, role, permissions, signature, totp_secret, totp_enabled, totp_recovery FROM users WHERE LOWER(username)=LOWER(?)', (username,))
            user = c.fetchone()
    except Exception as e:
        # Detaljno logovanje u server log (Render) radi dijagnostike; klijent dobija generičku poruku.
        logger.error(f"LOGIN DB ERROR for user '{username}': {e}", exc_info=True)
        log_audit('CRITICAL_ERROR', 'system', f'Login failed due to database error: {e}', is_suspicious=True, location=location)
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500

    # Dijagnostika (samo u server log): razlog neuspeha, bez otkrivanja klijentu.
    if not user:
        logger.info(f"LOGIN: unknown username '{username}' from IP {client_ip}")
    elif not check_password_hash(user[2], password):
        logger.info(f"LOGIN: wrong password for '{username}' from IP {client_ip}")

    # 4. Uspešna provera lozinke
    if user and check_password_hash(user[2], password):
        # 4a. 2FA gate — ako je korisnik uključio TOTP, mora predati validan kod
        totp_secret_db = user[6] if len(user) > 6 else None
        totp_enabled_db = int(user[7] or 0) if len(user) > 7 else 0
        totp_recovery_db = user[8] if len(user) > 8 else None
        if totp_enabled_db and totp_secret_db:
            # Klijent ne šalje kod → tražimo drugi korak
            if not totp_code and not recovery_code:
                log_audit('LOGIN', 'system', f'Password OK, waiting for 2FA code: {username}', location=location)
                return jsonify({"status": "totp_required",
                                "message": "Enter the 6-digit code from your Authenticator app, or a recovery code."}), 200
            ok = False
            # Recovery code path — iskoristi (jedan-put) recovery i disable TOTP zahtev za tu prijavu
            if recovery_code:
                try:
                    recovery_list = json.loads(totp_recovery_db) if totp_recovery_db else []
                except Exception:
                    recovery_list = []
                matched, remaining = verify_recovery_code(recovery_list, recovery_code)
                if matched:
                    ok = True
                    # Overwriteuj recovery listu u bazi (skinut korišćeni kod)
                    try:
                        with sqlite3.connect(DB_FILE, timeout=15.0) as _con:
                            _cc = _con.cursor()
                            _cc.execute("UPDATE users SET totp_recovery=? WHERE id=?",
                                        (json.dumps(remaining), user[0]))
                            _con.commit()
                        log_audit('SECURITY', 'system',
                                  f'2FA login via recovery code (one used): {username}. Remaining: {len(remaining)}',
                                  is_suspicious=True, location=location)
                    except Exception:
                        logger.warning('recovery code update failed', exc_info=True)
            # TOTP code path
            if not ok and totp_code:
                ok = totp_verify(totp_secret_db, totp_code)
            if not ok:
                log_audit('SECURITY', 'system', f'2FA failed: {username}', is_suspicious=True, location=location)
                return jsonify({"error": "TOTP_INVALID",
                                "message": "Invalid 2FA code. Try again or use a recovery code."}), 401
        session.permanent = True
        session['user_id'] = user[0]
        session['username'] = user[1]
        session['role'] = user[3]
        session['login_time'] = datetime.datetime.now(datetime.timezone.utc).timestamp()

        session['login_ip'] = client_ip
        session['login_ua'] = request.user_agent.string if request.user_agent else "Unknown"
        session['login_ua_family'] = f"{request.user_agent.browser or ''}|{request.user_agent.platform or ''}"
        # Snimi aktuelnu token_version u sesiju; promena lozinke uveća broj u bazi
        # i sve stare sesije padnu na prvoj sledećoj zaštićenoj ruti.
        session['token_version'] = get_user_token_version(user[0])

        if client_ip in FirewallCache.login_attempts:
            del FirewallCache.login_attempts[client_ip]

        # ANOMALY DETEKCIJA: iznenadna prijava iz druge zemlje u odnosu na prethodnu.
        try:
            _, ip_location, _tz = get_ip_info(client_ip) if client_ip else ('', '', '')
            # last_login_country se čuva u users tabeli (šema migrirana); poredimo
            # ipapi.co "network_info" reprezentaciju grada/zemlje.
            with sqlite3.connect(DB_FILE, timeout=15.0) as _conn:
                _c = _conn.cursor()
                _c.execute("SELECT last_login_country FROM users WHERE id=?", (user[0],))
                prev = _c.fetchone()
                prev_country = (prev[0] or '').strip() if prev else ''
                # Grubo poređenje po "Country" tokenu (poslednji token u ipapi label-u)
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
                    _c.execute("UPDATE users SET last_login_country=? WHERE id=?", (new_country, user[0]))
                    _conn.commit()
        except Exception:
            logger.warning('anomaly detection failed', exc_info=True)

        full_details = f"Successful login. Device: {device_info}"
        log_audit('LOGIN', 'system', full_details, location=location)
        return jsonify({"status": "success", "user": {"id": user[0], "username": user[1], "role": user[3], "permissions": json.loads(user[4]) if user[4] else {}, "signature": user[5] if len(user) > 5 else None}})
    
    # 5. Neuspešna prijava - beleženje pokušaja
    if client_ip not in FirewallCache.login_attempts:
        FirewallCache.login_attempts[client_ip] = []
    FirewallCache.login_attempts[client_ip].append(datetime.datetime.now(datetime.timezone.utc).timestamp())
    
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    FirewallCache.login_attempts[client_ip] = [t for t in FirewallCache.login_attempts[client_ip] if now - t < 300]
    
    if len(FirewallCache.login_attempts[client_ip]) >= FirewallCache.settings.get('max_login', 10):
        FirewallCache.blacklist.add(client_ip)
        log_audit('SECURITY', 'firewall', f"Auto-blacklisted IP {client_ip} due to brutal force attempts.", is_suspicious=True, location=location)

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
        row = None
        try:
            with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
                conn.execute('PRAGMA busy_timeout=30000;')
                c = conn.cursor()
                c.execute('SELECT permissions, signature FROM users WHERE id=?', (session['user_id'],))
                row = c.fetchone()
        except Exception:
            pass

        return jsonify({"user": {"id": session['user_id'], "username": session['username'], "role": session['role'], "permissions": json.loads(row[0]) if row and row[0] else {}, "signature": (row[1] if row and len(row) > 1 else None)}})
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

    try:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            conn.execute('PRAGMA busy_timeout=30000;')
            c = conn.cursor()
            pw_hash = generate_password_hash(new_password, method='scrypt:32768:8:1')
            c.execute('UPDATE users SET password=?, last_password_change_at=? WHERE id=?', (pw_hash, now_iso, session['user_id']))
            conn.commit()
    except Exception:
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
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            conn.execute('PRAGMA busy_timeout=30000;')
            conn.execute('UPDATE users SET signature=? WHERE id=?', (sig, session['user_id']))
            conn.commit()
    except Exception:
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