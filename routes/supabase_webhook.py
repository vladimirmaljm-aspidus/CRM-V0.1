"""
Supabase Auth webhook receiver.

Supabase moze da salje HTTP POST na nas endpoint svaki put kad se u
Auth-u desi bilo koji event (user.created, user.updated, user.deleted,
password.changed). Ovim automatski osvezavamo relevantno stanje u
Supabase partners tabeli (npr. partners.email ako se promeni u Supabase-u).

Setup:
  1. U Supabase Dashboard → Database → Webhooks:
     - New webhook → Table: auth.users
     - Events: INSERT, UPDATE, DELETE
     - URL: https://<your-app>/api/webhook/supabase-auth
     - HTTP Headers: X-Webhook-Secret: <isti kao u .env WEBHOOK_SECRET>

  2. U .env postavi:
     WEBHOOK_SECRET=<random string, min 32 chars>

Security:
  * Endpoint proverava X-Webhook-Secret header pre nego što primeni bilo šta.
  * Ako secret nije postavljen (dev), endpoint vraća 503.
  * Loguje SVAKI dolazni webhook u audit_log.

Dodatno — /api/admin/supabase-auth/sync — admin dugme koje
uparuje Supabase Auth users sa partners tabelom (za slucaj
propustenih webhook-ova).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from flask import Blueprint, jsonify, request, session

from utils import decrypt_data, encrypt_data, login_required, log_audit
import supabase_store as store

logger = logging.getLogger(__name__)
supabase_webhook_bp = Blueprint('supabase_webhook_bp', __name__)


def _valid_webhook_secret(req) -> tuple[bool, str]:
    secret = os.environ.get('WEBHOOK_SECRET', '').strip()
    if not secret:
        return False, 'WEBHOOK_SECRET nije postavljen u .env'
    received = req.headers.get('X-Webhook-Secret', '') or req.headers.get('x-webhook-secret', '')
    if not received:
        return False, 'X-Webhook-Secret header nedostaje'
    # Constant-time compare
    if not hmac.compare_digest(secret, received):
        return False, 'X-Webhook-Secret pogresan'
    return True, 'ok'


def _decode_partner_data(raw):
    """Defanzivno dekoduje partner.data: dict (Supabase JSONB) se vraća direktno,
    string (stari Fernet ciphertext) se dekriptuje preko decrypt_data()."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            decoded = decrypt_data(raw)
            if isinstance(decoded, dict):
                return decoded
        except Exception:
            pass
        try:
            return json.loads(raw) if raw.startswith('{') else {}
        except Exception:
            return {}
    return {}


def _find_partner_by_supabase_id_or_email(supabase_id, email):
    """Vraca (partner_id, partner_data) — trazi prvo po supabaseAuthId, pa
    po contact.email/email. None ako nema. V24.0 Supabase read."""
    email_lower = (email or '').strip().lower()
    try:
        rows = store.list_entities('partners') or []
    except Exception as e:
        logger.warning('list_entities(partners) failed: %s', e)
        return None, None
    for p in rows:
        if not isinstance(p, dict):
            continue
        pid = p.get('id')
        if not pid:
            continue
        if supabase_id and p.get('supabaseAuthId') == supabase_id:
            return pid, p
        p_email = ((p.get('contact') or {}).get('email') or p.get('email') or '').strip().lower()
        if email_lower and p_email == email_lower:
            return pid, p
    return None, None


def _save_partner(pid, pdata):
    """Best-effort save preko supabase_store.upsert_entity. Vraća True/False."""
    try:
        pdata_with_id = dict(pdata)
        pdata_with_id['id'] = pid
        store.upsert_entity('partners', pdata_with_id)
        return True
    except Exception as e:
        logger.warning('upsert_entity(partners, %s) failed: %s', pid, e)
        return False


