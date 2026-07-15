import datetime
import time
import uuid
import os
import sqlite3
import json
import secrets
import logging
import threading
import urllib.request
import ipaddress
from functools import wraps
from flask import request, session, jsonify, redirect, url_for
from config import DB_FILE, AUDIT_DB_FILE, ALLOWED_EXTENSIONS, ENCRYPTION_KEY
from cryptography.fernet import Fernet, InvalidToken

_util_logger = logging.getLogger(__name__)

cipher_suite = Fernet(ENCRYPTION_KEY)

def encrypt_data(data_dict):
    """Pretvara rečnik u JSON i šifruje ga."""
    json_str = json.dumps(data_dict)
    return cipher_suite.encrypt(json_str.encode('utf-8')).decode('utf-8')

def decrypt_data(encrypted_str):
    """Sigurno dešifruje ili parsira JSON. NIKADA ne baca izuzetak — svaka ruta koja
    čita nešto iz baze (KYC, comms_settings, company, permissions) pucala bi ako
    Fernet ne uspe (npr. rotacija ključa) i JSON ne uspe (npr. plain string).

    Pravilo: ako je payload {} ili [], vrati odgovarajući prazan kontejner;
    ako je čist string, vrati ga kao string; ako je None, vrati {}."""
    if encrypted_str is None or encrypted_str == '':
        return {}
    # 1) Pokušaj Fernet
    try:
        raw = cipher_suite.decrypt(encrypted_str.encode('utf-8') if isinstance(encrypted_str, str) else encrypted_str)
        try:
            return json.loads(raw.decode('utf-8'))
        except (json.JSONDecodeError, ValueError):
            return raw.decode('utf-8', errors='replace')
    except (InvalidToken, TypeError, ValueError, AttributeError):
        pass
    # 2) Pokušaj direktan JSON
    try:
        return json.loads(encrypted_str)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    # 3) Fallback: vrati kao string (ako je uopšte string) ili prazan dict
    return encrypted_str if isinstance(encrypted_str, str) else {}

def is_safe_file_content(file_stream, filename):
    """Čita sirove hex bajtove da spreči maliciozne skripte maskirane u slike."""
    if not allowed_file(filename):
        return False

    ext = filename.rsplit('.', 1)[1].lower()
    header = file_stream.read(512)
    file_stream.seek(0)

    magic_numbers = {
        'pdf': b'%PDF-',
        'png': b'\x89PNG\r\n\x1a\n',
        'jpg': b'\xff\xd8',
        'jpeg': b'\xff\xd8'
    }

    expected_magic = magic_numbers.get(ext)
    if expected_magic:
        if not header.startswith(expected_magic):
            return False

    if ext == 'json':
        stripped_header = header.lstrip()
        if not (stripped_header.startswith(b'{') or stripped_header.startswith(b'[')):
            return False

    if ext in ['csv', 'txt', 'json']:
        try:
            text_content = header.decode('utf-8', errors='ignore').lower()
            malicious = ['<?php', '<script', 'exec(', 'eval(', 'import os', 'bash -i']
            if any(p in text_content for p in malicious):
                return False
        except Exception:
            pass

    return True

class FirewallCache:
    login_attempts = {}
    portal_attempts = {}
    whitelist = set()
    blacklist = set()
    settings = {
        'max_login': 10,
        'max_portal': 50
    }

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Kes za geolokaciju po IP adresi. Ranije se za SVAKI request (login_required) i
# SVAKI audit-log upis (log_audit) pravio sinhroni HTTP poziv ka ip-api.com i
# ipapi.co - to je usporavalo svaki API poziv za 0.1-6 sekundi i, ako oba spoljna
# servisa padnu ili te rate-limituju, cela aplikacija bi prestala da radi za sve
# korisnike van localhost-a. Sa kesom, ista IP adresa se proverava spolja najvise
# jednom na IP_INFO_CACHE_TTL sekundi.
IP_INFO_CACHE = {}
IP_INFO_CACHE_TTL = 3600  # 1h

