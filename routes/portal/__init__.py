"""ASPIDUS portal — package init (V24.2 SUPABASE-ONLY).

Sadrži:
  - In-memory auth state (portal_otps, portal_auth_sessions, pending_email_sessions)
  - TTL konfiguraciju (FirewallCache)
  - safe_parse() — legacy helper koji se još uvek koristi u actions.py / data.py
  - find_partner_by_token(token) — V24.2: no cursor, čita iz Supabase preko data_layer
  - find_partner_by_email(email) — V24.2: čita iz Supabase preko data_layer
  - is_partner_premium(partner_dict | tuple | partner_id) — V24.2: ID case koristi supabase_store
  - log_portal_activity() — V24.2: insert u Supabase preko data_layer
  - init_portal_db() — V24.2: NO-OP (tabele već postoje na Supabase nakon Faze 1)
"""
import json
import logging
import secrets
import time
from datetime import datetime, timezone

from flask import Blueprint
from utils import decrypt_data, FirewallCache

logger = logging.getLogger(__name__)

portal_bp = Blueprint('portal', __name__)

# ==========================================================
#  MEMORIJSKO STANJE PORTAL AUTENTIFIKACIJE
# ==========================================================
# portal_otps:          token -> {'otp', 'expires', 'attempts'}
# portal_auth_sessions: token -> {'key', 'expires', 'last_active', 'partner_id'}
# pending_email_sessions: session_id -> {'token', 'partner_id', 'email', 'expires'}
#
# NAPOMENA (V24.2): ova stanja ostaju u memoriji Python procesa — Supabase
# ne čuvamo kratkoživeće OTP/session podatke (gube se na restartu, što
# je prihvatljivo jer su kratkog veka i klijent lako može da ponovo zatraži OTP).
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
    """V24.2 SUPABASE-ONLY: tabele (kyc_submissions, portal_products,
    portal_activity_log, …) već postoje na Supabase-u nakon Faza 1 šeme
    migracije. Funkcija je sada NO-OP — ostavljena radi kompatibilnosti sa
    app.py koji je poziva pri startu."""
    logger.info('init_portal_db: skipped (Supabase-only mode; schema applied in Phase 1)')


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
    i sl.

    V24.2 NAPOMENA: ova funkcija se još uvek koristi u actions.py / data.py
    gde se legacy `data` JSONB kolona čita kao sirov string (legacy tokovi).
    Za novi kod koristite supabase_store.get_entity() koji vraća već rehidriran
    dict (top-level kolone + data JSONB spojeni)."""
    if data_str is None or data_str == '':
        return {}
    if isinstance(data_str, dict):
        # V24.2: neki pozivaoci prosleđuju već rehidriran dict (defanzivno)
        return data_str
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


# ==========================================================
#  SUPABASE PARTNER LOOKUP — V24.2
# ==========================================================

# Mapa snake_case (top-level Supabase kolona) ↔ camelCase (legacy JSONB key koji
# frontend / stari kod očekuje). Koristi se u _partner_compat() da obezbedi da
# nakon read-a partner dict ima OBA oblika — bez menjaja svih pozivalaca.
_PARTNER_KEY_MAP = {
    'portal_token':       'portalToken',
    'is_portal_active':   'isPortalActive',
    'is_premium':         'isPremium',
    'company_name':       'companyName',
    'contact_person':     'contactPerson',
    'kyc_approved':       'kycApproved',
    'tax_id':             'taxId',
    'portal_level':       'portalLevel',
    'auth_user_id':       'authUserId',
    'can_login':          'canLogin',
}


def _partner_compat(p):
    """Uveri se da partner dict ima i snake_case (top-level kolona) i camelCase
    (legacy JSONB key) parove. Ako je jedan oblik postavljen, drugi se
    postavlja iz njega (ako nedostaje). Mutira i vraća isti dict."""
    if not isinstance(p, dict):
        return p
    for snake, camel in _PARTNER_KEY_MAP.items():
        if snake in p:
            # top-level column je izvor istine — postavi camelCase ako nedostaje
            if camel not in p or p.get(camel) is None:
                p[camel] = p[snake]
        elif camel in p:
            # JSONB ima camelCase, top-level nedostaje — mirroruj
            p[snake] = p[camel]
    return p


def find_partner_by_token(token, enforce_active=True):
    """V24.2 SUPABASE-ONLY. Pronalazi partnera po portalToken (sada top-level
    kolona `portal_token`). Ako enforce_active i portal je opozvan
    (isPortalActive == False), tretira se kao da partner ne postoji (Kill Switch).

    POTPIS SE RAZLIKUJE OD LEGACY: više ne prima SQLite cursor!
        STARI: find_partner_by_token(cursor, token, enforce_active=True)
        NOVI:  find_partner_by_token(token, enforce_active=True)

    Vraća (partner_id, partner_dict) ili (None, None). partner_dict ima i
    snake_case i camelCase ključeve (backward compat)."""
    if not token:
        return None, None
    try:
        import supabase_store as store
        from data_layer import select_one
        # Top-level kolona `portal_token` (Faza 1)
        row = select_one('partners', {'portal_token': ('eq', token)})
        if not row:
            return None, None
        pid = row.get('id')
        if not pid:
            return None, None
        # get_entity radi rehidraciju (top-level + data JSONB spojeni u flat dict)
        partner = store.get_entity('partners', pid)
        if not partner:
            return None, None
        _partner_compat(partner)
        if enforce_active and partner.get('isPortalActive', True) is False:
            return None, None
        return pid, partner
    except Exception as e:
        logger.error(f'find_partner_by_token({token[:8]}…): {e}')
        return None, None


def find_partner_by_email(email):
    """V24.2 SUPABASE-ONLY. Pronalazi partnera po top-level koloni `email`
    (case-insensitive). Vraća (partner_id, partner_dict) ili (None, None)."""
    if not email:
        return None, None
    try:
        import supabase_store as store
        from data_layer import select_one
        # Top-level kolona `email` (Faza 1)
        row = select_one('partners', {'email': ('ilike', email.strip())})
        if not row:
            return None, None
        pid = row.get('id')
        if not pid:
            return None, None
        partner = store.get_entity('partners', pid)
        if not partner:
            return None, None
        _partner_compat(partner)
        return pid, partner
    except Exception as e:
        logger.error(f'find_partner_by_email({email}): {e}')
        return None, None


def log_portal_activity(partner_id, action, details, ip=None, user_agent=None):
    """Beleži jedno dešavanje iz PORTALA (klijentski nalozi) u posebnu tabelu
    razdvojenu od CRM audit-a. Automatski obogaćuje unos IP geolokacijom
    (get_ip_info je kesiran, ne usporava) da admin može da vidi zemlju/grad
    i klikne na Google Maps za koordinate.

    V24.2 SUPABASE-ONLY: insert ide preko data_layer.insert u tabelu
    `portal_activity_log` (Postgres). Best-effort — nikad ne bacamo iz ovog
    helper-a da ne srušimo glavni request."""
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
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    try:
        from data_layer import insert as _dl_insert
        _dl_insert('portal_activity_log', {
            'id': entry_id,
            'partner_id': partner_id,
            'action': action,
            'details': (details or '')[:4000],
            'ip_address': ip,
            'user_agent': (user_agent or '')[:200],
            'location': location_str,
            'timestamp': now_iso,
        })
    except Exception as e:
        logger.debug(f'log_portal_activity skipped: {type(e).__name__}: {str(e)[:120]}')


def is_partner_premium(cursor_or_data):
    """Vraća True ako je partner PREMIUM klijent — dobija poseban tretman:
      • GPS lokacija NIJE obavezna za OTP login
      • KYC status ne blokira pristup portalu (uvek 'approved' na svojoj strani)
      • KYC forma sva polja opciona (nema IBAN/BIC/VIES hard-block-ova)
      • Poseban vizuelni prikaz (Premium tema)

    Parametar može biti:
      • partner dict (već učitan) — čita isPremium / is_premium
      • tuple (partner_id, partner_dict) — čita iz drugog elementa
      • partner_id string — učitava iz Supabase preko supabase_store.get_entity

    V24.2 SUPABASE-ONLY."""
    if isinstance(cursor_or_data, dict):
        return bool(cursor_or_data.get('isPremium') or cursor_or_data.get('is_premium'))
    if isinstance(cursor_or_data, tuple) and len(cursor_or_data) >= 2:
        p = cursor_or_data[1] or {}
        if isinstance(p, dict):
            return bool(p.get('isPremium') or p.get('is_premium'))
        return False
    # string ID case — učitaj iz Supabase
    pid = str(cursor_or_data or '').strip()
    if not pid:
        return False
    try:
        import supabase_store as store
        p = store.get_entity('partners', pid)
        if p:
            _partner_compat(p)
            return bool(p.get('isPremium') or p.get('is_premium'))
    except Exception as e:
        logger.debug(f'is_partner_premium({pid}): {e}')
    return False


# Učitavanje svih modula kako bi rute bile aktivne
from . import auth, data, actions, auth_supabase  # noqa: E402,F401
