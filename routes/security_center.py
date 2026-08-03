"""
Round F — SECURITY CENTER
==========================
Self-service security page za CRM korisnike i admin-only globalne kontrole.

Endpointi:
  /profile/security                       — HTML self-service (sessions, devices, history, password)
  /api/security/sessions                  — moje aktivne sesije
  /api/security/sessions/<sid>/revoke     — terminiraj jednu sesiju
  /api/security/login-history             — poslednjih 50 login-a
  /api/security/known-ips                 — moje pouzdane IP adrese
  /api/security/known-ips/<id>/forget     — obriši IP (sledeci login iz nje pravi novu notifikaciju)
  /api/security/trusted-devices           — spisak uredjaja
  /api/security/trusted-devices/<id>/revoke  — ukloni uredjaj
  /api/security/password/policy           — pravila (min duzina, age policy)
  /api/security/magic-link                — request magic-link login (email-based)
  /api/security/lockout/status            — proveri da li si zakljucan
  /api/admin/security/policy              — GET/POST global policy (2FA enforce, max attempts, lockout min)
  /api/admin/security/force-password-reset/<uid>   — postavi must_change_password=1
  /api/admin/security/unlock/<uid>        — otkljucaj zakljucanog user-a
  /api/admin/security/break-glass/<uid>   — generisi jednokratni recovery link za admin-a
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone, timedelta

from flask import Blueprint, jsonify, request, session, render_template

from utils import login_required, log_audit, get_client_ip

security_bp = Blueprint('security_bp', __name__)


# =========================================================================
#  HELPERS
# =========================================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _get_policy() -> dict:
    """V24.1 SUPABASE-ONLY: cita security policy iz Supabase settings tabele."""
    defaults = {
        'enforce_2fa_for_admins': False,
        'max_login_attempts': 10,
        'lockout_minutes': 15,
        'password_min_length': 12,
        'password_max_age_days': 0,
        'password_history_count': 5,
        'notify_new_ip': True,
        'trusted_device_ttl_days': 30,
        'magic_link_ttl_minutes': 15,
    }
    try:
        import supabase_store as _store
        v = _store.get_setting('security_policy')
        if v:
            stored = json.loads(v) if isinstance(v, str) else v
            if isinstance(stored, dict):
                defaults.update(stored)
    except Exception:
        pass
    return defaults


def _set_policy(patch: dict) -> dict:
    current = _get_policy()
    current.update({k: v for k, v in patch.items() if k in current})
    try:
        import supabase_store as _store
        _store.set_setting('security_policy', json.dumps(current))
    except Exception:
        pass
    return current


# =========================================================================
#  PAGE
# =========================================================================

@security_bp.route('/profile/security', methods=['GET'])
@login_required
def security_page():
    return render_template('security_center.html')


# =========================================================================
#  SESSIONS
# =========================================================================

@security_bp.route('/api/security/sessions', methods=['GET'])
@login_required
def my_sessions():
    """V24.1 SUPABASE-ONLY."""
    uid = session.get('user_id')
    current_sid = session.get('session_id')
    from data_layer import select as _dl_select
    rows = _dl_select('user_sessions', filters={'user_id': uid},
                      order='-last_seen_at', limit=50) or []
    return jsonify({
        'sessions': [{
            'id': r.get('id'), 'created_at': r.get('created_at'),
            'last_seen_at': r.get('last_seen_at'),
            'ip': r.get('ip'), 'country': r.get('country'),
            'ua': r.get('ua_family'), 'device': r.get('device_label'),
            'revoked': bool(r.get('revoked')),
            'is_current': r.get('id') == current_sid,
        } for r in rows]
    })


@security_bp.route('/api/security/sessions/<sid>/revoke', methods=['POST'])
@login_required
def revoke_session(sid):
    """V24.1 SUPABASE-ONLY."""
    uid = session.get('user_id')
    now = _now_iso()
    from data_layer import update as _dl_update
    updated = _dl_update('user_sessions',
                         {'id': sid, 'user_id': uid, 'revoked': False},
                         {'revoked': True, 'revoked_at': now, 'revoked_reason': 'user_revoked'})
    if not updated:
        return jsonify({'error': 'not_found_or_already_revoked'}), 404
    log_audit('SECURITY', 'session', f'User revoked session {sid[:8]}…')
    return jsonify({'revoked': True})


# =========================================================================
#  LOGIN HISTORY (koristi audit_logs)
# =========================================================================

@security_bp.route('/api/security/login-history', methods=['GET'])
@login_required
def login_history():
    """V24.1 SUPABASE-ONLY: cita audit_logs iz Supabase."""
    uid = session.get('user_id')
    from data_layer import select as _dl_select
    rows = _dl_select('audit_logs',
                      filters={'user_id': uid,
                               'action': ('in', ['LOGIN', 'SECURITY', 'LOGOUT'])},
                      order='-timestamp', limit=100) or []
    return jsonify({
        'events': [{
            'timestamp': r.get('timestamp'),
            'action': r.get('action'),
            'ip': r.get('ip_address'),
            'user_agent': r.get('user_agent'),
            'details': r.get('details'),
            'location': r.get('location'),
        } for r in rows]
    })


# =========================================================================
#  KNOWN IPs
# =========================================================================

@security_bp.route('/api/security/known-ips', methods=['GET'])
@login_required
def known_ips():
    """V24.1 SUPABASE-ONLY."""
    uid = session.get('user_id')
    from data_layer import select as _dl_select
    rows = _dl_select('known_ips', filters={'user_id': uid},
                      order='-last_seen') or []
    return jsonify({
        'ips': [{
            'id': r.get('id'), 'ip': r.get('ip'),
            'country': r.get('country'), 'city': r.get('city'),
            'first_seen': r.get('first_seen'), 'last_seen': r.get('last_seen'),
            'login_count': r.get('login_count'),
        } for r in rows]
    })


@security_bp.route('/api/security/known-ips/<kid>/forget', methods=['POST'])
@login_required
def forget_ip(kid):
    """V24.1 SUPABASE-ONLY."""
    uid = session.get('user_id')
    from data_layer import delete as _dl_delete
    n = _dl_delete('known_ips', {'id': kid, 'user_id': uid})
    log_audit('SECURITY', 'known_ips', f'User forgot known IP {kid}')
    return jsonify({'deleted': int(n or 0)})


# =========================================================================
#  TRUSTED DEVICES
# =========================================================================

@security_bp.route('/api/security/trusted-devices', methods=['GET'])
@login_required
def trusted_devices():
    """V24.1 SUPABASE-ONLY."""
    uid = session.get('user_id')
    from data_layer import select as _dl_select
    rows = _dl_select('trusted_devices', filters={'user_id': uid},
                      order='-last_seen_at') or []
    return jsonify({
        'devices': [{
            'id': r.get('id'), 'label': r.get('label'),
            'created_at': r.get('created_at'), 'expires_at': r.get('expires_at'),
            'last_seen_at': r.get('last_seen_at'), 'last_ip': r.get('last_ip'),
            'revoked': bool(r.get('revoked')),
        } for r in rows]
    })


@security_bp.route('/api/security/trusted-devices/<did>/revoke', methods=['POST'])
@login_required
def revoke_device(did):
    """V24.1 SUPABASE-ONLY."""
    uid = session.get('user_id')
    from data_layer import update as _dl_update
    updated = _dl_update('trusted_devices', {'id': did, 'user_id': uid}, {'revoked': True})
    log_audit('SECURITY', 'trusted_device', f'User revoked device {did}')
    return jsonify({'revoked': len(updated) if isinstance(updated, list) else int(bool(updated))})


# =========================================================================
#  POLICY (public — svako moze da vidi trenutna pravila)
# =========================================================================

@security_bp.route('/api/security/password/policy', methods=['GET'])
def password_policy_public():
    p = _get_policy()
    # Ne izlaze admin-only polja
    return jsonify({
        'password_min_length': p['password_min_length'],
        'password_max_age_days': p['password_max_age_days'],
        'password_history_count': p['password_history_count'],
        'magic_link_ttl_minutes': p['magic_link_ttl_minutes'],
    })


# =========================================================================
#  MAGIC LINK LOGIN (passwordless)
# =========================================================================

@security_bp.route('/api/security/magic-link', methods=['POST'])
def request_magic_link():
    """Passwordless login: user daje username, dobije email sa jednokratnim linkom.
    Ne otkriva da li username postoji (constant-time response), ali stvarno salje mejl
    samo ako user postoji i ima email na profilu."""
    body = request.get_json(silent=True) or {}
    username = str(body.get('username') or '').strip().lower()
    if not username or len(username) > 100:
        return jsonify({'status': 'ok'})  # constant response

    ip = get_client_ip()
    import supabase_store as _store
    u = _store.get_user_by_username(username)
    if not u or not u.get('email'):
        # Constant response da napadac ne otkrije koji usernames imaju email
        return jsonify({'status': 'ok'})
    uid = u['id']; email = u['email']; real_username = u['username']

    tok = secrets.token_urlsafe(48)
    policy = _get_policy()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=policy['magic_link_ttl_minutes'])).isoformat().replace('+00:00', 'Z')

    from data_layer import insert as _dl_insert
    try:
        _dl_insert('magic_login_tokens', {
            'token': _hash_token(tok), 'user_id': uid, 'purpose': 'login',
            'created_at': _now_iso(), 'expires_at': expires, 'request_ip': ip,
        })
    except Exception:
        pass

    # Sastavi link i posalji preko utils_email
    from urllib.parse import urljoin
    base = request.host_url.rstrip('/')
    link = f"{base}/login/magic?t={tok}"
    try:
        from utils_email import send_email_now
        subject = 'Aspidus — Sign-in link'
        body_html = (
            f"<p>Hi {real_username},</p>"
            f"<p>Use this one-time link to sign in to Aspidus CRM. "
            f"It expires in {policy['magic_link_ttl_minutes']} minutes and can be used once.</p>"
            f"<p><a href=\"{link}\">Sign in now</a></p>"
            f"<p>Requested from IP {ip or 'unknown'}. If this wasn't you, ignore this message and change your password.</p>"
        )
        send_email_now(email, subject, body_html, body_type='html')
    except Exception as _e:
        pass  # ne otkrivamo email failure napadacu

    log_audit('SECURITY', 'magic_link', f'Magic-link requested for user {real_username}')
    return jsonify({'status': 'ok'})


@security_bp.route('/login/magic', methods=['GET'])
def consume_magic_link():
    """Ovaj endpoint klijent posećuje kroz link iz emaila. Aktivira sesiju
    i redirektuje na /. Ne trazi ni lozinku ni 2FA (magic link je vec dokaz)."""
    tok = request.args.get('t', '')
    if not tok:
        return "Missing token.", 400
    tok_hash = _hash_token(tok)
    now_iso = _now_iso()
    from data_layer import select_one as _dl_select_one, update as _dl_update
    row = _dl_select_one('magic_login_tokens', {'token': tok_hash})
    if not row:
        return "Invalid or already-used link.", 400
    uid = row.get('user_id'); expires_at = row.get('expires_at')
    used_at = row.get('used_at'); purpose = row.get('purpose')
    if used_at:
        return "This link has already been used.", 400
    if str(expires_at) < now_iso:
        return "This link has expired. Request a new one from the sign-in page.", 400
    import supabase_store as _store
    u = _store.get_user_by_id(uid)
    if not u:
        return "User not found.", 400
    _dl_update('magic_login_tokens', {'token': tok_hash}, {'used_at': now_iso})

    from utils import get_user_token_version
    session.permanent = True
    session['user_id'] = u.get('id')
    session['username'] = u.get('username')
    session['role'] = u.get('role')
    session['login_time'] = datetime.now(timezone.utc).timestamp()
    session['login_ip'] = get_client_ip()
    session['login_ua'] = request.user_agent.string if request.user_agent else 'Unknown'
    session['login_ua_family'] = f"{request.user_agent.browser or ''}|{request.user_agent.platform or ''}"
    session['token_version'] = get_user_token_version(u.get('id'))
    session['session_id'] = _create_session_row(u.get('id'))
    log_audit('LOGIN', 'system', f'Magic-link successful login for {u.get("username")} (purpose={purpose})')
    from flask import redirect
    if purpose == 'break_glass':
        return redirect('/profile/security#password')
    return redirect('/')


# =========================================================================
#  LOCKOUT STATUS
# =========================================================================

@security_bp.route('/api/security/lockout/status', methods=['POST'])
def lockout_status():
    """Anonymous — client daje username, mi vratimo koliko sekundi je zakljucan.
    Ne otkriva da li username postoji (uvek vraca isti shape)."""
    body = request.get_json(silent=True) or {}
    username = str(body.get('username') or '').strip().lower()[:100]
    if not username:
        return jsonify({'locked': False, 'remaining_seconds': 0})
    import supabase_store as _store
    u = _store.get_user_by_username(username)
    if not u or not u.get('locked_until'):
        return jsonify({'locked': False, 'remaining_seconds': 0})
    try:
        until = datetime.fromisoformat(str(u['locked_until']).replace('Z', '+00:00'))
        remaining = int((until - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return jsonify({'locked': False, 'remaining_seconds': 0})
    if remaining <= 0:
        return jsonify({'locked': False, 'remaining_seconds': 0})
    return jsonify({'locked': True, 'remaining_seconds': remaining})


# =========================================================================
#  ADMIN POLICY
# =========================================================================

@security_bp.route('/api/admin/security/policy', methods=['GET'])
@login_required
def admin_policy_get():
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    return jsonify({'policy': _get_policy()})


@security_bp.route('/api/admin/security/policy', methods=['POST'])
@login_required
def admin_policy_set():
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    body = request.get_json(silent=True) or {}
    # Validacija
    valid_types = {
        'enforce_2fa_for_admins': bool,
        'max_login_attempts': int,
        'lockout_minutes': int,
        'password_min_length': int,
        'password_max_age_days': int,
        'password_history_count': int,
        'notify_new_ip': bool,
        'trusted_device_ttl_days': int,
        'magic_link_ttl_minutes': int,
    }
    patch = {}
    for k, t in valid_types.items():
        if k in body:
            try:
                if t is bool:
                    patch[k] = bool(body[k])
                else:
                    patch[k] = int(body[k])
            except (ValueError, TypeError):
                return jsonify({'error': f'invalid_type_{k}'}), 400
    updated = _set_policy(patch)
    log_audit('EDIT', 'security_policy', f'Admin updated policy: {list(patch.keys())}')
    return jsonify({'policy': updated})


@security_bp.route('/api/admin/security/force-password-reset/<uid>', methods=['POST'])
@login_required
def admin_force_reset(uid):
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    from data_layer import update as _dl_update
    n = _dl_update('users', {'id': uid}, {'must_change_password': True})
    log_audit('SECURITY', 'admin', f'Admin forced password reset for user {uid}')
    return jsonify({'forced': len(n) if isinstance(n, list) else int(bool(n))})


@security_bp.route('/api/admin/security/unlock/<uid>', methods=['POST'])
@login_required
def admin_unlock(uid):
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    from data_layer import update as _dl_update
    n = _dl_update('users', {'id': uid}, {'locked_until': None})
    log_audit('SECURITY', 'admin', f'Admin unlocked user {uid}')
    return jsonify({'unlocked': len(n) if isinstance(n, list) else int(bool(n))})


@security_bp.route('/api/admin/security/break-glass/<uid>', methods=['POST'])
@login_required
def admin_break_glass(uid):
    """Generise jednokratni magic link koji dopusti admin-u pristup nalogu drugog user-a
    (npr. korisnik zaboravio lozinku i 2FA). Link se salje na email tog user-a, admin
    kaze user-u da otvori mejl. Nakon konzumacije, user_a se automatski gura na
    change-password stranicu (purpose='break_glass')."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    import supabase_store as _store
    from data_layer import insert as _dl_insert
    u = _store.get_user_by_id(uid)
    if not u or not u.get('email'):
        return jsonify({'error': 'user_has_no_email'}), 400

    tok = secrets.token_urlsafe(48)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat().replace('+00:00', 'Z')
    try:
        _dl_insert('magic_login_tokens', {
            'token': _hash_token(tok), 'user_id': uid, 'purpose': 'break_glass',
            'created_at': _now_iso(), 'expires_at': expires, 'request_ip': get_client_ip(),
        })
    except Exception:
        pass

    base = request.host_url.rstrip('/')
    link = f"{base}/login/magic?t={tok}"
    try:
        from utils_email import send_email_now
        send_email_now(
            u.get('email'),
            'Aspidus — Emergency account recovery',
            f"<p>Hi {u.get('username')},</p>"
            f"<p>An administrator ({session.get('username')}) issued a one-time recovery link for your account. "
            f"It expires in 30 minutes. After clicking, you'll be signed in and asked to set a new password.</p>"
            f"<p><a href=\"{link}\">Recover my account</a></p>"
            f"<p>If you didn't request this, contact your administrator immediately.</p>",
            body_type='html'
        )
    except Exception as e:
        return jsonify({'error': f'email_failed: {e}'}), 500

    log_audit('SECURITY', 'admin', f'Admin issued break-glass link for user {row[0]}',
              is_suspicious=True)
    return jsonify({'sent_to': row[1]})


