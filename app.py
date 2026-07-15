import os
import time
import logging
from datetime import timedelta
from flask import Flask, render_template, jsonify, request, abort
from werkzeug.middleware.proxy_fix import ProxyFix

from config import SECRET_KEY, MAX_CONTENT_LENGTH, UPLOAD_FOLDER, PORTAL_UPLOAD_FOLDER
from database import init_db
from utils import (FirewallCache, log_audit, verify_csrf_token, _ensure_csrf_token,
                   load_firewall_settings, start_housekeeping)

from routes.auth import auth_bp
from routes.users import users_bp
from routes.files import files_bp
from routes.audit import audit_bp
from routes.data import data_bp
from routes.comms import comms_bp
from routes.portal import portal_bp
from routes.firewall import firewall_bp
from routes.vault import vault_bp
from routes.system import system_bp

# Konfiguracija sistemskog logovanja (sprečava ispisivanje osetljivih grešaka korisnicima)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# OBAVEZNO ZA PRODUKCIJU (Nginx/Cloudflare): Rešava problem lažiranja IP adresa
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

if SECRET_KEY == 'aspidus-pro-secure-vault-x92f8j3pL-2026-vQpZm1!k9':
    logger.warning("SECURITY WARNING: SECRET_KEY environment variable is not set. Using the hardcoded fallback key from source code is UNSAFE in production (session cookies can be forged). Set the SECRET_KEY env var before deploying.")

app.secret_key = SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
# ISPRAVKA: ranije nije bio postavljen, pa je /portal_uploads/<filename> (u
# routes/portal/actions.py) uvek bacao KeyError -> 500 pri preuzimanju KYC/portal fajlova.
app.config['PORTAL_UPLOAD_FOLDER'] = PORTAL_UPLOAD_FOLDER

# BRUTALNA ZAŠTITA SESIJE - VOJNI STANDARD
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=2)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Strict'

# Sesijski kolačić sa Secure flagom se NE ŠALJE preko nešifrovanog HTTP-a (npr.
# http://localhost), pa bi uključivanje ovoga u lokalnom razvoju "odjavljivalo"
# korisnika odmah nakon prijave. Zato je podrazumevano isključeno (za localhost),
# a u PRODUKCIJI (pravi domen sa HTTPS-om) OBAVEZNO postaviti env SESSION_COOKIE_SECURE=true.
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', 'false').lower() in ('true', '1', 'yes')
if not app.config['SESSION_COOKIE_SECURE']:
    logger.warning("NAPOMENA: SESSION_COOKIE_SECURE je isključen (dev/localhost). U produkciji sa HTTPS-om postaviti env SESSION_COOKIE_SECURE=true.")

# Inicijalizacija/migracija baze pri učitavanju aplikacije.
# VAŽNO: ranije se init_db() zvao samo u __main__ bloku, pa se pod
# `flask run` i gunicorn-om (produkcija) nije izvršavao — nove migracije
# (npr. kolona 'signature') se ne bi primenile. Sada se izvršava uvek.
init_db()

# Učitaj firewall/session postavke (admin ih menja preko Settings modula) i
# pokreni pozadinsko održavanje (rotacija audit log-a, čišćenje kešova).
load_firewall_settings()
start_housekeeping()

# Registracija modula (Blueprints)
app.register_blueprint(auth_bp)
app.register_blueprint(users_bp)
app.register_blueprint(files_bp)
app.register_blueprint(audit_bp)
app.register_blueprint(data_bp)
app.register_blueprint(comms_bp)
app.register_blueprint(portal_bp)
app.register_blueprint(firewall_bp)
app.register_blueprint(vault_bp)
app.register_blueprint(system_bp)

@app.before_request
def enforce_csrf():
    """Odbija POST/PUT/DELETE/PATCH bez validnog X-CSRF-Token header-a.
    Portal rute i login se preskaču (imaju drugu odbranu — OTP odn. brute-force
    limit). GET/HEAD/OPTIONS su prirodno idempotentni."""
    if request.path.startswith('/static'):
        return
    if not verify_csrf_token():
        log_audit('SECURITY', 'system', f'CSRF token missing/invalid for {request.method} {request.path}', is_suspicious=True)
        return jsonify({"error": "CSRF_TOKEN_INVALID"}), 403

@app.before_request
def check_crm_session_timeout():
    from flask import session as flask_session
    if 'user_id' in flask_session and not request.path.startswith('/portal') and not request.path.startswith('/static'):
        now = time.time()
        last = flask_session.get('last_active', flask_session.get('login_time', 0))
        ttl = int(FirewallCache.settings.get('crm_inactivity', 1200))
        if now - last > ttl:
            username = flask_session.get('username', 'unknown')
            log_audit('SECURITY', 'system', f'Session expired due to inactivity for user: {username}', is_suspicious=False)
            flask_session.clear()
            if request.path.startswith('/api/'):
                return jsonify({"error": "SESSION_EXPIRED"}), 401
            return
        flask_session['last_active'] = now

