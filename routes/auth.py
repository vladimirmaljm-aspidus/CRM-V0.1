import datetime
import json
import sqlite3
import re
import logging
from flask import Blueprint, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
from config import DB_FILE
from utils import log_audit, login_required, FirewallCache

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
            conn.execute('PRAGMA journal_mode=WAL;')
            c = conn.cursor()
            c.execute('SELECT id, username, password, role, permissions, signature FROM users WHERE LOWER(username)=LOWER(?)', (username,))
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

    # 4. Uspešna prijava
    if user and check_password_hash(user[2], password):
        session.permanent = True
        session['user_id'] = user[0]
        session['username'] = user[1]
        session['role'] = user[3]
        session['login_time'] = datetime.datetime.now(datetime.timezone.utc).timestamp()
        
        session['login_ip'] = client_ip
        session['login_ua'] = request.user_agent.string if request.user_agent else "Unknown"
        session['login_ua_family'] = f"{request.user_agent.browser or ''}|{request.user_agent.platform or ''}"
        
        if client_ip in FirewallCache.login_attempts:
            del FirewallCache.login_attempts[client_ip]
            
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
                conn.execute('PRAGMA journal_mode=WAL;')
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
    
    try:
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
            conn.execute('PRAGMA busy_timeout=30000;')
            c = conn.cursor()
            pw_hash = generate_password_hash(new_password, method='scrypt:32768:8:1')
            c.execute('UPDATE users SET password=? WHERE id=?', (pw_hash, session['user_id']))
            conn.commit()
    except Exception:
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500
    
    log_audit('EDIT', 'users', 'User successfully changed their own password.')
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
            conn.execute('PRAGMA journal_mode=WAL;')
            conn.execute('PRAGMA busy_timeout=30000;')
            conn.execute('UPDATE users SET signature=? WHERE id=?', (sig, session['user_id']))
            conn.commit()
    except Exception:
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500

    log_audit('EDIT', 'users', 'User updated their personal signature.' if sig else 'User removed their personal signature.')
    return jsonify({"status": "success", "signature": sig})