# =========================================================================
#  INTERNAL — create session row (called from auth.login on success)
# =========================================================================

def _create_session_row(uid: str) -> str:
    """Kreira row u user_sessions i vraca njegov id. Zovi ga iz login handler-a
    posle uspesnog auth-a; snimi vraceni id u session['session_id']."""
    ip = get_client_ip()
    ua = request.user_agent.string if request.user_agent else 'Unknown'
    ua_family = f"{request.user_agent.browser or ''}|{request.user_agent.platform or ''}"
    device_label = f"{request.user_agent.browser or 'Browser'} on {request.user_agent.platform or 'Device'}"
    sid = str(uuid.uuid4())
    now = _now_iso()

    country = None
    try:
        from utils import get_ip_info
        _, ip_loc, _ = get_ip_info(ip) if ip else ('', '', '')
        if ip_loc and ',' in ip_loc:
            country = ip_loc.split(',')[-1].strip()[:80]
    except Exception:
        pass

    row = {
        'id': sid, 'user_id': uid, 'created_at': now, 'last_seen_at': now,
        'ip': ip, 'country': country, 'user_agent': ua,
        'ua_family': ua_family, 'device_label': device_label,
        'revoked': False,
    }
    # V24.1 SUPABASE-ONLY
    try:
        from data_layer import upsert as _db_upsert
        _db_upsert('user_sessions', row, on_conflict='id')
    except Exception:
        pass
    return sid


