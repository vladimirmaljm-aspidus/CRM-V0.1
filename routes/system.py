"""System/health endpointi za admin monitoring.

Cilj: dati adminu instant pregled zdravlja instance (baze, disk, backup-i,
housekeeping thread) bez SSH-a na server. Ništa što otkriva čita se bez admin role.
"""
import os
import sqlite3
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify, session, request

from config import DB_FILE, PORTAL_DB_FILE, AUDIT_DB_FILE, DATA_DIR, UPLOAD_FOLDER, PORTAL_UPLOAD_FOLDER
from utils import login_required, log_audit, FirewallCache, encrypt_data

system_bp = Blueprint('system', __name__, url_prefix='/api/system')


def _is_admin():
    return session.get('role') == 'admin'


def _db_stats(path):
    if not os.path.exists(path):
        return {'exists': False}
    try:
        st = os.stat(path)
        row_counts = {}
        with sqlite3.connect(path, timeout=10.0) as conn:
            c = conn.cursor()
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [r[0] for r in c.fetchall()]
            for t in tables:
                try:
                    row_counts[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                except Exception:
                    row_counts[t] = None
        return {
            'exists': True,
            'size_bytes': st.st_size,
            'size_mb': round(st.st_size / 1024 / 1024, 2),
            'modified_at': datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat().replace('+00:00', 'Z'),
            'tables': row_counts,
        }
    except Exception as e:
        return {'exists': True, 'error': str(e)}


def _dir_size(path):
    total = 0
    if not os.path.isdir(path):
        return 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
    except Exception:
        pass
    return total


@system_bp.route('/health', methods=['GET'])
@login_required
def health():
    """Admin-only. Vraća JSON o stanju sistema — koristi se za dashboard indikator
    i za brzu dijagnostiku problema u produkciji."""
    if not _is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403

    backups_dir = os.path.join(DATA_DIR, 'backups')
    backups = []
    if os.path.isdir(backups_dir):
        try:
            for name in sorted(os.listdir(backups_dir), reverse=True)[:20]:
                if not name.endswith('.fernet'):
                    continue
                p = os.path.join(backups_dir, name)
                try:
                    st = os.stat(p)
                    backups.append({
                        'name': name,
                        'size_kb': round(st.st_size / 1024, 1),
                        'created_at': datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat().replace('+00:00', 'Z'),
                    })
                except Exception:
                    pass
        except Exception:
            pass

    try:
        vfs = os.statvfs(DATA_DIR)
        disk = {
            'total_gb': round(vfs.f_frsize * vfs.f_blocks / 1e9, 2),
            'available_gb': round(vfs.f_frsize * vfs.f_bavail / 1e9, 2),
            'used_percent': round((1 - vfs.f_bavail / vfs.f_blocks) * 100, 1) if vfs.f_blocks else None,
        }
    except Exception:
        disk = {'error': 'statvfs unavailable'}

    firewall_snapshot = {
        'blacklist_size': len(FirewallCache.blacklist),
        'whitelist_size': len(FirewallCache.whitelist),
        'active_ips_tracking_logins': len(FirewallCache.login_attempts),
        'settings': dict(FirewallCache.settings),
    }

    payload = {
        'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'databases': {
            'crm': _db_stats(DB_FILE),
            'portal': _db_stats(PORTAL_DB_FILE),
            'audit': _db_stats(AUDIT_DB_FILE),
        },
        'storage': {
            'data_dir': DATA_DIR,
            'uploads_size_mb': round(_dir_size(UPLOAD_FOLDER) / 1024 / 1024, 2),
            'portal_uploads_size_mb': round(_dir_size(PORTAL_UPLOAD_FOLDER) / 1024 / 1024, 2),
            'backups_total_mb': round(_dir_size(backups_dir) / 1024 / 1024, 2) if os.path.isdir(backups_dir) else 0,
            'disk': disk,
        },
        'backups': {
            'directory': backups_dir,
            'count_recent': len(backups),
            'latest': backups[0] if backups else None,
            'recent': backups,
        },
        'firewall': firewall_snapshot,
    }
    return jsonify(payload)


