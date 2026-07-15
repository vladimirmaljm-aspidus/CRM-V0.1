from flask import Blueprint, jsonify, request, session
import sqlite3
import ipaddress
from utils import (login_required, log_audit, FirewallCache, decrypt_data, encrypt_data,
                   load_firewall_settings, DEFAULT_FIREWALL_SETTINGS)
from config import AUDIT_DB_FILE, DB_FILE

firewall_bp = Blueprint('firewall', __name__, url_prefix='/api/firewall')

def is_admin():
    """Dozvoljeno adminu ili radniku kome je admin dodelio 'firewall_manage' permisiju."""
    role = session.get('role', '')
    if role and role.lower() == 'admin':
        return True
    if 'user_id' not in session:
        return False
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    try:
        c = conn.cursor()
        c.execute('SELECT permissions FROM users WHERE id=?', (session['user_id'],))
        row = c.fetchone()
    finally:
        conn.close()
    perms = decrypt_data(row[0]) if row and row[0] else {}
    return bool(isinstance(perms, dict) and perms.get('firewall_manage'))

@firewall_bp.route('/status', methods=['GET'])
@login_required
def get_firewall_status():
    if not is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403

    attempts = []
    try:
        with sqlite3.connect(AUDIT_DB_FILE, timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute('''SELECT username, ip_address, action, details, timestamp, location, is_suspicious 
                         FROM audit_logs 
                         WHERE action IN ('LOGIN', 'SECURITY') 
                         ORDER BY timestamp DESC LIMIT 100''')
            rows = c.fetchall()
            for row in rows:
                attempts.append({
                    "username": row["username"],
                    "ip_address": row["ip_address"],
                    "action": row["action"],
                    "details": row["details"],
                    "timestamp": row["timestamp"],
                    "location": row["location"],
                    "is_suspicious": bool(row["is_suspicious"])
                })
    except Exception:
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500

    return jsonify({
        "blacklist": list(FirewallCache.blacklist),
        "whitelist": list(FirewallCache.whitelist),
        "login_attempts_log": attempts
    }), 200

@firewall_bp.route('/blacklist/add', methods=['POST'])
@login_required
def add_to_blacklist():
    if not is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403

    data = request.get_json(silent=True) or {}
    ip = data.get('ip')
    if not ip:
        return jsonify({"error": "MISSING_IP"}), 400

    ip = ip.strip()
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return jsonify({"error": "INVALID_IP_FORMAT"}), 400

    FirewallCache.blacklist.add(ip)
    FirewallCache.whitelist.discard(ip)
    
    log_audit('FIREWALL_MANAGE', 'firewall', f"Admin manually blacklisted IP address: {ip}")
    return jsonify({"message": "IP_BLACKLISTED_SUCCESSFULLY", "ip": ip}), 200

@firewall_bp.route('/blacklist/remove', methods=['POST'])
@login_required
def remove_from_blacklist():
    if not is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403

    data = request.get_json(silent=True) or {}
    ip = data.get('ip')
    if not ip:
        return jsonify({"error": "MISSING_IP"}), 400

    ip = ip.strip()
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return jsonify({"error": "INVALID_IP_FORMAT"}), 400

    if ip in FirewallCache.blacklist:
        FirewallCache.blacklist.remove(ip)
        if ip in FirewallCache.login_attempts:
            del FirewallCache.login_attempts[ip]
            
        log_audit('FIREWALL_MANAGE', 'firewall', f"Admin manually removed IP address from blacklist: {ip}")
        return jsonify({"message": "IP_UNBLACKLISTED_SUCCESSFULLY", "ip": ip}), 200
    
    return jsonify({"error": "IP_NOT_FOUND"}), 404

@firewall_bp.route('/whitelist/add', methods=['POST'])
@login_required
def add_to_whitelist():
    if not is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403

    data = request.get_json(silent=True) or {}
    ip = data.get('ip')
    if not ip:
        return jsonify({"error": "MISSING_IP"}), 400

    ip = ip.strip()
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return jsonify({"error": "INVALID_IP_FORMAT"}), 400

    FirewallCache.whitelist.add(ip)
    FirewallCache.blacklist.discard(ip)
    
    log_audit('FIREWALL_MANAGE', 'firewall', f"Admin manually whitelisted IP address: {ip}")
    return jsonify({"message": "IP_WHITELISTED_SUCCESSFULLY", "ip": ip}), 200

@firewall_bp.route('/whitelist/remove', methods=['POST'])
@login_required
def remove_from_whitelist():
    if not is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403

    data = request.get_json(silent=True) or {}
    ip = data.get('ip')
    if not ip:
        return jsonify({"error": "MISSING_IP"}), 400

    ip = ip.strip()
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return jsonify({"error": "INVALID_IP_FORMAT"}), 400

    if ip in FirewallCache.whitelist:
        FirewallCache.whitelist.remove(ip)
        log_audit('FIREWALL_MANAGE', 'firewall', f"Admin manually removed IP address from whitelist: {ip}")
        return jsonify({"message": "IP_UNWHITELISTED_SUCCESSFULLY", "ip": ip}), 200

    return jsonify({"error": "IP_NOT_FOUND"}), 404


# ==========================================================
#  ADMIN-KONFIGURABILNE POSTAVKE (rate limit, TTL, retention)
# ==========================================================

@firewall_bp.route('/settings', methods=['GET'])
@login_required
def get_firewall_settings():
    """Vraća trenutne (aktivne) firewall postavke + spisak default-a i opisa.
    Admin ih menja preko POST /api/firewall/settings."""
    if not is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403

    stored = {}
    try:
        with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
            c = conn.cursor()
            c.execute("SELECT value FROM settings WHERE key='firewall'")
            row = c.fetchone()
        if row and row[0]:
            stored = decrypt_data(row[0]) or {}
    except Exception:
        stored = {}

    active = dict(DEFAULT_FIREWALL_SETTINGS)
    if isinstance(stored, dict):
        for k in DEFAULT_FIREWALL_SETTINGS:
            if k in stored:
                try:
                    v = int(stored[k])
                    if v > 0: active[k] = v
                except (TypeError, ValueError):
                    pass

    return jsonify({
        "active": active,
        "defaults": DEFAULT_FIREWALL_SETTINGS,
        "descriptions": {
            "max_login_attempts": "Failed logins allowed per IP in 5 minutes before auto-blacklist.",
            "max_portal_requests_per_min": "Requests-per-minute cap for anonymous portal endpoints.",
            "crm_inactivity_seconds": "CRM auto-logout after N seconds without activity.",
            "portal_session_seconds": "Maximum lifetime of a portal login session.",
            "portal_inactivity_seconds": "Portal auto-logout after N seconds of inactivity.",
            "portal_otp_seconds": "Portal OTP validity window in seconds.",
            "audit_retention_days": "Days to retain non-suspicious audit rows before automatic purge.",
        }
    })


@firewall_bp.route('/settings', methods=['POST'])
@login_required
def save_firewall_settings():
    """Snima nove vrednosti (samo poznati int ključevi) i odmah ih primenjuje."""
    if not is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403

    payload = request.get_json(silent=True) or {}
    clean = {}
    for k in DEFAULT_FIREWALL_SETTINGS:
        if k in payload:
            try:
                v = int(payload[k])
                if 1 <= v <= 10_000_000:
                    clean[k] = v
            except (TypeError, ValueError):
                pass

    try:
        with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('firewall', ?)",
                         (encrypt_data(clean),))
            conn.commit()
    except Exception:
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500

    applied = load_firewall_settings()
    log_audit('EDIT', 'firewall', f"Admin updated firewall settings: {clean}")
    return jsonify({"status": "success", "applied": applied})