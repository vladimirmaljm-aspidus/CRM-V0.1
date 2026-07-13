import datetime
import time
import uuid
import sqlite3
import json
import urllib.request
import ipaddress
from functools import wraps
from flask import request, session, jsonify, redirect, url_for
from config import DB_FILE, AUDIT_DB_FILE, ALLOWED_EXTENSIONS, ENCRYPTION_KEY
from cryptography.fernet import Fernet, InvalidToken

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