def touch_session(sid: str) -> None:
    """V24.1 SUPABASE-ONLY."""
    if not sid:
        return
    try:
        from data_layer import update as _dl_update
        _dl_update('user_sessions', {'id': sid}, {'last_seen_at': _now_iso()})
    except Exception:
        pass


def is_session_revoked(sid: str) -> bool:
    if not sid:
        return False
    try:
        from data_layer import select_one as _dl_select_one
        row = _dl_select_one('user_sessions', {'id': sid})
        return bool(row and row.get('revoked'))
    except Exception:
        return False


# =========================================================================
#  KNOWN-IP TRACKING (called from auth.login on success)
# =========================================================================

def record_login_ip(uid: str, ip: str) -> bool:
    """Insertuje ili apdejtuje known_ips row. Vraca True ako je IP NOV (nikad
    videna) — pozivac tada može da posalje 'new login' email."""
    if not ip:
        return False
    now = _now_iso()
    is_new = False
    country = city = None
    try:
        from utils import get_ip_info
        network_info, ip_loc, _ = get_ip_info(ip)
        if ip_loc and ',' in ip_loc:
            parts = ip_loc.split(',')
            city = parts[0].strip()[:120]
            country = parts[-1].strip()[:80]
    except Exception:
        pass
    # V24.1 SUPABASE-ONLY
    try:
        from data_layer import select as _dl_select, update as _dl_update, insert as _dl_insert
        rows = _dl_select('known_ips', filters={'user_id': uid, 'ip': ip}, limit=1) or []
        if rows:
            existing = rows[0]
            _dl_update('known_ips', {'id': existing.get('id')},
                       {'last_seen': now,
                        'login_count': int(existing.get('login_count', 0) or 0) + 1})
        else:
            is_new = True
            _dl_insert('known_ips', {
                'id': str(uuid.uuid4()), 'user_id': uid, 'ip': ip,
                'country': country, 'city': city,
                'first_seen': now, 'last_seen': now, 'login_count': 1,
            })
    except Exception:
        pass
    return is_new