@supabase_webhook_bp.route('/api/webhook/supabase-auth', methods=['POST'])
def supabase_auth_webhook():
    """Prima Auth event iz Supabase-a i osvezava lokalni partner state."""
    ok, why = _valid_webhook_secret(request)
    if not ok:
        # Ne otkrivaj razlog spolja — samo 403 sa generic porukom
        logger.warning(f'Webhook rejected: {why}')
        return jsonify({'error': 'forbidden'}), 403

    payload = request.get_json(silent=True) or {}
    event_type = payload.get('type') or payload.get('event') or 'unknown'
    record = payload.get('record') or {}
    old_record = payload.get('old_record') or {}

    supabase_id = record.get('id') or old_record.get('id')
    email = record.get('email') or old_record.get('email')
    action = 'unknown'
    n_updated = 0

    pid, pdata = _find_partner_by_supabase_id_or_email(supabase_id, email)

    if event_type in ('INSERT', 'user.created') and pid is None:
        # Novi Auth user — samo audit; ne pravimo automatski partner-a
        # (mozda je admin napravio user-a direktno iz Supabase-a, mora
        # eksplicitno da poveze sa CRM partnerom)
        action = 'ignored_no_matching_partner'
    elif event_type in ('UPDATE', 'user.updated') and pid:
        # Sinhronizuj: email (ako promenjen), supabaseAuthId (ako nije bio setovan)
        changed = False
        if email:
            cur_email = ((pdata.get('contact') or {}).get('email') or pdata.get('email') or '').strip().lower()
            new_email = email.strip().lower()
            if cur_email != new_email:
                if isinstance(pdata.get('contact'), dict):
                    pdata['contact']['email'] = new_email
                else:
                    pdata['contact'] = {'email': new_email}
                pdata['email'] = new_email
                changed = True
        if supabase_id and pdata.get('supabaseAuthId') != supabase_id:
            pdata['supabaseAuthId'] = supabase_id
            changed = True
        if changed:
            if _save_partner(pid, pdata):
                action = 'partner_updated'
                n_updated = 1
            else:
                action = 'partner_update_failed'
        else:
            action = 'no_change_needed'
    elif event_type in ('DELETE', 'user.deleted') and pid:
        # Ne brisemo partnera — samo suspenddujemo portal pristup (kill switch)
        pdata['isPortalActive'] = False
        pdata.pop('supabaseAuthId', None)
        if _save_partner(pid, pdata):
            action = 'partner_portal_deactivated'
            n_updated = 1
        else:
            action = 'partner_deactivate_failed'

    log_audit('EDIT', 'webhook',
              f'Supabase Auth webhook: {event_type} email={email} → {action}'
              f'{" (updated " + str(n_updated) + ")" if n_updated else ""}',
              is_suspicious=False)

    return jsonify({
        'ok': True,
        'event': event_type,
        'email': email,
        'supabase_id': supabase_id,
        'action': action,
        'n_updated': n_updated,
    })


# ==========================================================
#  ADMIN: on-demand sync (za slucaj propustenih webhook-ova)
# ==========================================================

@supabase_webhook_bp.route('/api/admin/supabase-auth/sync', methods=['POST'])
@login_required
def admin_sync_auth_users():
    """Za svakog Supabase Auth user-a — pronadji lokalnog partnera po emailu
    i update-uj supabaseAuthId ako fali. Vraca report {matched, missing,
    errors}."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'Admin only.'}), 403
    try:
        from auth_supabase import admin_client
    except ImportError:
        return jsonify({'ok': False, 'error': 'auth_supabase not available'}), 500

    try:
        client = admin_client()
    except Exception as e:
        return jsonify({'ok': False, 'error': f'{type(e).__name__}: {str(e)[:200]}'}), 500

    matched = []
    missing_partner = []
    errors = []

    # Pageaj kroz sve Auth user-e
    page = 1
    while page < 50:
        try:
            resp = client.auth.admin.list_users(page=page, per_page=200)
        except Exception as e:
            errors.append({'page': page, 'error': str(e)[:200]})
            break
        users = resp if isinstance(resp, list) else (getattr(resp, 'users', None) or [])
        if not users:
            break
        for u in users:
            u_email = getattr(u, 'email', None) or (u.get('email') if isinstance(u, dict) else None)
            u_id = getattr(u, 'id', None) or (u.get('id') if isinstance(u, dict) else None)
            if not u_email or not u_id:
                continue
            pid, pdata = _find_partner_by_supabase_id_or_email(str(u_id), u_email)
            if pid and pdata:
                if pdata.get('supabaseAuthId') != str(u_id):
                    pdata['supabaseAuthId'] = str(u_id)
                    if _save_partner(pid, pdata):
                        matched.append({'email': u_email, 'partner_id': pid, 'action': 'linked'})
                    else:
                        errors.append({'email': u_email, 'error': 'upsert failed'})
                else:
                    matched.append({'email': u_email, 'partner_id': pid, 'action': 'already_linked'})
            else:
                missing_partner.append({'email': u_email, 'supabase_id': str(u_id)})
        if len(users) < 200:
            break
        page += 1

    log_audit('EDIT', 'system',
              f'Supabase Auth sync: matched={len(matched)}, orphan_auth_users={len(missing_partner)}',
              is_suspicious=False)
    return jsonify({
        'ok': True,
        'matched': matched,
        'missing_partner': missing_partner,
        'errors': errors,
        'summary': {
            'matched': len(matched),
            'missing_partner': len(missing_partner),
            'errors': len(errors),
        }
    })
