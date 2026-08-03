"""System/health endpointi za admin monitoring.

V25 SUPABASE-ONLY — svi DB pozivi idu kroz `data_layer` facade ili
`supabase_store` helper. Nema vise `sqlite3.connect(...)` poziva; sve
postojke (otp delivery, chat webhooks, hcaptcha, api keys) zive u Supabase
`settings` tabeli (encrypted preko utils.encrypt_data/decrypt_data).

Cilj: dati adminu instant pregled zdravlja instance (Supabase konekcija,
disk usage uploads foldera, firewall cache) bez SSH-a na server. Nista
sto otkriva se ne cita bez admin role.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta

from flask import Blueprint, jsonify, session, request

from config import DATA_DIR, UPLOAD_FOLDER, PORTAL_UPLOAD_FOLDER
from utils import login_required, log_audit, FirewallCache, encrypt_data, decrypt_data
import supabase_store as store
import data_layer as _dl

logger = logging.getLogger(__name__)

system_bp = Blueprint('system', __name__, url_prefix='/api/system')

# Supabase Studio backup URL ( projekat gceaznutofvqbuyypjlh / eu-west-1 ).
# Backup-i sada radi Supabase automatski (PITR + daily snapshot).
SUPABASE_BACKUP_URL = 'https://app.supabase.com/project/gceaznutofvqbuyypjlh/database/backups'


def _is_admin():
    return session.get('role') == 'admin'


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


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


def _read_encrypted_setting(key, default=None):
    """Vraca decrypted dict/string iz settings tabele, ili default."""
    try:
        v = store.get_setting(key)
        if not v:
            return default
        d = decrypt_data(v)
        return d if d is not None else default
    except Exception as _e:
        logger.info(f'_read_encrypted_setting({key}) failed: {_e}')
        return default


def _write_encrypted_setting(key, value):
    """Enkriptuje i upisuje vrednost u settings tabelu."""
    encrypted = encrypt_data(value)
    store.set_setting(key, encrypted)


@system_bp.route('/health', methods=['GET'])
@login_required
def health():
    """Admin-only. V25 SUPABASE-ONLY: prikazuje status Supabase konekcije,
    broj zapisa u kljucnim tabelama, lokalni disk usage uploads foldera, i
    firewall cache snapshot. Nema vise SQLite DB stats / pragmas."""
    if not _is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403

    # ---- 1) Supabase backend health probe ---------------------------------
    try:
        supabase_health = _dl.health()
    except Exception as _e:
        supabase_health = {'backend': '?', 'ok': False, 'error': str(_e)}

    # ---- 2) Row counts in key tables (best-effort) ------------------------
    counts = {}
    for table in ('partners', 'users', 'offers', 'invoices', 'proformas',
                  'document_register', 'document_revisions', 'email_queue',
                  'audit_logs', 'kyc_submissions', 'portal_products'):
        try:
            counts[table] = _dl.count(table)
        except Exception as _e:
            logger.info(f'health count({table}) failed: {_e}')
            counts[table] = None

    # Recent audit log entries (last 24h) — best-effort
    recent_audit_24h = None
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat().replace('+00:00', 'Z')
        recent_audit_24h = _dl.count('audit_logs', filters={'timestamp': ('gte', cutoff)})
    except Exception as _e:
        logger.info(f'health recent_audit_24h failed: {_e}')

    # ---- 3) Disk usage (uploads folders only — filesystem, ne SQLite) ------
    backups_dir = os.path.join(DATA_DIR, 'backups')
    try:
        vfs = os.statvfs(DATA_DIR)
        disk = {
            'total_gb': round(vfs.f_frsize * vfs.f_blocks / 1e9, 2),
            'available_gb': round(vfs.f_frsize * vfs.f_bavail / 1e9, 2),
            'used_percent': round((1 - vfs.f_bavail / vfs.f_blocks) * 100, 1) if vfs.f_blocks else None,
        }
    except Exception:
        disk = {'error': 'statvfs unavailable'}

    storage = {
        'data_dir': DATA_DIR,
        'uploads_size_mb': round(_dir_size(UPLOAD_FOLDER) / 1024 / 1024, 2),
        'portal_uploads_size_mb': round(_dir_size(PORTAL_UPLOAD_FOLDER) / 1024 / 1024, 2),
        'backups_total_mb': round(_dir_size(backups_dir) / 1024 / 1024, 2) if os.path.isdir(backups_dir) else 0,
        'disk': disk,
    }

    # ---- 4) Firewall cache snapshot (in-memory, best-effort) -------------
    firewall_snapshot = {
        'blacklist_size': len(FirewallCache.blacklist),
        'whitelist_size': len(FirewallCache.whitelist),
        'active_ips_tracking_logins': len(FirewallCache.login_attempts),
        'settings': dict(FirewallCache.settings),
    }

    payload = {
        'timestamp': _now_iso(),
        'supabase': supabase_health,
        'counts': counts,
        'recent_audit_24h': recent_audit_24h,
        'storage': storage,
        'firewall': firewall_snapshot,
        'note': ('Backups managed by Supabase Studio (PITR + daily). '
                 f'Visit {SUPABASE_BACKUP_URL} for restore operations.'),
    }
    return jsonify(payload)


# ==========================================================
#  BACKUP — Supabase-managed (PITR + daily snapshot)
# ==========================================================
# Supabase ima sopstveni backup sistem. SQLite `.fernet` snapshoti i
# kompletni `tar.gz` arhivi su zastareli — sve je sada u Supabase Studio-u
# → Database → Backups. Ovi endpoint-i ostaju radi backward-compat sa
# frontendom (Operations Center UI), ali vracaju info poruku.

@system_bp.route('/backup/now', methods=['POST'])
@login_required
def backup_now():
    """V25: backup radi Supabase automatski. Vraca info poruku."""
    if not _is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403
    log_audit('INFO', 'system',
              f'Admin requested manual backup (now managed by Supabase) by {session.get("username","?")}',
              is_suspicious=False)
    return jsonify({
        "status": "managed_by_supabase",
        "message": "Backups are now managed by Supabase. Use Supabase Studio → Database → Backups for restore.",
        "url": SUPABASE_BACKUP_URL,
    })


@system_bp.route('/backup/full', methods=['GET'])
@login_required
def backup_full_download():
    """V25: backup radi Supabase. Endpoint vise ne stream-uje .tar.gz —
    vraca info poruku sa linkom na Supabase Studio."""
    if not _is_admin():
        return jsonify({"error": "ACCESS_DENIED"}), 403
    log_audit('INFO', 'system',
              f'Admin requested full backup download (now managed by Supabase) by {session.get("username","?")}',
              is_suspicious=False)
    return jsonify({
        "status": "managed_by_supabase",
        "message": "Full backup download is deprecated — Supabase manages backups automatically.",
        "url": SUPABASE_BACKUP_URL,
    }), 200


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
    from mail_providers import clear_config_cache

    payload = request.get_json(silent=True) or {}
    provider = str(payload.get('provider', 'smtp')).lower().strip()
    if provider not in ('smtp', 'resend', 'sendgrid', 'postmark'):
        return jsonify({"error": "INVALID_PROVIDER"}), 400

    # Učitaj postojeći config da sačuvamo API ključ ako korisnik ne menja
    existing = _read_encrypted_setting('otpMailProvider', {}) or {}
    if not isinstance(existing, dict):
        existing = {}

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
        _write_encrypted_setting('otpMailProvider', new_cfg)
    except Exception as e:
        return jsonify({"error": "SAVE_FAILED", "message": str(e)}), 500

    clear_config_cache()
    log_audit('SECURITY', 'system',
              f'OTP delivery provider changed to {provider} by {session.get("username","?")}',
              is_suspicious=False)
    return jsonify({"status": "success", "provider": provider})


@system_bp.route('/chat_webhooks', methods=['GET'])
@login_required
def get_chat_webhooks():
    """Redigovan pregled — API tokeni se ne otkrivaju."""
    if not _is_admin():
        return jsonify({"error": "UNAUTHORIZED"}), 403
    cfg = _read_encrypted_setting('chatWebhooks', {}) or {}
    if not isinstance(cfg, dict):
        cfg = {}

    def _mask(s):
        s = str(s or '')
        return (s[:8] + '…' + s[-4:]) if len(s) > 15 else ('•' * len(s))
    return jsonify({
        'slack': cfg.get('slack', ''),
        'teams': cfg.get('teams', ''),
        'telegram_bot_token': _mask(cfg.get('telegram_bot_token', '')) if cfg.get('telegram_bot_token') else '',
        'telegram_chat_id': cfg.get('telegram_chat_id', ''),
        'ntfy_url': cfg.get('ntfy_url', ''),
        'whatsapp_phone_id': cfg.get('whatsapp_phone_id', ''),
        'whatsapp_token': _mask(cfg.get('whatsapp_token', '')) if cfg.get('whatsapp_token') else '',
        'whatsapp_to': cfg.get('whatsapp_to', ''),
        'events': cfg.get('events', ['offer_accepted', 'offer_declined', 'kyc_submitted',
                                     'sanctions_flag', 'deal_created', 'document_signed']),
        'has_slack': bool(cfg.get('slack')),
        'has_teams': bool(cfg.get('teams')),
        'has_telegram': bool(cfg.get('telegram_bot_token') and cfg.get('telegram_chat_id')),
        'has_ntfy': bool(cfg.get('ntfy_url')),
        'has_whatsapp': bool(cfg.get('whatsapp_token') and cfg.get('whatsapp_phone_id')),
    })


@system_bp.route('/chat_webhooks', methods=['POST'])
@login_required
def set_chat_webhooks():
    """Snima chat notifikacijsku konfiguraciju. Sve tajne se enkriptuju."""
    if not _is_admin():
        return jsonify({"error": "UNAUTHORIZED"}), 403
    from webhooks import clear_cache

    payload = request.get_json(silent=True) or {}
    existing = _read_encrypted_setting('chatWebhooks', {}) or {}
    if not isinstance(existing, dict):
        existing = {}

    # Za tajne (bot_token, whatsapp_token): sačuvaj postojeći ako je nova
    # vrednost prazna ili maskirana (sadrži '…' ili samo '•')
    def _preserve(new, old):
        s = str(new or '').strip()
        if not s or '…' in s or set(s) <= {'•'}: return str(old or '')
        return s

    new_cfg = {
        'slack': str(payload.get('slack', '')).strip(),
        'teams': str(payload.get('teams', '')).strip(),
        'telegram_bot_token': _preserve(payload.get('telegram_bot_token'), existing.get('telegram_bot_token')),
        'telegram_chat_id':   str(payload.get('telegram_chat_id', '')).strip(),
        'ntfy_url':            str(payload.get('ntfy_url', '')).strip(),
        'whatsapp_phone_id':  str(payload.get('whatsapp_phone_id', '')).strip(),
        'whatsapp_token':     _preserve(payload.get('whatsapp_token'), existing.get('whatsapp_token')),
        'whatsapp_to':         str(payload.get('whatsapp_to', '')).strip(),
        'events':              list(payload.get('events') or []),
    }
    try:
        _write_encrypted_setting('chatWebhooks', new_cfg)
    except Exception as e:
        return jsonify({"error": "SAVE_FAILED", "message": str(e)}), 500

    clear_cache()
    log_audit('SECURITY', 'system',
              f'Chat webhooks reconfigured by {session.get("username","?")}',
              is_suspicious=False)
    return jsonify({"status": "success"})


@system_bp.route('/chat_webhooks/test', methods=['POST'])
@login_required
def test_chat_webhooks():
    """Šalje test poruku na sve konfigurisane kanale."""
    if not _is_admin():
        return jsonify({"error": "UNAUTHORIZED"}), 403
    from webhooks import notify
    notify('offer_accepted', {  # koristimo poznat event tip za test
        'Test': 'This is a test notification from Aspidus CRM',
        'Triggered by': session.get('username', '?'),
        'When': _now_iso()[:19],
    })
    return jsonify({"status": "success", "message": "Test dispatched to all configured channels."})


@system_bp.route('/hcaptcha', methods=['GET'])
@login_required
def get_hcaptcha_config():
    """Redigovan pregled hCaptcha config-a."""
    if not _is_admin():
        return jsonify({"error": "UNAUTHORIZED"}), 403
    cfg = _read_encrypted_setting('hcaptchaConfig', {}) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    secret = str(cfg.get('secret', ''))
    return jsonify({
        'sitekey': cfg.get('sitekey', ''),  # sitekey je public, otkriva se
        'has_secret': bool(secret),
        'secret_masked': (secret[:6] + '…' + secret[-4:]) if len(secret) > 12 else '',
    })


@system_bp.route('/hcaptcha', methods=['POST'])
@login_required
def set_hcaptcha_config():
    if not _is_admin():
        return jsonify({"error": "UNAUTHORIZED"}), 403
    from security_ext import clear_hcaptcha_cache

    payload = request.get_json(silent=True) or {}
    existing = _read_encrypted_setting('hcaptchaConfig', {}) or {}
    if not isinstance(existing, dict):
        existing = {}

    new_secret = str(payload.get('secret', '')).strip()
    if not new_secret or '…' in new_secret:
        new_secret = str(existing.get('secret', ''))

    cfg = {
        'sitekey': str(payload.get('sitekey', '')).strip(),
        'secret': new_secret,
    }
    try:
        _write_encrypted_setting('hcaptchaConfig', cfg)
    except Exception as e:
        return jsonify({"error": "SAVE_FAILED", "message": str(e)}), 500

    clear_hcaptcha_cache()
    log_audit('SECURITY', 'system', f'hCaptcha reconfigured by {session.get("username","?")}',
              is_suspicious=False)
    return jsonify({"status": "success", "enabled": bool(cfg['sitekey']) and bool(cfg['secret'])})


# ==========================================================
#  API KEYS — tracking (17TRACK, MarineTraffic, FlightAware,
#  Companies House) + market data (Alpha Vantage)
# ==========================================================

_API_KEYS = {
    'track17ApiKey':      {'label': '17TRACK API key',       'group': 'tracking'},
    'marineTrafficKey':   {'label': 'MarineTraffic PS7 key', 'group': 'tracking'},
    'flightAwareKey':     {'label': 'FlightAware AeroAPI',   'group': 'tracking'},
    'companiesHouseKey':  {'label': 'Companies House UK',    'group': 'tracking'},
    'alphaVantageKey':    {'label': 'Alpha Vantage key',     'group': 'market'},
}


def _mask_key(s):
    s = str(s or '')
    if not s: return ''
    if len(s) <= 8: return '•' * len(s)
    return s[:4] + '…' + s[-4:]


@system_bp.route('/api_keys', methods=['GET'])
@login_required
def get_api_keys():
    """Redigovan pregled svih integracijskih ključeva — vraća samo maske."""
    if not _is_admin():
        return jsonify({"error": "UNAUTHORIZED"}), 403
    out = {}
    try:
        for key, meta in _API_KEYS.items():
            val = ''
            raw = store.get_setting(key)
            if raw:
                try:
                    decrypted = decrypt_data(raw)
                    val = str(decrypted) if decrypted is not None else ''
                except Exception:
                    val = ''
            out[key] = {
                'label': meta['label'],
                'group': meta['group'],
                'has_value': bool(val),
                'masked': _mask_key(val),
            }
    except Exception as e:
        return jsonify({"error": "READ_FAILED", "message": str(e)}), 500
    return jsonify(out)


@system_bp.route('/api_keys', methods=['POST'])
@login_required
def set_api_keys():
    """Snima jedan ili više ključeva. Prazna string / maska (sadrži '…' ili samo '•')
    znači 'ne menjaj postojeći'. Sve se enkriptuje pre snimanja."""
    if not _is_admin():
        return jsonify({"error": "UNAUTHORIZED"}), 403
    payload = request.get_json(silent=True) or {}
    updated = []
    try:
        for key in _API_KEYS.keys():
            if key not in payload:
                continue
            incoming = str(payload.get(key, '') or '').strip()
            if not incoming or '…' in incoming or set(incoming) <= {'•'}:
                continue
            _write_encrypted_setting(key, incoming)
            updated.append(key)
    except Exception as e:
        return jsonify({"error": "SAVE_FAILED", "message": str(e)}), 500

    if updated:
        log_audit('SECURITY', 'system',
                  f'API keys updated ({",".join(updated)}) by {session.get("username","?")}',
                  is_suspicious=False)
    return jsonify({"status": "success", "updated": updated})


# ==========================================================
#  FTS5 UNIFIED SEARCH — globalna pretraga za Cmd+K
# ==========================================================

@system_bp.route('/search', methods=['GET'])
@login_required
def unified_search():
    """FTS5 pretraga preko svih entiteta. Query params:
       - q: search string (obavezno)
       - limit: max broj rezultata (default 20, max 100)
       - types: comma-separated list ('partner,product,deal,offer,document')
    """
    q = (request.args.get('q') or '').strip()
    if not q:
        return jsonify({"results": [], "query": ""})
    try:
        limit = min(int(request.args.get('limit', 20)), 100)
    except Exception:
        limit = 20
    types = None
    if request.args.get('types'):
        types = [t.strip() for t in request.args['types'].split(',') if t.strip()]

    from search_index import search
    results = search(q, limit=limit, entity_types=types)
    return jsonify({"query": q, "count": len(results), "results": results})


@system_bp.route('/search/rebuild', methods=['POST'])
@login_required
def rebuild_search_index():
    """Ručno pokreće rebuild FTS5 indeksa. Admin-only."""
    if not _is_admin():
        return jsonify({"error": "UNAUTHORIZED"}), 403
    from search_index import rebuild_index
    counts = rebuild_index()
    log_audit('INFO', 'system',
              f'Search index rebuilt: {counts} by {session.get("username","?")}',
              is_suspicious=False)
    return jsonify({"status": "success", "indexed": counts})


@system_bp.route('/search/stats', methods=['GET'])
@login_required
def search_stats():
    from search_index import index_stats
    return jsonify(index_stats())


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