def get_ip_info(ip):
    if not ip or ip in ['127.0.0.1', 'localhost', '::1']:
        return "LOCAL_NETWORK", "N/A", "LOCAL_TIMEZONE"

    cached = IP_INFO_CACHE.get(ip)
    if cached and (time.time() - cached[3]) < IP_INFO_CACHE_TTL:
        return cached[0], cached[1], cached[2]

    result = _fetch_ip_info(ip)
    IP_INFO_CACHE[ip] = (result[0], result[1], result[2], time.time())
    return result

def _fetch_ip_info(ip):
    try:
        url = f"http://ip-api.com/json/{ip}?fields=country,city,isp,lat,lon,status,timezone"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=2) as response:
            data = json.loads(response.read().decode())
            if data.get('status') == 'success':
                network = f"{data.get('city')}, {data.get('country')} (ISP: {data.get('isp')})"
                location = f"{data.get('lat')},{data.get('lon')}"
                timezone = data.get('timezone', 'UNKNOWN_TIMEZONE')
                return network, location, timezone
    except Exception:
        pass

    try:
        url = f"https://ipapi.co/{ip}/json/"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=2) as response:
            data = json.loads(response.read().decode())
            if 'error' not in data and 'latitude' in data:
                network = f"{data.get('city', 'Unknown')}, {data.get('country_name', 'Unknown')} (ISP: {data.get('org', 'Unknown')})"
                location = f"{data.get('latitude')},{data.get('longitude')}"
                timezone = data.get('timezone', 'UNKNOWN_TIMEZONE')
                return network, location, timezone
    except Exception:
        pass

    return "UNKNOWN_IP_LOCATION", "N/A", "UNKNOWN_TIMEZONE"

def get_client_ip():
    ip_addr = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip_addr and ',' in ip_addr:
        ip_addr = ip_addr.split(',')[0].strip()
    return ip_addr

