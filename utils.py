import datetime
import time
import uuid
import os
import json
import secrets
import logging
import threading
import urllib.request
import ipaddress
from functools import wraps
from flask import request, session, jsonify, redirect, url_for
from config import ALLOWED_EXTENSIONS, ENCRYPTION_KEY
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
    # V22.04.05 (user's improvement): defense against double-extension bypass
    # (npr. shell.php.jpg, backdoor.jsp.png). Ekstenzija POSLEDNJA mora biti u
    # dozvoljenom skupu, i NIJEDNA prethodna ekstenzija (dot-separated segment)
    # ne sme biti u opasnom skupu.
    if not filename or '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False
    dangerous_extensions = {
        'php', 'php3', 'php4', 'php5', 'php7', 'phtml', 'pht',
        'asp', 'aspx', 'asa', 'ascx', 'ashx', 'asmx', 'asax',
        'jsp', 'jspx', 'jspa', 'jsw', 'jsv',
        'cgi', 'pl', 'py', 'rb', 'sh', 'bash', 'bat', 'cmd', 'com',
        'exe', 'dll', 'so', 'dylib', 'msi', 'scr', 'vbs', 'vbe',
        'wsf', 'wsh', 'ps1', 'psm1', 'psd1',
        'htaccess', 'htpasswd', 'ini', 'conf', 'cfg', 'env',
        'html', 'htm', 'shtml', 'xhtml',
        'js', 'mjs', 'ts', 'tsx', 'jsx',
        'svg', 'xml', 'xsl', 'xslt', 'rss',
    }
    parts = filename.lower().split('.')
    # All middle segments (between name and final ext) must not be dangerous
    for mid in parts[1:-1]:
        if mid in dangerous_extensions:
            return False
    return True

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
    # Bez request contexta (npr. iz background thread-a) request.headers baca
    # RuntimeError. U tom slucaju nemamo IP i vraćamo None.
    try:
        ip_addr = request.headers.get('X-Forwarded-For', request.remote_addr)
    except RuntimeError:
        return None
    if ip_addr and ',' in ip_addr:
        ip_addr = ip_addr.split(',')[0].strip()
    return ip_addr