def send_new_ip_alert(uid: str, ip: str) -> None:
    """Salje email ako je user pretplaticen i policy notify_new_ip = True."""
    policy = _get_policy()
    if not policy.get('notify_new_ip'):
        return
    try:
        import supabase_store as _store
        _u = _store.get_user_by_id(uid)
        if not _u or not _u.get('email'):
            return
        username = _u.get('username'); email = _u.get('email')
        ua = 'Unknown'
        try:
            ua = request.user_agent.string
        except Exception:
            pass
        loc = ip
        try:
            from utils import get_ip_info
            _, ip_loc, _ = get_ip_info(ip)
            if ip_loc and ip_loc != 'N/A':
                loc = f"{ip_loc} ({ip})"
        except Exception:
            pass
        from utils_email import send_email_now
        send_email_now(
            email,
            'Aspidus — New sign-in from an unfamiliar location',
            f"<p>Hi {username},</p>"
            f"<p>A new sign-in to your Aspidus CRM account was detected:</p>"
            f"<ul>"
            f"<li><b>Location:</b> {loc}</li>"
            f"<li><b>Device:</b> {ua}</li>"
            f"<li><b>Time (UTC):</b> {_now_iso()}</li>"
            f"</ul>"
            f"<p>If this was you, no action is needed. If not, "
            f"open your Security Center and revoke the session, then change your password.</p>",
            body_type='html'
        )
    except Exception:
        pass