def log_audit(action, module, details, is_suspicious=False, location="N/A"):
    user_id = session.get('user_id', 'SYSTEM')
    username = session.get('username', 'GUEST')

    ip_addr = get_client_ip()

    browser_name = request.user_agent.browser or "UNKNOWN_BROWSER"
    browser_version = request.user_agent.version or ""
    os_platform = request.user_agent.platform or "UNKNOWN_OS"
    formatted_user_agent = f"{browser_name} {browser_version} ({os_platform})"

    http_method = request.method
    requested_url = request.path
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')

    # Geolokacija je sada kesirana (vidi get_ip_info) pa vise ne usporava svaki upis.
    network_info, ip_location, tz_info = get_ip_info(ip_addr)

    if location in ["N/A", "Unknown", "GPS_DENIED", "DENIED"]:
        if ip_location != "N/A":
            location = ip_location

    extended_details = f"[{http_method} {requested_url}] | {details} | NET: {network_info} | TZ: {tz_info}"

    try:
        with sqlite3.connect(AUDIT_DB_FILE, timeout=30.0) as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
            conn.execute('PRAGMA busy_timeout=30000;')
            c = conn.cursor()
            c.execute('''INSERT INTO audit_logs
                           (id, user_id, username, action, module, details, ip_address, user_agent, timestamp, is_suspicious, location)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (str(uuid.uuid4()), user_id, username, action, module, extended_details, ip_addr, formatted_user_agent, timestamp, is_suspicious, location))
            conn.commit()
    except sqlite3.OperationalError:
        pass

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        is_api = request.path.startswith('/api/') or request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        if 'user_id' not in session:
            log_audit('SECURITY', 'system', 'Unauthorized access attempt', is_suspicious=True)
            if is_api:
                return jsonify({"error": "UNAUTHORIZED"}), 401
            return redirect(url_for('auth.login'))

        current_ip = get_client_ip()

        if current_ip not in FirewallCache.whitelist and current_ip in FirewallCache.blacklist:
            log_audit('SECURITY', 'system', f'Blocked Blacklisted IP Attempt: {current_ip}', is_suspicious=True)
            if is_api:
                return jsonify({"error": "IP_BLACKLISTED"}), 403
            return "Access Denied (IP Blacklist)", 403

        # ISPRAVKA: ranije se ovde na SVAKOM zahtevu ponovo pozivao spoljni
        # geolokacijski servis i blokirao pristup ako on ne uspe da razresi IP
        # (LOCATION_REQUIRED). To je bio i ozbiljan usporivac i single point of
        # failure - pad ip-api.com/ipapi.co je bukvalno gasio celu aplikaciju.
        # Lokacija se i dalje beleži (keširano, vidi get_ip_info) radi audit traga,
        # ali vise ne blokira pristup po zahtevu. Provera lokacije pri LOGIN-u
        # (routes/auth.py) ostaje kao gate za sam ulazak u sistem.

        current_ua = request.user_agent.string if request.user_agent else "Unknown"
        current_ua_family = f"{request.user_agent.browser or ''}|{request.user_agent.platform or ''}"
        session_ua_family = session.get('login_ua_family')

        # ISPRAVKA: ranije se poredio CEO user-agent string ukljucujuci tacnu verziju
        # browsera, pa je svaka auto-nadogradnja browsera (Chrome 128 -> 129) odjavljivala
        # korisnika usred rada uz poruku "Session Hijacked". Sada se IP adresa i dalje
        # strogo poredi, a UA se poredi samo na nivou browser+OS porodice - i dalje hvata
        # stvarnu kradju sesije (drugi uredjaj/browser), ali ne i bezopasne auto-update-e.
        if session.get('login_ip') != current_ip or (session_ua_family and session_ua_family != current_ua_family):
            log_audit('SECURITY', 'system', f'CRITICAL: Session Hijacking attempt blocked! Orig IP: {session.get("login_ip")}, Attack IP: {current_ip}', is_suspicious=True)
            session.clear()
            if is_api:
                return jsonify({"error": "SESSION_HIJACKED"}), 401
            return redirect(url_for('auth.login'))

        # TOKEN VERSION: ako je admin izmenio lozinku ili ručno odjavio sve sesije,
        # token_version u bazi je uvećan. Bilo koja starija sesija se odmah odbija.
        session_tv = session.get('token_version', 1)
        current_tv = get_user_token_version(session['user_id'])
        if int(session_tv) != int(current_tv):
            log_audit('SECURITY', 'system', f'Stale session token invalidated for user_id {session["user_id"]} (v{session_tv} vs v{current_tv})', is_suspicious=False)
            session.clear()
            if is_api:
                return jsonify({"error": "SESSION_INVALIDATED"}), 401
            return redirect(url_for('auth.login'))

        return f(*args, **kwargs)
    return decorated_function

def safe_parse(val):
    """Sigurno parsiranje JSON-a iz baze podataka."""
    try:
        if isinstance(val, str):
            return json.loads(val)
        return val if val is not None else {}
    except (json.JSONDecodeError, TypeError):
        return {}


# ==========================================================
#  CSRF ZAŠTITA — double-submit token vezan za sesiju
# ==========================================================
# Klijent u prvom zahtevu dobije X-CSRF-Token header (izlaže se preko
# /api/auth/me i /api/csrf/token). Svaki mutating (POST/PUT/DELETE) zahtev
# koji ide iz browsera MORA da postavi X-CSRF-Token header koji se poredi
# constant-time sa vrednošću u sesiji. Ovim se blokira klasičan CSRF (napadač
# ne vidi header iz cross-origin fetch-a).

def _ensure_csrf_token():
    """Vraća CSRF token za trenutnu sesiju; kreira ga pri prvom pristupu."""
    tok = session.get('_csrf_token')
    if not tok:
        tok = secrets.token_urlsafe(32)
        session['_csrf_token'] = tok
    return tok


def verify_csrf_token():
    """Vraća True ako je zahtev CSRF-safe, False u suprotnom.
    Zahtev je safe ako:
      - method je GET/HEAD/OPTIONS (idempotent) ILI
      - X-CSRF-Token header se poklapa sa session tokenom (constant-time) ILI
      - dolazi sa portal auth headerom (portal koristi zasebnu OTP-based auth,
        ne oslanja se na cookie sesiju CRM-a; CSRF je za /api/* CRM ruta)"""
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return True
    # Portal endpointi imaju sopstvenu X-Portal-Auth zaštitu i ne dele cookie
    # sesiju sa CRM-om; CSRF token nema smisla tamo.
    if request.path.startswith('/api/portal/'):
        return True
    # Login endpoint mora da radi bez CSRF (token dobija tek nakon login-a).
    if request.endpoint in ('auth.login',):
        return True
    header_tok = request.headers.get('X-CSRF-Token', '')
    session_tok = session.get('_csrf_token', '')
    if not header_tok or not session_tok:
        return False
    return secrets.compare_digest(str(header_tok), str(session_tok))


# ==========================================================
#  FIREWALL / SESSION POSTAVKE — čitaju se iz admin Settings modula
# ==========================================================
# Podrazumevane vrednosti; admin ih menja preko `settings.firewall`.
DEFAULT_FIREWALL_SETTINGS = {
    'max_login_attempts': 10,          # koliko neuspešnih login-a pre auto-blacklist (5 min prozor)
    'max_portal_requests_per_min': 50, # portal per-IP rate limit
    'crm_inactivity_seconds': 1200,    # CRM auto-logout posle X sekundi neaktivnosti
    'portal_session_seconds': 3600,    # trajanje portal sesije
    'portal_inactivity_seconds': 900,  # portal auto-logout
    'portal_otp_seconds': 300,         # trajanje portal OTP koda
    'audit_retention_days': 180,       # koliko dana čuvamo audit logove (starije se automatski brišu)
}


def load_firewall_settings():
    """Učitava firewall postavke iz DB (settings.firewall), spaja sa default-ima
    i primenjuje na FirewallCache. Zove se na startup i posle svakog admin save."""
    merged = dict(DEFAULT_FIREWALL_SETTINGS)
    try:
        with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
            c = conn.cursor()
            c.execute("SELECT value FROM settings WHERE key='firewall'")
            row = c.fetchone()
        if row and row[0]:
            stored = decrypt_data(row[0])
            if isinstance(stored, dict):
                # samo poznati ključevi (sprečava injekciju smeća)
                for k in DEFAULT_FIREWALL_SETTINGS:
                    if k in stored:
                        try:
                            v = int(stored[k])
                            if v > 0:
                                merged[k] = v
                        except (TypeError, ValueError):
                            pass
    except Exception:
        _util_logger.warning('load_firewall_settings: falling back to defaults', exc_info=True)

    FirewallCache.settings['max_login'] = merged['max_login_attempts']
    FirewallCache.settings['max_portal'] = merged['max_portal_requests_per_min']
    FirewallCache.settings['crm_inactivity'] = merged['crm_inactivity_seconds']
    FirewallCache.settings['portal_session'] = merged['portal_session_seconds']
    FirewallCache.settings['portal_inactivity'] = merged['portal_inactivity_seconds']
    FirewallCache.settings['portal_otp'] = merged['portal_otp_seconds']
    FirewallCache.settings['audit_retention_days'] = merged['audit_retention_days']
    return merged


# ==========================================================
#  AUTOMATSKO ODRŽAVANJE — rotacija audit loga, čišćenje sesija
# ==========================================================

_housekeeping_started = False
_housekeeping_lock = threading.Lock()


def _housekeeping_loop():
    """Periodični posao (na svaki sat): rotira stari audit log,
    prazni istekle geoip cache stavke, resetuje login-attempts kešove.
    Sve u pozadinskom thread-u pa ne blokira request handling."""
    import gc
    while True:
        try:
            # 1) audit log retention
            days = int(FirewallCache.settings.get('audit_retention_days', 180))
            if days > 0:
                cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat().replace('+00:00', 'Z')
                try:
                    with sqlite3.connect(AUDIT_DB_FILE, timeout=30.0) as conn:
                        conn.execute('PRAGMA journal_mode=WAL;')
                        c = conn.cursor()
                        c.execute('DELETE FROM audit_logs WHERE timestamp < ? AND is_suspicious = 0', (cutoff,))
                        deleted = c.rowcount
                        conn.commit()
                    if deleted:
                        _util_logger.info(f'HOUSEKEEPING: purged {deleted} audit rows older than {days}d')
                except Exception:
                    _util_logger.warning('HOUSEKEEPING: audit purge failed', exc_info=True)

            # 2) geoip cache — obriši istekle
            now = time.time()
            expired = [ip for ip, v in IP_INFO_CACHE.items() if now - v[3] > IP_INFO_CACHE_TTL * 2]
            for ip in expired:
                IP_INFO_CACHE.pop(ip, None)

            # 3) login_attempts prozor je 300 s; obriši IP-ove bez skorašnjih pokušaja
            stale_ips = [ip for ip, ts_list in FirewallCache.login_attempts.items()
                         if not ts_list or now - max(ts_list) > 3600]
            for ip in stale_ips:
                FirewallCache.login_attempts.pop(ip, None)

            gc.collect()
        except Exception:
            _util_logger.warning('HOUSEKEEPING: iteration failed', exc_info=True)
        time.sleep(3600)  # jednom na sat


def start_housekeeping():
    """Pokreće pozadinski thread jedanput — ostaje aktivan tokom života procesa.
    Idempotentno: dvostruki poziv ne otvara drugi thread."""
    global _housekeeping_started
    with _housekeeping_lock:
        if _housekeeping_started:
            return
        _housekeeping_started = True
    t = threading.Thread(target=_housekeeping_loop, name='crm-housekeeping', daemon=True)
    t.start()
    # Isto tako pokreni backup thread (zaseban od housekeeping-a; posao je I/O-težak).
    tb = threading.Thread(target=_backup_loop, name='crm-backup', daemon=True)
    tb.start()


# ==========================================================
#  AUTOMATSKI ŠIFROVANI BACKUP BAZE — dnevni snapshot, zadrži poslednjih 14
# ==========================================================

def _backup_loop():
    """Jedanput dnevno pravi Fernet-šifrovan snapshot svih .db fajlova i briše
    starije od 14 dana. Ako DATA_DIR/backups direktorijum nije upisiv, tiho
    preskoči — housekeeping se ne sme sabotirati zbog produkcionih FS problema.
    Backup je šifrovan istim ENCRYPTION_KEY-om koji se koristi za Fernet vault
    upisa u DB, tako da napadač koji dobije samo snapshot ne može da pročita."""
    from config import DB_FILE, PORTAL_DB_FILE, AUDIT_DB_FILE, DATA_DIR
    backups_dir = os.path.join(DATA_DIR, 'backups')
    try:
        os.makedirs(backups_dir, exist_ok=True)
    except Exception:
        return
    # Sačekaj 60s posle starta pa počni (ne blokiraj boot).
    time.sleep(60)
    while True:
        try:
            ts = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            for db_path in (DB_FILE, PORTAL_DB_FILE, AUDIT_DB_FILE):
                if not os.path.exists(db_path):
                    continue
                # Bezbedan snapshot — sqlite backup API garantuje konzistentnost
                # čak i dok drugi procesi pišu u WAL.
                tmp_copy = os.path.join(backups_dir, f'.tmp_{os.path.basename(db_path)}')
                try:
                    src_conn = sqlite3.connect(db_path, timeout=30.0)
                    dst_conn = sqlite3.connect(tmp_copy, timeout=30.0)
                    with dst_conn:
                        src_conn.backup(dst_conn)
                    dst_conn.close()
                    src_conn.close()
                    with open(tmp_copy, 'rb') as f:
                        raw = f.read()
                    enc = cipher_suite.encrypt(raw)
                    out = os.path.join(backups_dir, f'{os.path.basename(db_path)}.{ts}.fernet')
                    with open(out, 'wb') as f:
                        f.write(enc)
                    os.remove(tmp_copy)
                    try:
                        os.chmod(out, 0o600)
                    except Exception:
                        pass
                except Exception:
                    _util_logger.warning(f'BACKUP: snapshot failed for {db_path}', exc_info=True)
                    try:
                        if os.path.exists(tmp_copy): os.remove(tmp_copy)
                    except Exception:
                        pass

            # Retention: obriši backup-ove starije od 14 dana
            cutoff_s = time.time() - 14 * 86400
            for name in os.listdir(backups_dir):
                if name.endswith('.fernet'):
                    p = os.path.join(backups_dir, name)
                    try:
                        if os.path.getmtime(p) < cutoff_s:
                            os.remove(p)
                    except Exception:
                        pass
            _util_logger.info('BACKUP: snapshot complete.')
        except Exception:
            _util_logger.warning('BACKUP: iteration failed', exc_info=True)
        # svakih 24h; malo drema između da uzeti prvi rezultat ne bude odmah dupli
        time.sleep(24 * 3600)


# ==========================================================
#  PER-ENDPOINT RATE LIMITER — sliding window 1 min
# ==========================================================
# Koristi se kao dekorator na svakom osetljivom endpointu (upload, KYC, RFQ...).
# Ograničava broj poziva iste IP-e u prozoru; nezavisno od globalnog IP blacklist-a
# (koji se aktivira samo za login brute force).

_endpoint_hits = {}   # (endpoint_name, ip) -> [timestamp, ...]
_endpoint_hits_lock = threading.Lock()


def rate_limit(max_per_minute=30, key='endpoint'):
    """Dekorator: dozvoljava max_per_minute zahteva po IP-i u minuti.
    key: string koji ide u ključ (podržava razdvojene limite po ruti)."""
    from functools import wraps as _wraps

    def _decorator(fn):
        @_wraps(fn)
        def _wrapped(*args, **kwargs):
            ip = get_client_ip() or 'unknown'
            if ip in FirewallCache.whitelist:
                return fn(*args, **kwargs)
            now = time.time()
            k = (key or fn.__name__, ip)
            with _endpoint_hits_lock:
                bucket = _endpoint_hits.get(k, [])
                bucket = [t for t in bucket if now - t < 60]
                if len(bucket) >= max_per_minute:
                    _util_logger.warning(f'RATE_LIMIT hit on {k[0]} from {ip}')
                    log_audit('SECURITY_BLOCK', 'firewall',
                              f'Rate limit exceeded on {k[0]} from {ip}', is_suspicious=True)
                    return jsonify({"error": "RATE_LIMIT_EXCEEDED"}), 429
                bucket.append(now)
                _endpoint_hits[k] = bucket
            return fn(*args, **kwargs)
        return _wrapped
    return _decorator


# ==========================================================
#  TOKEN VERSION — invalidira sve sesije kad korisnik menja lozinku
# ==========================================================

def bump_user_token_version(user_id):
    """Povećava token_version korisnika za 1. Svaka sesija koja u sebi drži
    stariji broj biće odbijena pri sledećem zahtevu (login_required)."""
    if not user_id:
        return
    try:
        with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
            conn.execute('PRAGMA busy_timeout=15000;')
            conn.execute("UPDATE users SET token_version = COALESCE(token_version, 1) + 1 WHERE id = ?", (user_id,))
            conn.commit()
    except Exception:
        _util_logger.warning(f'bump_user_token_version({user_id}) failed', exc_info=True)


def get_user_token_version(user_id):
    """Vraća aktuelnu token_version iz baze (1 ako nije postavljena)."""
    if not user_id:
        return 1
    try:
        with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
            c = conn.cursor()
            c.execute("SELECT token_version FROM users WHERE id = ?", (user_id,))
            row = c.fetchone()
        return int(row[0]) if row and row[0] is not None else 1
    except Exception:
        return 1