def log_audit(action, module, details, is_suspicious=False, location="N/A"):
    # BEZBEDNO IZ POZADINSKIH THREAD-OVA: session/request su Werkzeug LocalProxy
    # objekti koji bacaju RuntimeError("Working outside of request context") ako se
    # dohvate bez aktivnog HTTP zahteva. Migration thread, backup loop, digest
    # loop — svi zovu log_audit i ranije su padali (vidi server log 2026-07-27
    # 18:00:17 i dalje). Sada svaka proxy operacija ide u try/except sa fallback-om.
    try:
        user_id = session.get('user_id', 'SYSTEM')
        username = session.get('username', 'SYSTEM_THREAD')
    except RuntimeError:
        user_id = 'SYSTEM'
        username = 'SYSTEM_THREAD'

    try:
        ip_addr = get_client_ip()
    except RuntimeError:
        ip_addr = None

    try:
        browser_name = request.user_agent.browser or "UNKNOWN_BROWSER"
        browser_version = request.user_agent.version or ""
        os_platform = request.user_agent.platform or "UNKNOWN_OS"
        formatted_user_agent = f"{browser_name} {browser_version} ({os_platform})"
    except RuntimeError:
        formatted_user_agent = "SYSTEM/THREAD"

    try:
        http_method = request.method
        requested_url = request.path
    except RuntimeError:
        http_method = "THREAD"
        requested_url = "-"

    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')

    # Geolokacija je sada kesirana (vidi get_ip_info) pa vise ne usporava svaki upis.
    network_info, ip_location, tz_info = get_ip_info(ip_addr) if ip_addr else ("SYSTEM_THREAD", "N/A", "N/A")

    if location in ["N/A", "Unknown", "GPS_DENIED", "DENIED"]:
        if ip_location != "N/A":
            location = ip_location

    extended_details = f"[{http_method} {requested_url}] | {details} | NET: {network_info} | TZ: {tz_info}"

    # V24.1 SUPABASE-ONLY: audit ide direktno u Supabase. Nikada ne baca —
    # log_audit se poziva na SVAKOM requestu i ne sme da srusi request.
    try:
        from data_layer import insert as _dl_insert
        _dl_insert('audit_logs', {
            'sync_id': str(uuid.uuid4()),
            'user_id': user_id,
            'username': username,
            'action': action,
            'module': module,
            'details': extended_details[:2000],
            'ip_address': ip_addr,
            'user_agent': (formatted_user_agent or '')[:200],
            'timestamp': timestamp,
            'is_suspicious': bool(is_suspicious),
            'location': location,
        })
    except Exception:
        # Log-only fallback ako Supabase nije dostupan — nastavi request.
        # (Ne pise nista drugde — to je ranije bio SQLite audit fajl koji
        # je Render brisao pri deploy-u, pa je audit ionako bio prolazan.)
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

        # ROUND F: per-session revoke — user je ubio TU sesiju iz Security Center-a.
        # Ovo je nezavisno od token_version (koji ubija SVE sesije). Individualni
        # revoke se koristi kad user vidi sumnjivi Chrome/Windows u listi i ubije
        # samo njega, a nastavlja da radi na svom laptopu.
        sid = session.get('session_id')
        if sid:
            try:
                from routes.security_center import is_session_revoked, touch_session
                if is_session_revoked(sid):
                    session.clear()
                    if is_api:
                        return jsonify({"error": "SESSION_REVOKED"}), 401
                    return redirect(url_for('auth.login'))
                touch_session(sid)  # best-effort update last_seen_at
            except Exception:
                pass  # ne blokiramo ako security_center nije registrovan

        # ROUND F: must_change_password gate — dozvoli samo pristup Security Center-u
        # i change-password endpoint-u dok user ne postavi novu lozinku.
        try:
            allowed_paths = ('/profile/security', '/api/security/', '/api/auth/change_password',
                             '/api/users/change-password', '/api/auth/me', '/api/auth/logout',
                             '/api/csrf/token', '/static/')
            if not any(request.path.startswith(p) for p in allowed_paths):
                import supabase_store as _store
                _u = _store.get_user_by_id(session['user_id']) or {}
                if _u.get('must_change_password'):
                    if is_api:
                        return jsonify({"error": "MUST_CHANGE_PASSWORD",
                                        "redirect": "/profile/security#password"}), 403
                    return redirect('/profile/security#password')
        except Exception:
            pass  # DB hiccup ne sme da blokira svaki request

        return f(*args, **kwargs)
    return decorated_function

