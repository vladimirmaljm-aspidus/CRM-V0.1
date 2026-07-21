import sqlite3
import json
import time
import secrets
from flask import Blueprint
from config import PORTAL_DB_FILE, DB_FILE
from utils import decrypt_data, FirewallCache

portal_bp = Blueprint('portal', __name__)

# ==========================================================
#  MEMORIJSKO STANJE PORTAL AUTENTIFIKACIJE
# ==========================================================
# portal_otps:          token -> {'otp', 'expires', 'attempts'}
# portal_auth_sessions: token -> {'key', 'expires', 'last_active', 'partner_id'}
# pending_email_sessions: session_id -> {'token', 'partner_id', 'email', 'expires'}
portal_otps = {}
portal_auth_sessions = {}
pending_email_sessions = {}

# Podrazumevani TTL-ovi (u sekundama). Admin ih menja preko settings.firewall
# i vrednosti se učitavaju u FirewallCache pri startu / posle svakog save-a.
PORTAL_SESSION_TTL = 3600
PORTAL_INACTIVITY_TTL = 900
PORTAL_OTP_TTL = 300
PORTAL_OTP_MAX_ATTEMPTS = 5


def _fw_ttl(key, default):
    """Uzmi konfigurabilnu vrednost iz FirewallCache (postavlja je admin), inače default."""
    try:
        return int(FirewallCache.settings.get(key, default))
    except (TypeError, ValueError):
        return default


