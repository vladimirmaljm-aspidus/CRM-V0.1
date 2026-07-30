"""V24.4 SUPABASE-ONLY: firewall admin routes (blacklist, whitelist, rate-limit settings)."""
from flask import Blueprint, jsonify, request, session
import ipaddress
from utils import (login_required, log_audit, FirewallCache, decrypt_data, encrypt_data,
                   load_firewall_settings, DEFAULT_FIREWALL_SETTINGS)
import supabase_store as store

firewall_bp = Blueprint('firewall', __name__, url_prefix='/api/firewall')


def is_admin():
    """Dozvoljeno adminu ili radniku kome je admin dodelio 'firewall_manage' permisiju."""
    role = session.get('role', '')
    if role and role.lower() == 'admin':
        return True
    if 'user_id' not in session:
        return False
    u = store.get_user_by_id(session['user_id']) or {}
    perms = u.get('permissions') or {}
    if isinstance(perms, str):
        try:
            import json as _j
            perms = _j.loads(perms)
        except Exception:
            perms = {}
    return bool(isinstance(perms, dict) and perms.get('firewall_manage'))


@firewall_bp.route('/status', methods=['GET'])
@login_required
def get_firewall_status():
    if not is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403
    attempts = []
    try:
        from data_layer import select as _dl_select
        rows = _dl_select('audit_logs',
                          filters={'action': ('in', ['LOGIN', 'SECURITY'])},
                          order='-timestamp', limit=100) or []
        for row in rows:
            attempts.append({
                "username": row.get('username'),
                "ip_address": row.get('ip_address'),
                "action": row.get('action'),
                "details": row.get('details'),
                "timestamp": row.get('timestamp'),
                "location": row.get('location'),
                "is_suspicious": bool(row.get('is_suspicious'))
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


@firewall_bp.route('/settings', methods=['GET'])
@login_required
def get_firewall_settings():
    if not is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403
    stored = {}
    try:
        enc = store.get_setting('firewall')
        if enc:
            stored = decrypt_data(enc) or {}
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


@firewall_bp.route('/config', methods=['POST'])
@login_required
def save_firewall_config():
    if not is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403
    payload = request.get_json(silent=True) or {}
    # Whitelist
    cleaned_wl = set()
    for ip in payload.get('whitelist') or []:
        s = str(ip or '').strip()
        if not s: continue
        try:
            ipaddress.ip_address(s)
            cleaned_wl.add(s)
        except ValueError:
            pass
    FirewallCache.whitelist = cleaned_wl
    # Blacklist
    cleaned_bl = set()
    for ip in payload.get('blacklist') or []:
        s = str(ip or '').strip()
        if not s: continue
        try:
            ipaddress.ip_address(s)
            cleaned_bl.add(s)
        except ValueError:
            pass
    cleaned_bl -= cleaned_wl
    FirewallCache.blacklist = cleaned_bl

    # Preserve existing settings
    fw_settings = {}
    try:
        enc = store.get_setting('firewall')
        if enc:
            existing = decrypt_data(enc) or {}
            if isinstance(existing, dict):
                fw_settings = existing
    except Exception:
        pass

    for src_key, dest_key in [('max_login', 'max_login_attempts'),
                              ('max_portal', 'max_portal_requests_per_min')]:
        if src_key in payload:
            try:
                v = int(payload[src_key])
                if 1 <= v <= 10_000_000:
                    fw_settings[dest_key] = v
            except (TypeError, ValueError):
                pass

    try:
        fw_settings['whitelist'] = sorted(cleaned_wl)
        fw_settings['blacklist'] = sorted(cleaned_bl)
        store.set_setting('firewall', encrypt_data(fw_settings))
    except Exception as e:
        return jsonify({"error": "SAVE_FAILED", "message": str(e)}), 500

    applied = load_firewall_settings()
    log_audit('FIREWALL_MANAGE', 'firewall',
              f'Admin bulk-saved firewall config: {len(cleaned_wl)} whitelist, {len(cleaned_bl)} blacklist')
    return jsonify({
        "status": "success",
        "whitelist_count": len(cleaned_wl),
        "blacklist_count": len(cleaned_bl),
        "applied": applied,
    })


@firewall_bp.route('/unblock', methods=['POST'])
@login_required
def unblock_ip():
    if not is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403
    data = request.get_json(silent=True) or {}
    ip = str(data.get('ip') or '').strip()
    if not ip:
        return jsonify({"error": "MISSING_IP"}), 400
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return jsonify({"error": "INVALID_IP_FORMAT"}), 400
    was_blocked = ip in FirewallCache.blacklist
    FirewallCache.blacklist.discard(ip)
    if ip in FirewallCache.login_attempts:
        del FirewallCache.login_attempts[ip]
    log_audit('FIREWALL_MANAGE', 'firewall',
              f'Admin unblocked IP {ip} ({"was in blacklist" if was_blocked else "not blocked"})')
    return jsonify({"status": "success", "ip": ip, "was_blocked": was_blocked})


@firewall_bp.route('/settings', methods=['POST'])
@login_required
def save_firewall_settings():
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
        store.set_setting('firewall', encrypt_data(clean))
    except Exception:
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500
    applied = load_firewall_settings()
    log_audit('EDIT', 'firewall', f"Admin updated firewall settings: {clean}")
    return jsonify({"status": "success", "applied": applied})
