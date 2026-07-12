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
# portal_auth_sessions: token -> {'key', 'expires'}
portal_otps = {}
portal_auth_sessions = {}

# Sesija portala važi 1h; OTP 5 min; posle 5 pogrešnih OTP unosa kod se poništava.
PORTAL_SESSION_TTL = 3600
PORTAL_OTP_TTL = 300
PORTAL_OTP_MAX_ATTEMPTS = 5


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


def check_portal_rate_limit(ip):
    if ip in FirewallCache.whitelist: return True
    now = time.time()
    FirewallCache.portal_attempts[ip] = [t for t in FirewallCache.portal_attempts.get(ip, []) if now - t < 60]
    if len(FirewallCache.portal_attempts.get(ip, [])) > FirewallCache.settings.get('max_portal', 50): return False
    FirewallCache.portal_attempts.setdefault(ip, []).append(now)
    return True


def safe_parse(data_str):
    try:
        return json.loads(data_str)
    except:
        return decrypt_data(data_str)


# ==========================================================
#  CENTRALIZOVANA PORTAL AUTENTIFIKACIJA
# ==========================================================

def _cleanup_expired():
    """Sprečava neograničeno rastenje memorije od isteklih OTP-ova i sesija."""
    now = time.time()
    for tok in [t for t, v in portal_otps.items() if v.get('expires', 0) < now]:
        portal_otps.pop(tok, None)
    for tok in [t for t, v in portal_auth_sessions.items() if v.get('expires', 0) < now]:
        portal_auth_sessions.pop(tok, None)


def create_portal_otp(token):
    """Generiše novi OTP i resetuje brojač pokušaja za dati token."""
    _cleanup_expired()
    otp = str(secrets.randbelow(900000) + 100000)
    portal_otps[token] = {'otp': otp, 'expires': time.time() + PORTAL_OTP_TTL, 'attempts': 0}
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


def create_portal_session(token):
    key = secrets.token_hex(32)
    portal_auth_sessions[token] = {'key': key, 'expires': time.time() + PORTAL_SESSION_TTL}
    return key


def verify_portal_session(token, auth_header):
    """Constant-time provera portal sesije sa isticanjem (TTL)."""
    if not token or not auth_header:
        return False
    sess = portal_auth_sessions.get(token)
    if not sess:
        return False
    if sess['expires'] < time.time():
        portal_auth_sessions.pop(token, None)
        return False
    return secrets.compare_digest(sess['key'], auth_header)


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


# Učitavanje svih modula kako bi rute bile aktivne
from . import auth, data, actions