def init_portal_db():
    conn = None
    try:
        conn = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
        # WAL se postavlja SAMO ovde (init, pri startu pre workera) — trajno na
        # fajlu. Per-request konekcije koriste busy_timeout, ne diraju journal mode
        # (menjanje journal mode-a traži ekskluzivni lock → "database is locked").
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA busy_timeout=30000;')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS kyc_submissions
                     (id TEXT PRIMARY KEY, partner_id TEXT, token TEXT, data JSON, submitted_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS portal_products
                     (id TEXT PRIMARY KEY, partner_id TEXT, data JSON, status TEXT, created_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS portal_activity_log
                     (id TEXT PRIMARY KEY, partner_id TEXT, action TEXT, details TEXT,
                      ip_address TEXT, user_agent TEXT, location TEXT, timestamp TEXT)''')
        conn.commit()
    finally:
        if conn: conn.close()

init_portal_db()


def check_portal_rate_limit(ip):
    if ip in FirewallCache.whitelist: return True
    now = time.time()
    FirewallCache.portal_attempts[ip] = [t for t in FirewallCache.portal_attempts.get(ip, []) if now - t < 60]
    if len(FirewallCache.portal_attempts.get(ip, [])) > FirewallCache.settings.get('max_portal', 50): return False
    FirewallCache.portal_attempts.setdefault(ip, []).append(now)
    return True


def safe_parse(data_str):
    """Pokušava JSON parse; ako ne uspe, pretpostavlja da je payload šifrovan
    Fernet-om pa poziva decrypt_data(). Bare except zamenjen preciznijim
    hvatanjem — hvatamo samo očekivane greške parsiranja/tipa, ne KeyboardInterrupt
    i sl."""
    if data_str is None or data_str == '':
        return {}
    try:
        return json.loads(data_str)
    except (json.JSONDecodeError, TypeError, ValueError):
        return decrypt_data(data_str)


# ==========================================================
#  CENTRALIZOVANA PORTAL AUTENTIFIKACIJA
# ==========================================================

def _cleanup_expired():
    """Sprečava neograničeno rastenje memorije od isteklih OTP-ova i sesija."""
    now = time.time()
    inactivity = _fw_ttl('portal_inactivity', PORTAL_INACTIVITY_TTL)
    for tok in [t for t, v in portal_otps.items() if v.get('expires', 0) < now]:
        portal_otps.pop(tok, None)
    for tok in [t for t, v in portal_auth_sessions.items() if v.get('expires', 0) < now or now - v.get('last_active', 0) > inactivity]:
        portal_auth_sessions.pop(tok, None)
    for sid in [s for s, v in pending_email_sessions.items() if v.get('expires', 0) < now]:
        pending_email_sessions.pop(sid, None)


def create_portal_otp(token):
    """Generiše novi OTP i resetuje brojač pokušaja za dati token."""
    _cleanup_expired()
    otp = str(secrets.randbelow(900000) + 100000)
    portal_otps[token] = {'otp': otp, 'expires': time.time() + _fw_ttl('portal_otp', PORTAL_OTP_TTL), 'attempts': 0}
    return otp


def verify_portal_otp(token, user_otp):
    """Constant-time provera OTP-a sa limitom pokušaja (anti brute-force).
    Vraća novi auth_key na uspeh, ili None na neuspeh."""
    _cleanup_expired()
    record = portal_otps.get(token)
    if not record:
        return None
    if record['expires'] < time.time():
        portal_otps.pop(token, None)
        return None
    # Limit pokušaja: posle N grešaka, kod se poništava (mora nov OTP).
    if record.get('attempts', 0) >= PORTAL_OTP_MAX_ATTEMPTS:
        portal_otps.pop(token, None)
        return None
    if user_otp and secrets.compare_digest(str(record['otp']), str(user_otp)):
        portal_otps.pop(token, None)
        return create_portal_session(token)
    record['attempts'] = record.get('attempts', 0) + 1
    return None


def create_portal_session(token, partner_id=None):
    key = secrets.token_hex(32)
    now = time.time()
    from flask import request as _req
    ip = _req.headers.get('X-Forwarded-For', _req.remote_addr) if _req else ''
    if ip and ',' in ip: ip = ip.split(',')[0].strip()
    portal_auth_sessions[token] = {
        'key': key, 'expires': now + _fw_ttl('portal_session', PORTAL_SESSION_TTL),
        'last_active': now, 'partner_id': partner_id,
        # BEZBEDNOST: vežemo sesiju za IP koji je prošao OTP verifikaciju.
        # Ako se auth_key pojavi sa druge IP adrese, to je pokušaj krađe sesije.
        'bound_ip': ip or None
    }
    return key


def verify_portal_session(token, auth_header):
    """Constant-time provera portal sesije sa isticanjem (TTL + inactivity + IP binding)."""
    if not token or not auth_header:
        return False
    sess = portal_auth_sessions.get(token)
    if not sess:
        return False
    now = time.time()
    if sess['expires'] < now:
        portal_auth_sessions.pop(token, None)
        return False
    if now - sess.get('last_active', 0) > _fw_ttl('portal_inactivity', PORTAL_INACTIVITY_TTL):
        portal_auth_sessions.pop(token, None)
        return False
    if not secrets.compare_digest(sess['key'], auth_header):
        return False

    # IP binding — ako se sesija koristi sa druge IP-e, poništi je i loguj kao suspicious.
    try:
        from flask import request as _req
        cur_ip = _req.headers.get('X-Forwarded-For', _req.remote_addr) if _req else ''
        if cur_ip and ',' in cur_ip: cur_ip = cur_ip.split(',')[0].strip()
        if sess.get('bound_ip') and cur_ip and cur_ip != sess['bound_ip']:
            portal_auth_sessions.pop(token, None)
            try:
                log_portal_activity(sess.get('partner_id'),
                                    'SESSION_HIJACK_BLOCKED',
                                    f'Portal auth_key seen from {cur_ip}, bound to {sess["bound_ip"]}',
                                    ip=cur_ip)
            except Exception:
                pass
            return False
    except Exception:
        pass

    sess['last_active'] = now
    return True


def find_partner_by_token(cursor, token, enforce_active=True):
    """Pronalazi partnera po portalTokenu. Ako enforce_active i portal je opozvan
    (isPortalActive == False), tretira se kao da partner ne postoji (Kill Switch).
    Vraća (partner_id, partner_dict) ili (None, None)."""
    if not token:
        return None, None
    cursor.execute("SELECT id, data FROM partners")
    for r in cursor.fetchall():
        p_data = safe_parse(r[1])
        if p_data.get('portalToken') == token:
            if enforce_active and p_data.get('isPortalActive', True) is False:
                return None, None
            return r[0], p_data
    return None, None


def log_portal_activity(partner_id, action, details, ip=None, user_agent=None):
    """Beleži jedno dešavanje iz PORTALA (klijentski nalozi) u posebnu tabelu
    razdvojenu od CRM audit-a. Automatski obogaćuje unos IP geolokacijom
    (get_ip_info je kesiran, ne usporava) da admin može da vidi zemlju/grad
    i klikne na Google Maps za koordinate."""
    from flask import request as _req
    from utils import get_ip_info
    if ip is None:
        try:
            ip = _req.headers.get('X-Forwarded-For', _req.remote_addr)
            if ip and ',' in ip: ip = ip.split(',')[0].strip()
        except Exception:
            ip = None
    if user_agent is None:
        try:
            user_agent = _req.user_agent.string if _req.user_agent else 'Unknown'
        except Exception:
            user_agent = 'Unknown'

    # Geo lookup (kesiran)
    location_str = 'N/A'
    try:
        network_info, ip_location, _tz = get_ip_info(ip) if ip else ('N/A', 'N/A', 'N/A')
        # Sastavimo "grad, zemlja | lat,lng" format da UI može da parsira mapu.
        parts = []
        if network_info and network_info not in ('N/A', 'UNKNOWN_IP_LOCATION', 'LOCAL_NETWORK'):
            parts.append(network_info)
        if ip_location and ip_location != 'N/A':
            parts.append(ip_location)
        if parts:
            location_str = ' | '.join(parts)
    except Exception:
        pass

    entry_id = secrets.token_hex(12)
    timestamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    try:
        conn = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
        conn.execute('PRAGMA busy_timeout=30000;')
        conn.execute(
            "INSERT INTO portal_activity_log (id, partner_id, action, details, ip_address, user_agent, location, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, partner_id, action, details, ip, user_agent, location_str, timestamp)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def is_partner_premium(cursor_or_data):
    """Vraća True ako je partner PREMIUM klijent — dobija poseban tretman:
      • GPS lokacija NIJE obavezna za OTP login
      • KYC status ne blokira pristup portalu (uvek 'approved' na svojoj strani)
      • KYC forma sva polja opciona (nema IBAN/BIC/VIES hard-block-ova)
      • Poseban vizuelni prikaz (Premium tema)

    Parametar može biti partner dict (već učitan) ili tuple (partner_id, partner_dict)
    ili samo partner_id string (u tom slučaju učitavamo iz baze)."""
    if isinstance(cursor_or_data, dict):
        return bool(cursor_or_data.get('isPremium'))
    if isinstance(cursor_or_data, tuple) and len(cursor_or_data) >= 2:
        return bool((cursor_or_data[1] or {}).get('isPremium'))
    # string ID case — učitaj iz baze
    pid = str(cursor_or_data or '').strip()
    if not pid:
        return False
    try:
        with sqlite3.connect(DB_FILE, timeout=10.0) as conn:
            row = conn.execute("SELECT data FROM partners WHERE id=?", (pid,)).fetchone()
        if row:
            p = safe_parse(row[0])
            return bool(isinstance(p, dict) and p.get('isPremium'))
    except Exception:
        pass
    return False


def find_partner_by_email(email):
    if not email:
        return None, None
    email_lower = email.strip().lower()
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    c = conn.cursor()
    c.execute('SELECT id, data FROM partners')
    for row in c.fetchall():
        p = safe_parse(row[1])
        p_email = (p.get('contact', {}).get('email') or p.get('email', '')).strip().lower()
        if p_email == email_lower:
            conn.close()
            return row[0], p
    conn.close()
    return None, None


# Učitavanje svih modula kako bi rute bile aktivne
from . import auth, data, actions