@app.before_request
def limit_login_attempts():
    """
    Optimizovan i osiguran sistem za blokiranje Brute Force napada.
    """
    if request.endpoint == 'auth.login' and request.method == 'POST':
        ip = request.remote_addr
        
        if ip in FirewallCache.whitelist:
            return
        
        now = time.time()
        
        # Očisti stare pokušaje
        attempts = FirewallCache.login_attempts.get(ip, [])
        valid_attempts = [t for t in attempts if now - t < 300]
        
        # Provera pre nego što dodamo novi pokušaj (sprečava produžavanje kazne u beskraj)
        if len(valid_attempts) >= FirewallCache.settings.get('max_login', 10):
            logger.warning(f"Brute force attempt blocked from IP: {ip}")
            log_audit('SECURITY_BLOCK', 'firewall', f'IP {ip} blocked due to excessive login attempts.', is_suspicious=True)
            abort(429, description="Too many login attempts. Your IP address is blocked for 5 minutes for security reasons.")
        
        # Ako nije blokiran, upiši trenutni pokušaj
        valid_attempts.append(now)
        FirewallCache.login_attempts[ip] = valid_attempts

# === GLOBALNI HVATAČI GREŠAKA (ZABRANJUJU CURENJE INFORMACIJA) ===

@app.errorhandler(413)
def request_entity_too_large(error):
    logger.warning(f"Payload too large from IP: {request.remote_addr}")
    log_audit('SECURITY_WARNING', 'upload', f'Payload too large attempt from IP {request.remote_addr}', is_suspicious=True)
    return jsonify({"error": "File exceeds the maximum allowed size on the server."}), 413

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"error": str(e.description)}), 429

@app.errorhandler(404)
def not_found_error(error):
    if request.path.startswith('/api/'):
        return jsonify({"error": "Requested resource not found."}), 404
    # Dodat pravilan HTTP status kod
    return render_template('index.html'), 404

@app.errorhandler(500)
def internal_server_error(error):
    """
    Apsolutna zaštita: Ako kod pukne, napadač ne dobija Python Traceback.
    Sve se beleži isključivo u zaštićeni interni log.
    """
    logger.error(f"Internal Server Error: {request.path} - {str(error)}", exc_info=True)
    log_audit('CRITICAL_ERROR', 'system', f"Endpoint {request.path} failed.", is_suspicious=True)
    if request.path.startswith('/api/'):
        return jsonify({"error": "Internal Server Error. The issue has been logged and reported to the administrators."}), 500
    # Dodat pravilan HTTP status kod
    return render_template('index.html'), 500

@app.errorhandler(405)
def method_not_allowed(error):
    if request.path.startswith('/api/'):
        return jsonify({"error": "Method not allowed for this endpoint."}), 405
    # Dodat pravilan HTTP status kod
    return render_template('index.html'), 405

@app.after_request
def apply_brutal_security_headers(response):
    """
    Aplikacija bezbednosnih zaglavlja i onemogućavanje keširanja za osetljive rute.
    """
    response.cache_control.no_store = True
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    # HSTS - Prisiljava pretraživač da narednih godinu dana komunicira isključivo preko HTTPS-a
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'

    # Dodatni sloj: onemogući curenje referrer-a, isključi opasne browser API-je
    # (kamera/mikrofon/USB/serial), izoluj window od cross-origin popup-a, i
    # blokiraj Adobe/Silverlight cross-domain policy fajlove.
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = (
        'accelerometer=(), autoplay=(), camera=(), clipboard-read=(self), '
        'display-capture=(), encrypted-media=(), fullscreen=(self), '
        'geolocation=(self), gyroscope=(), hid=(), magnetometer=(), '
        'microphone=(), midi=(), payment=(), picture-in-picture=(), '
        'publickey-credentials-get=(self), screen-wake-lock=(), '
        'serial=(), sync-xhr=(self), usb=(), xr-spatial-tracking=()'
    )
    response.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
    response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
    response.headers['X-Permitted-Cross-Domain-Policies'] = 'none'
    
    # Sadržajna polisa (CSP) je pooštrena
    # Ograničeni su izvori slika na tačno definisane sigurne lokacije, umesto divljeg "http://*"
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
        "img-src 'self' data: blob: https://googleusercontent.com; "
        "frame-src 'self' blob:; "
        "connect-src 'self' http://ip-api.com;"
    )
    response.headers['Content-Security-Policy'] = csp
    
    # Brisanje Server zaglavlja kako napadači ne bi znali koju verziju softvera koristimo
    response.headers.pop('Server', None)
    
    return response

@app.route('/api/csrf/token', methods=['GET'])
def csrf_token_endpoint():
    """Vraća aktuelni CSRF token trenutne sesije. Frontend ga poziva pri startu
    i posle svakog login-a; drži ga u memoriji i šalje u X-CSRF-Token header."""
    return jsonify({"csrf_token": _ensure_csrf_token()})


@app.route('/')
def index():
    _ensure_csrf_token()
    return render_template('index.html')

if __name__ == '__main__':
    # Inicijalizacija baze pre pokretanja servera
    init_db()
    
    # Pokretanje servera (OBAVEZNO debug=False za sigurnost)
    app.run(debug=False, host='0.0.0.0', port=5000)