def require_perm(perm_key):
    """V23.1 — dekorator koji proverava granular permission za tekućeg user-a.
    Admin uvek prolazi. Za ostale: gleda users.permissions JSON dict.

    Primer:
        @require_perm('deals.delete')
        def delete_deal(...): ...

    Ako user nema permisiju, vraća 403 sa objašnjenjem. Zove ga se PORED
    @login_required (mora prvo biti autentifikovan)."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('role') == 'admin':
                return f(*args, **kwargs)
            uid = session.get('user_id')
            if not uid:
                return jsonify({'error': 'UNAUTHORIZED'}), 401
            try:
                import supabase_store as _store
                _u = _store.get_user_by_id(uid) or {}
                perms = _u.get('permissions') or {}
                if isinstance(perms, str):
                    try: perms = json.loads(perms)
                    except Exception: perms = {}
            except Exception:
                perms = {}
            if not perms.get(perm_key):
                log_audit('SECURITY', 'permissions',
                          f'User {session.get("username")} blocked from action requiring {perm_key}',
                          is_suspicious=True)
                return jsonify({
                    'error': 'PERMISSION_DENIED',
                    'required_permission': perm_key,
                    'message': f'You need "{perm_key}" permission for this action. Contact your admin.',
                }), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


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
    # V23.1: javni Round F endpointi bez sesije (magic-link request, lockout
    # provera, webhook signup) — sesije još nema, CSRF nije primenljiv.
    if request.path in ('/api/security/magic-link',
                        '/api/security/lockout/status',
                        '/api/webhook/supabase-auth'):
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
    i primenjuje na FirewallCache. Zove se na startup i posle svakog admin save.

    V24.1 SUPABASE-ONLY: čita iz `settings` tabele preko
    `supabase_store.get_setting('firewall')`, pa dešifruje Fernet-om
    (`decrypt_data`). Ako ključ ne postoji ili je payload nevalidan, vraća
    default vrednosti (definisane iznad).
    """
    merged = dict(DEFAULT_FIREWALL_SETTINGS)
    try:
        import supabase_store as _store
        raw = _store.get_setting('firewall')
        if raw:
            stored = decrypt_data(raw)
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
                # restore whitelist/blacklist arrays (dodato u v22 P0 batch-u —
                # ranije se gubilo posle restart-a servera jer se čuvalo samo u
                # in-memory FirewallCache)
                import ipaddress as _ip
                for src_key, cache_set in (('whitelist', FirewallCache.whitelist),
                                            ('blacklist', FirewallCache.blacklist)):
                    lst = stored.get(src_key) or []
                    if isinstance(lst, list):
                        for ip in lst:
                            s = str(ip or '').strip()
                            if not s: continue
                            try:
                                _ip.ip_address(s)
                                cache_set.add(s)
                            except ValueError:
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
    Sve u pozadinskom thread-u pa ne blokira request handling.

    V24.1 SUPABASE-ONLY: audit retention prune ide preko `data_layer.delete`
    umesto SQLite DELETE; nema `sqlite3.connect(AUDIT_DB_FILE)`.
    """
    import gc
    while True:
        try:
            # 1) audit log retention — briše NE-suspicious slogove starije od N dana.
            #    Suspicious se zadržavaju trajno (forenzicki trag).
            days = int(FirewallCache.settings.get('audit_retention_days', 180))
            if days > 0:
                cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat().replace('+00:00', 'Z')
                try:
                    from data_layer import delete as _dl_delete
                    n = _dl_delete(
                        'audit_logs',
                        {'timestamp': ('lt', cutoff),
                         'is_suspicious': ('eq', False)},
                    )
                    n = int(n or 0)
                    if n:
                        _util_logger.info(f'HOUSEKEEPING: purged {n} audit rows older than {days}d')
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

            # 4) email queue retry — svakih 60s (mora se odvojiti u zaseban loop
            #    da ne čeka sat vremena između pokušaja). Ovde samo trigger prvi put.
            try:
                from utils_email import process_email_queue
                stats = process_email_queue(max_batch=20)
                if stats['processed']:
                    _util_logger.info(f'HOUSEKEEPING: email queue {stats}')
            except Exception:
                _util_logger.warning('email queue processing failed', exc_info=True)

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
    # V24.1 SUPABASE-ONLY: backup loop je UKLONJEN — Supabase ima sopstveni backup
    # sistem (PITR + daily snapshots + WAL archiving). Nema potrebe za lokalnim
    # SQLite .db backup-om jer baza više ne postoji na lokalnom FS-u. Ako neko
    # želi dodatni off-site mirror, konfiguriše Supabase Storage bucket unutar
    # Supabase Dashboard-a (Project → Storage).
    # Email queue retry loop — svakih 60s. Odvojen thread jer 1h je predugo
    # za retry neuspelih mejlova (klijent ne sme da čeka).
    tq = threading.Thread(target=_email_queue_loop, name='crm-email-queue', daemon=True)
    tq.start()
    # v22.5: Notification digest — dnevni sažetak adminu (opt-in preko env-a).
    td = threading.Thread(target=_notification_digest_loop, name='crm-notif-digest', daemon=True)
    td.start()


def _email_queue_loop():
    """Retry-uje neuspele mejlove iz email_queue tabele svakih 60s."""
    time.sleep(30)  # čekaj startup
    while True:
        try:
            from utils_email import process_email_queue
            process_email_queue(max_batch=5)
        except Exception:
            _util_logger.warning('email queue loop iteration failed', exc_info=True)
        time.sleep(60)


# ==========================================================
#  AUTOMATSKI ŠIFROVANI BACKUP BAZE — dnevni snapshot, zadrži poslednjih 14
# ==========================================================
# V24.1 SUPABASE-ONLY: ovaj deo je ISKLJUČEN. Supabase ima sopstveni backup
# sistem (PITR — Point-in-Time Recovery, dnevni snapshot-ovi, WAL archiving,
# i opcioni Storage mirror). Lokalni Fernet-šifrovani .db backup-ovi su bili
# potrebni samo dok je aplikacija koristila SQLite fajl koji Render briše pri
# svakom deploy-u. Sada je baza u Supabase-u i nije na lokalnom FS-u.
#
# Funkcija ostaje definisana (sa praznim telom) radi kompatibilnosti — neki
# pozivaoci je možda još uvek referenciraju (npr. testovi).

def _backup_loop():
    """No-op u V24.1 SUPABASE-ONLY modu.

    Ranije je pravio dnevni Fernet-šifrovan snapshot svih .db fajlova i bri-
    sao starije od 14 dana. Supabase sada ima sopstveni backup sistem
    (Project Settings → Database → Backups):
      - Automated daily logical snapshots (7-day retention)
      - Point-in-Time Recovery (PITR) do 7 dana unazad
      - Manual snapshot-ovi iz Dashboard-a
    Off-site mirror u Supabase Storage bucket je opciono konfigurisati
    nezavisno od aplikacije.
    """
    pass


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
    """V24.1 SUPABASE-ONLY: povecava token_version korisnika za 1.
    Svaka sesija koja u sebi drzi stariji broj bice odbijena pri sledecem
    zahtevu (login_required)."""
    if not user_id:
        return
    try:
        import supabase_store as _store
        _store.bump_token_version(user_id)
    except Exception:
        _util_logger.warning(f'bump_user_token_version({user_id}) failed', exc_info=True)


def get_user_token_version(user_id):
    """V24.1 SUPABASE-ONLY."""
    if not user_id:
        return 1
    try:
        import supabase_store as _store
        u = _store.get_user_by_id(user_id) or {}
        return int(u.get('token_version', 1) or 1)
    except Exception:
        return 1

# ==========================================================
# BATCH D2 — NOTIFICATION DIGEST (email admin sažetak jednom dnevno)
# ==========================================================
# Ako je NOTIF_DIGEST_ENABLED=true i imamo SMTP_HOST, background thread
# jednom dnevno (u 8:00 UTC) posalje admin-u kratak sažetak:
#   - Broj novih portal KYC prijava
#   - Broj novih RFQ-ova
#   - Broj neuspešnih mejlova u queue-u
#   - Broj partnera sa lastModified > 1 godina
#   - Broj deala sa payment overdue
# Ako je BROJ = 0, mejl se ne šalje (bez spama).

def _notification_digest_loop():
    """V24.1 SUPABASE-ONLY: daily digest za admina (failed/dead mejlovi).

    Broj neuspelih mejlova u `email_queue` se čita preko `data_layer.count`
    umesto direktnog SQLite SELECT COUNT(*). Ostatak logike (8:00 UTC trigger,
    env-gate, send_branded_admin_message) je netaknut.
    """
    import time as _t, datetime as _dt, os as _os
    # Sacekaj 5 min posle starta pa udji u loop
    _t.sleep(300)
    last_sent_day = None
    while True:
        try:
            if not _os.environ.get('NOTIF_DIGEST_ENABLED', '').strip().lower() in ('1','true','yes','on'):
                _t.sleep(3600)
                continue
            now = _dt.datetime.now(_dt.timezone.utc)
            # Sve u 8:00 UTC jednom dnevno
            if now.hour != 8 or last_sent_day == now.date():
                _t.sleep(600)  # cekaj 10 min i pokusaj ponovo
                continue
            # Sakupi metrike — broj failed/dead mejlova u email_queue.
            # V24.1: Supabase preko data_layer.count.
            failed = 0
            try:
                from data_layer import count as _dl_count
                failed = int(_dl_count('email_queue',
                                       filters={'status': ('in', ['failed', 'dead'])}) or 0)
            except Exception as _e:
                _util_logger.warning(f'NOTIF_DIGEST: metric fetch failed: {_e}')
                _t.sleep(3600); continue

            # Nema sta da javis ako nema failed mejlova (mvp)
            if failed == 0:
                last_sent_day = now.date()
                _t.sleep(3600)
                continue

            # Posalji admin-u
            admin_email = _os.environ.get('ADMIN_EMAIL', '').strip()
            if admin_email:
                try:
                    from utils_email import send_branded_admin_message
                    body = (f"<h2>Daily digest — Aspidus CRM</h2>"
                            f"<p><b>{failed}</b> emails failed to send in the last cycle. "
                            f"Open <a href='https://aspidus.pythonanywhere.com/admin/mail-queue'>Mail Queue</a> to retry or delete.</p>")
                    send_branded_admin_message(admin_email, "🔔 Aspidus CRM — Daily digest", body)
                    _util_logger.info(f'NOTIF_DIGEST: sent to {admin_email}')
                except Exception as _e:
                    _util_logger.warning(f'NOTIF_DIGEST: send failed: {_e}')
            last_sent_day = now.date()
        except Exception:
            _util_logger.exception('NOTIF_DIGEST: iteration failed')
        _t.sleep(3600)