@system_bp.route('/backup/now', methods=['POST'])
@login_required
def backup_now():
    """Ručno pokreni backup ciklus (osim automatskog dnevnog).
    Radi u pozadinskom threadu — endpoint se odmah vraća."""
    if not _is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403
    import threading
    from utils import _backup_loop
    # Ne pokrećemo ceo loop — samo jedan pass. Kopiramo logiku:
    from cryptography.fernet import Fernet
    from utils import cipher_suite

    def _one_shot():
        try:
            backups_dir = os.path.join(DATA_DIR, 'backups')
            os.makedirs(backups_dir, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            for db_path in (DB_FILE, PORTAL_DB_FILE, AUDIT_DB_FILE):
                if not os.path.exists(db_path):
                    continue
                tmp = os.path.join(backups_dir, f'.tmp_{os.path.basename(db_path)}')
                src = sqlite3.connect(db_path, timeout=30.0)
                dst = sqlite3.connect(tmp, timeout=30.0)
                with dst:
                    src.backup(dst)
                dst.close(); src.close()
                with open(tmp, 'rb') as f: raw = f.read()
                out = os.path.join(backups_dir, f'{os.path.basename(db_path)}.{ts}.MANUAL.fernet')
                with open(out, 'wb') as f: f.write(cipher_suite.encrypt(raw))
                os.remove(tmp)
                try: os.chmod(out, 0o600)
                except Exception: pass
        except Exception:
            pass

    threading.Thread(target=_one_shot, daemon=True).start()
    log_audit('CREATE', 'system', 'Admin-triggered manual backup started', is_suspicious=False)
    return jsonify({"status": "success", "message": "Backup started in background."})


# ==========================================================
#  OTP DELIVERY CONFIG — transactional email provider + magic link
# ==========================================================

@system_bp.route('/otp_delivery', methods=['GET'])
@login_required
def get_otp_delivery():
    """Vraća redigovanu konfiguraciju (API ključ se ne otkriva) — koristi Settings UI."""
    if not _is_admin():
        return jsonify({"error": "UNAUTHORIZED"}), 403
    from mail_providers import config_summary
    return jsonify(config_summary())


@system_bp.route('/otp_delivery', methods=['POST'])
@login_required
def set_otp_delivery():
    """Snima OTP delivery konfiguraciju. Encrypted u settings.otpMailProvider.
    API ključ se traži samo kad se menja provider ili kad admin eksplicitno pošalje
    'change_api_key: true' i novi 'api_key' — u suprotnom čuvamo postojeći ključ."""
    if not _is_admin():
        return jsonify({"error": "UNAUTHORIZED"}), 403
    import sqlite3, json
    from utils import decrypt_data
    from mail_providers import clear_config_cache

    payload = request.get_json(silent=True) or {}
    provider = str(payload.get('provider', 'smtp')).lower().strip()
    if provider not in ('smtp', 'resend', 'sendgrid', 'postmark'):
        return jsonify({"error": "INVALID_PROVIDER"}), 400

    # Učitaj postojeći config da sačuvamo API ključ ako korisnik ne menja
    existing = {}
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='otpMailProvider'").fetchone()
            if row and row[0]:
                try: existing = decrypt_data(row[0]) or {}
                except Exception: existing = {}
    except Exception:
        pass

    new_cfg = {
        'provider': provider,
        'from_email': str(payload.get('from_email', existing.get('from_email', ''))).strip()[:120],
        'from_name': str(payload.get('from_name', existing.get('from_name', 'Aspidus'))).strip()[:80],
        'magic_link_enabled': bool(payload.get('magic_link_enabled', False)),
        'magic_link_ttl_min': max(5, min(int(payload.get('magic_link_ttl_min', 15) or 15), 60)),
    }

    # API key: menjaj samo ako je poslat, u suprotnom nasledi postojeći
    api_key_in = str(payload.get('api_key', '')).strip()
    if provider == 'smtp':
        new_cfg['api_key'] = ''  # SMTP nema api_key
    elif api_key_in:
        new_cfg['api_key'] = api_key_in
    else:
        new_cfg['api_key'] = existing.get('api_key', '')

    # Validacija api_key formata po provideru
    if provider == 'resend' and new_cfg['api_key'] and not new_cfg['api_key'].startswith('re_'):
        return jsonify({"error": "RESEND_KEY_INVALID",
                        "message": "Resend API key must start with 're_'"}), 400
    if provider == 'sendgrid' and new_cfg['api_key'] and not new_cfg['api_key'].startswith('SG.'):
        return jsonify({"error": "SENDGRID_KEY_INVALID",
                        "message": "SendGrid API key must start with 'SG.'"}), 400

    try:
        with sqlite3.connect(DB_FILE, timeout=30) as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('otpMailProvider', ?)",
                         (encrypt_data(new_cfg),))
            conn.commit()
    except Exception as e:
        return jsonify({"error": "SAVE_FAILED", "message": str(e)}), 500

    clear_config_cache()
    log_audit('SECURITY', 'system',
              f'OTP delivery provider changed to {provider} by {session.get("username","?")}',
              is_suspicious=False)
    return jsonify({"status": "success", "provider": provider})


@system_bp.route('/otp_delivery/test', methods=['POST'])
@login_required
def test_otp_delivery():
    """Šalje test mejl na admin-adresu preko trenutno konfigurisanog providera —
    admin može odmah da vidi da li konfiguracija radi bez čekanja klijenta."""
    if not _is_admin():
        return jsonify({"error": "UNAUTHORIZED"}), 403
    payload = request.get_json(silent=True) or {}
    to_email = str(payload.get('to', '')).strip().lower()
    if not to_email or '@' not in to_email:
        return jsonify({"error": "INVALID_EMAIL"}), 400
    from mail_providers import send_transactional
    ok, info = send_transactional(
        to_email,
        '[Aspidus] OTP delivery test',
        '<html><body><h2>✓ OTP delivery works</h2><p>This test was sent via the currently configured provider. If you received it in the inbox (not spam), the setup is correct.</p></body></html>',
        'OTP delivery works — this test was sent via the currently configured provider.',
    )
    log_audit('INFO', 'system',
              f'OTP delivery test to {to_email}: {"OK" if ok else "FAIL"} ({str(info)[:200]})',
              is_suspicious=False)
    return jsonify({"ok": bool(ok), "info": str(info)[:300]})