# =========================================================================
#  PASSWORD HISTORY / POLICY ENFORCEMENT
# =========================================================================

def check_password_reuse(uid: str, plain_password: str) -> bool:
    """V24.1 SUPABASE-ONLY."""
    from werkzeug.security import check_password_hash
    policy = _get_policy()
    n = int(policy.get('password_history_count', 5))
    if n <= 0:
        return False
    try:
        from data_layer import select as _dl_select
        rows = _dl_select('password_history', filters={'user_id': uid},
                          order='-changed_at', limit=n) or []
    except Exception:
        return False
    for r in rows:
        try:
            if check_password_hash(r.get('password_hash', ''), plain_password):
                return True
        except Exception:
            pass
    return False


def add_password_history(uid: str, password_hash: str) -> None:
    """V24.1 SUPABASE-ONLY."""
    now = _now_iso()
    policy = _get_policy()
    keep = int(policy.get('password_history_count', 5))
    try:
        from data_layer import insert as _dl_insert, select as _dl_select, delete as _dl_delete, update as _dl_update
        _dl_insert('password_history', {
            'id': str(uuid.uuid4()), 'user_id': uid,
            'password_hash': password_hash, 'changed_at': now,
        })
        # Prune old
        if keep > 0:
            all_rows = _dl_select('password_history', filters={'user_id': uid},
                                  order='-changed_at') or []
            for r in all_rows[keep:]:
                try: _dl_delete('password_history', {'id': r.get('id')})
                except Exception: pass
        # password_expires_at ako je policy > 0
        max_age = int(policy.get('password_max_age_days', 0))
        if max_age > 0:
            expires = (datetime.now(timezone.utc) + timedelta(days=max_age)).isoformat().replace('+00:00', 'Z')
            _dl_update('users', {'id': uid},
                       {'password_expires_at': expires, 'must_change_password': False})
    except Exception:
        pass
    return
