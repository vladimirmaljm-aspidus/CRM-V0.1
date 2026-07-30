"""V24.4 SUPABASE-ONLY: bulk actions, custom fields, API keys, outbound webhooks."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, session, render_template
from utils import login_required, log_audit
import supabase_store as store

v23_extras_bp = Blueprint('v23_extras_bp', __name__)


@v23_extras_bp.route('/admin/custom-fields', methods=['GET'])
@login_required
def custom_fields_page():
    if session.get('role') != 'admin':
        return "Admin only.", 403
    return render_template('admin_custom_fields.html')


@v23_extras_bp.route('/admin/webhooks', methods=['GET'])
@login_required
def webhooks_page():
    if session.get('role') != 'admin':
        return "Admin only.", 403
    return render_template('admin_webhooks.html')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


# =========================================================================
#  BULK ACTIONS
# =========================================================================

BULK_ALLOWED = {
    ('partners',  'archive'),  ('partners',  'unarchive'),  ('partners',  'tag'),   ('partners',  'delete'),
    ('products',  'archive'),  ('products',  'unarchive'),  ('products',  'tag'),   ('products',  'delete'),
    ('deals',     'archive'),  ('deals',     'unarchive'),  ('deals',     'tag'),   ('deals',     'delete'),
    ('offers',    'archive'),  ('offers',    'delete'),
    ('invoices',  'archive'),  ('invoices',  'delete'),
}


@v23_extras_bp.route('/api/bulk/<entity>/<action>', methods=['POST'])
@login_required
def bulk_action(entity, action):
    if (entity, action) not in BULK_ALLOWED:
        return jsonify({'error': 'action_not_allowed_on_entity'}), 400
    body = request.get_json(silent=True) or {}
    ids = body.get('ids') or []
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': 'ids_required'}), 400
    if len(ids) > 500:
        return jsonify({'error': 'too_many_ids_max_500'}), 400
    tag_value = str(body.get('tag') or '').strip()[:60] if action == 'tag' else None

    ok = 0; failed = 0
    for eid in ids:
        try:
            if action == 'delete':
                if store.delete_entity(entity, eid):
                    ok += 1
                else:
                    failed += 1
            else:
                item = store.get_entity(entity, eid)
                if not item:
                    failed += 1
                    continue
                if action == 'archive':
                    item['archived'] = True
                    item['archivedAt'] = _now()
                    item['archivedBy'] = session.get('username')
                elif action == 'unarchive':
                    item['archived'] = False
                    item.pop('archivedAt', None)
                elif action == 'tag':
                    tags = set(item.get('tags') or [])
                    if tag_value:
                        tags.add(tag_value)
                    item['tags'] = sorted(tags)
                item['id'] = eid
                store.upsert_entity(entity, item)
                ok += 1
        except Exception:
            failed += 1

    log_audit('EDIT', entity, f'Bulk {action}: {ok} OK, {failed} failed ({tag_value or "-"})')
    try:
        emit_event(f'{entity}.bulk_{action}', {'count_ok': ok, 'count_failed': failed, 'ids': ids[:50]})
    except Exception:
        pass
    return jsonify({'ok': ok, 'failed': failed})


# =========================================================================
#  CUSTOM FIELDS
# =========================================================================

@v23_extras_bp.route('/api/custom-fields', methods=['GET'])
@login_required
def list_custom_fields():
    entity = request.args.get('entity', '')
    from data_layer import select as _dl_select
    filters = {'is_active': True}
    if entity:
        filters['entity_type'] = entity
    rows = _dl_select('custom_field_defs', filters=filters, order='display_order') or []
    out = []
    for r in rows:
        opts = None
        raw = r.get('options_json')
        if isinstance(raw, dict):
            opts = raw
        elif isinstance(raw, str):
            try: opts = json.loads(raw)
            except Exception: opts = None
        out.append({
            'id': r.get('id'), 'entity_type': r.get('entity_type'),
            'field_key': r.get('field_key'),
            'field_label': r.get('field_label'), 'field_type': r.get('field_type'),
            'options': opts, 'required': bool(r.get('required')),
            'display_order': r.get('display_order'),
        })
    return jsonify({'fields': out})


@v23_extras_bp.route('/api/custom-fields', methods=['POST'])
@login_required
def create_custom_field():
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    body = request.get_json(silent=True) or {}
    entity = str(body.get('entity_type') or '').strip()[:40]
    key = str(body.get('field_key') or '').strip().lower()[:60]
    label = str(body.get('field_label') or '').strip()[:120]
    ftype = str(body.get('field_type') or 'text').strip()
    if ftype not in ('text', 'number', 'date', 'bool', 'select', 'url', 'email'):
        return jsonify({'error': 'invalid_type'}), 400
    if not entity or not key or not label:
        return jsonify({'error': 'entity_key_label_required'}), 400
    opts = body.get('options') if isinstance(body.get('options'), list) else None
    fid = str(uuid.uuid4())
    # V24.4: rucna provera duplog (entity_type, field_key) — Supabase UNIQUE
    # constraint takodje pukne, ali ovde uhvatimo pre INSERT-a za bolji msg
    from data_layer import select as _dl_select, insert as _dl_insert
    dupe = _dl_select('custom_field_defs',
                      filters={'entity_type': entity, 'field_key': key, 'is_active': True},
                      limit=1) or []
    if dupe:
        return jsonify({'error': 'field_key_already_exists'}), 409
    try:
        _dl_insert('custom_field_defs', {
            'id': fid, 'entity_type': entity, 'field_key': key,
            'field_label': label, 'field_type': ftype,
            'options_json': opts,
            'required': bool(body.get('required')),
            'display_order': int(body.get('display_order') or 100),
            'is_active': True,
            'created_at': _now(),
        })
    except Exception as e:
        if 'duplicate' in str(e).lower() or '23505' in str(e):
            return jsonify({'error': 'field_key_already_exists'}), 409
        raise
    return jsonify({'id': fid})


@v23_extras_bp.route('/api/custom-fields/<fid>', methods=['DELETE'])
@login_required
def delete_custom_field(fid):
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    from data_layer import update as _dl_update
    _dl_update('custom_field_defs', {'id': fid}, {'is_active': False})
    return jsonify({'deleted': True})


# =========================================================================
#  API KEYS
# =========================================================================

def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


@v23_extras_bp.route('/api/api-keys', methods=['GET'])
@login_required
def list_api_keys():
    uid = session.get('user_id')
    is_admin = session.get('role') == 'admin'
    from data_layer import select as _dl_select
    filters = {} if is_admin else {'owner_user_id': uid}
    rows = _dl_select('api_keys', filters=filters, order='-created_at') or []
    return jsonify({
        'keys': [{
            'id': r.get('id'), 'name': r.get('name'),
            'key_prefix': r.get('key_prefix'), 'owner': r.get('owner_user_id'),
            'scope': r.get('scope'),
            'rate_limit_per_min': r.get('rate_limit_per_min'),
            'created_at': r.get('created_at'), 'last_used_at': r.get('last_used_at'),
            'revoked': bool(r.get('revoked')),
        } for r in rows]
    })


@v23_extras_bp.route('/api/api-keys', methods=['POST'])
@login_required
def create_api_key():
    body = request.get_json(silent=True) or {}
    name = str(body.get('name') or '').strip()[:100]
    if not name:
        return jsonify({'error': 'name_required'}), 400
    scope = str(body.get('scope') or 'read')
    if scope not in ('read', 'write', 'admin'):
        return jsonify({'error': 'invalid_scope'}), 400
    if scope == 'admin' and session.get('role') != 'admin':
        return jsonify({'error': 'admin_scope_admin_only'}), 403
    raw = 'ask_' + secrets.token_urlsafe(40)
    prefix = raw[:12]
    kid = str(uuid.uuid4())
    from data_layer import insert as _dl_insert
    _dl_insert('api_keys', {
        'id': kid, 'name': name, 'key_hash': _hash_key(raw),
        'key_prefix': prefix, 'owner_user_id': session.get('user_id'),
        'scope': scope, 'rate_limit_per_min': int(body.get('rate_limit_per_min') or 60),
        'created_at': _now(), 'revoked': False,
    })
    log_audit('CREATE', 'api_key', f'Created API key "{name}" ({scope})', is_suspicious=True)
    return jsonify({'id': kid, 'raw_key': raw, 'name': name, 'scope': scope,
                    'warning': 'Save this key now — it will not be shown again.'})


@v23_extras_bp.route('/api/api-keys/<kid>/revoke', methods=['POST'])
@login_required
def revoke_api_key(kid):
    uid = session.get('user_id')
    is_admin = session.get('role') == 'admin'
    from data_layer import update as _dl_update
    filters = {'id': kid} if is_admin else {'id': kid, 'owner_user_id': uid}
    n = _dl_update('api_keys', filters, {'revoked': True, 'revoked_at': _now()})
    log_audit('SECURITY', 'api_key', f'Revoked API key {kid}')
    return jsonify({'revoked': len(n) if isinstance(n, list) else int(bool(n))})


def verify_api_key(bearer_token: str) -> dict | None:
    """V24.4 SUPABASE-ONLY."""
    if not bearer_token or not bearer_token.startswith('ask_'):
        return None
    h = _hash_key(bearer_token)
    from data_layer import select as _dl_select, update as _dl_update
    rows = _dl_select('api_keys', filters={'key_hash': h, 'revoked': False}, limit=1) or []
    if not rows:
        return None
    ak = rows[0]
    u = store.get_user_by_id(ak.get('owner_user_id')) or {}
    try:
        _dl_update('api_keys', {'id': ak.get('id')}, {'last_used_at': _now()})
    except Exception:
        pass
    return {
        'key_id': ak.get('id'), 'user_id': ak.get('owner_user_id'),
        'scope': ak.get('scope'),
        'rate_limit_per_min': ak.get('rate_limit_per_min'),
        'username': u.get('username'), 'role': u.get('role'),
    }


# =========================================================================
#  OUTBOUND WEBHOOKS
# =========================================================================

@v23_extras_bp.route('/api/webhooks', methods=['GET'])
@login_required
def list_webhooks():
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    from data_layer import select as _dl_select
    rows = _dl_select('outbound_webhooks', order='-created_at') or []
    return jsonify({
        'webhooks': [{
            'id': r.get('id'), 'name': r.get('name'),
            'target_url': r.get('target_url'),
            'events': (str(r.get('events') or '')).split(','),
            'is_active': bool(r.get('is_active')),
            'created_at': r.get('created_at'),
            'created_by': r.get('created_by'),
            'last_fired_at': r.get('last_fired_at'),
            'last_status': r.get('last_status'),
            'fail_count': r.get('fail_count'),
        } for r in rows]
    })


@v23_extras_bp.route('/api/webhooks', methods=['POST'])
@login_required
def create_webhook():
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    body = request.get_json(silent=True) or {}
    name = str(body.get('name') or '').strip()[:100]
    url = str(body.get('target_url') or '').strip()[:500]
    events = body.get('events') or []
    if not name or not url.startswith(('http://', 'https://')):
        return jsonify({'error': 'name_and_valid_url_required'}), 400
    if not events or not isinstance(events, list):
        return jsonify({'error': 'events_list_required'}), 400
    events_csv = ','.join([str(e).strip()[:60] for e in events if str(e).strip()])[:1000]
    secret = 'whsec_' + secrets.token_urlsafe(32)
    wid = str(uuid.uuid4())
    from data_layer import insert as _dl_insert
    _dl_insert('outbound_webhooks', {
        'id': wid, 'name': name, 'target_url': url,
        'events': events_csv, 'secret': secret,
        'is_active': True, 'created_at': _now(),
        'created_by': session.get('username'), 'fail_count': 0,
    })
    log_audit('CREATE', 'webhook', f'Created webhook "{name}" → {url}', is_suspicious=True)
    return jsonify({'id': wid, 'secret': secret,
                    'warning': 'Signing secret shown once — save it now.'})


@v23_extras_bp.route('/api/webhooks/<wid>', methods=['DELETE'])
@login_required
def delete_webhook(wid):
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    from data_layer import delete as _dl_delete
    _dl_delete('outbound_webhooks', {'id': wid})
    _dl_delete('webhook_deliveries', {'webhook_id': wid})
    return jsonify({'deleted': True})


@v23_extras_bp.route('/api/webhooks/<wid>/deliveries', methods=['GET'])
@login_required
def webhook_deliveries(wid):
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    from data_layer import select as _dl_select
    rows = _dl_select('webhook_deliveries', filters={'webhook_id': wid},
                      order='-delivered_at', limit=100) or []
    return jsonify({
        'deliveries': [{
            'id': r.get('id'), 'event': r.get('event'),
            'status_code': r.get('status_code'),
            'response': r.get('response_snippet'),
            'delivered_at': r.get('delivered_at'),
            'duration_ms': r.get('duration_ms'),
        } for r in rows]
    })


# =========================================================================
#  emit_event / _deliver_webhook
# =========================================================================

def emit_event(event_name: str, payload: dict) -> None:
    """V24.4 SUPABASE-ONLY."""
    try:
        from data_layer import select as _dl_select
        rows = _dl_select('outbound_webhooks', filters={'is_active': True}) or []
    except Exception:
        return
    for r in rows:
        wid = r.get('id'); url = r.get('target_url')
        events_csv = r.get('events') or ''
        secret = r.get('secret') or ''
        events = str(events_csv).split(',')
        matched = False
        for ev in events:
            ev = ev.strip()
            if ev == '*' or ev == event_name:
                matched = True; break
            if ev.endswith('.*') and event_name.startswith(ev[:-1]):
                matched = True; break
        if not matched:
            continue
        t = threading.Thread(target=_deliver_webhook,
                             args=(wid, url, secret, event_name, payload),
                             daemon=True)
        t.start()


def _deliver_webhook(wid, url, secret, event, payload):
    """V24.4 SUPABASE-ONLY."""
    import urllib.request, urllib.error
    body_dict = {'event': event, 'delivered_at': _now(), 'payload': payload}
    body = json.dumps(body_dict).encode('utf-8')
    sig = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    started = time.time()
    status = 0; snippet = ''
    try:
        req = urllib.request.Request(url, data=body, headers={
            'Content-Type': 'application/json',
            'X-Aspidus-Event': event,
            'X-Aspidus-Signature': f'sha256={sig}',
            'User-Agent': 'Aspidus-Webhook/1.0',
        }, method='POST')
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            snippet = resp.read(500).decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            snippet = e.read(500).decode('utf-8', errors='replace')
        except Exception:
            snippet = str(e)
    except Exception as e:
        status = 0
        snippet = f'{type(e).__name__}: {e}'
    duration_ms = int((time.time() - started) * 1000)
    outcome = 'ok' if 200 <= status < 300 else ('4xx' if 400 <= status < 500 else ('5xx' if status >= 500 else 'timeout'))

    try:
        from data_layer import insert as _dl_insert, update as _dl_update, select_one as _dl_select_one
        _dl_insert('webhook_deliveries', {
            'id': str(uuid.uuid4()), 'webhook_id': wid, 'event': event,
            'payload_hash': hashlib.sha256(body).hexdigest()[:32],
            'status_code': status, 'response_snippet': snippet[:500],
            'delivered_at': _now(), 'duration_ms': duration_ms,
        })
        if outcome == 'ok':
            _dl_update('outbound_webhooks', {'id': wid},
                       {'last_fired_at': _now(), 'last_status': 'ok', 'fail_count': 0})
        else:
            row = _dl_select_one('outbound_webhooks', {'id': wid}) or {}
            new_fc = int(row.get('fail_count', 0) or 0) + 1
            patch = {'last_fired_at': _now(), 'last_status': outcome, 'fail_count': new_fc}
            if new_fc >= 20:
                patch['is_active'] = False
            _dl_update('outbound_webhooks', {'id': wid}, patch)
    except Exception:
        pass
