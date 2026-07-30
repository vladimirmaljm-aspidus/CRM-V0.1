"""V24.2 SUPABASE-ONLY: saved user filters."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, session
from utils import login_required

saved_filters_bp = Blueprint('saved_filters_bp', __name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


@saved_filters_bp.route('/api/filters', methods=['GET'])
@login_required
def list_filters():
    """V24.2 SUPABASE-ONLY."""
    uid = session.get('user_id')
    entity = request.args.get('entity', '')
    from data_layer import select as _dl_select
    all_rows = _dl_select('saved_filters', limit=1000) or []
    # PostgREST ne moze OR (owner_user_id=? OR is_shared=1) u jednom filteru →
    # povuci sve + filtriraj u Pythonu (tabela je mala, per-user)
    rows = [r for r in all_rows
            if (r.get('owner_user_id') == uid or r.get('is_shared'))
               and (not entity or r.get('entity_type') == entity)]
    rows.sort(key=lambda r: (r.get('entity_type') or '', r.get('name') or ''))
    return jsonify({
        'filters': [{
            'id': r.get('id'), 'name': r.get('name'),
            'entity_type': r.get('entity_type'),
            'filter': _parse_json(r.get('filter_json')),
            'is_shared': bool(r.get('is_shared')),
            'is_owner': r.get('owner_user_id') == uid,
            'created_at': r.get('created_at'), 'updated_at': r.get('updated_at'),
        } for r in rows]
    })


def _parse_json(v):
    if isinstance(v, dict): return v
    if isinstance(v, str):
        try: return json.loads(v)
        except Exception: return {}
    return {}


@saved_filters_bp.route('/api/filters', methods=['POST'])
@login_required
def create_filter():
    body = request.get_json(silent=True) or {}
    name = str(body.get('name') or '').strip()[:100]
    entity = str(body.get('entity_type') or '').strip()[:40]
    filt = body.get('filter') if isinstance(body.get('filter'), dict) else None
    if not name or not entity or filt is None:
        return jsonify({'error': 'name_entity_filter_required'}), 400
    fid = str(uuid.uuid4())
    now = _now()
    from data_layer import insert as _dl_insert
    _dl_insert('saved_filters', {
        'id': fid, 'owner_user_id': session.get('user_id'),
        'name': name, 'entity_type': entity,
        'filter_json': filt,
        'is_shared': bool(body.get('is_shared')),
        'created_at': now, 'updated_at': now,
    })
    return jsonify({'id': fid})


@saved_filters_bp.route('/api/filters/<fid>', methods=['PATCH'])
@login_required
def update_filter(fid):
    body = request.get_json(silent=True) or {}
    uid = session.get('user_id')
    updates = {}
    if 'name' in body:
        updates['name'] = str(body['name'])[:100]
    if 'filter' in body and isinstance(body['filter'], dict):
        updates['filter_json'] = body['filter']
    if 'is_shared' in body:
        updates['is_shared'] = bool(body['is_shared'])
    if not updates:
        return jsonify({'error': 'no_changes'}), 400
    updates['updated_at'] = _now()
    from data_layer import update as _dl_update
    n = _dl_update('saved_filters', {'id': fid, 'owner_user_id': uid}, updates)
    if not n:
        return jsonify({'error': 'not_found_or_not_owner'}), 404
    return jsonify({'updated': True})


@saved_filters_bp.route('/api/filters/<fid>', methods=['DELETE'])
@login_required
def delete_filter(fid):
    from data_layer import delete as _dl_delete
    n = _dl_delete('saved_filters', {'id': fid, 'owner_user_id': session.get('user_id')})
    return jsonify({'deleted': int(n or 0)})
