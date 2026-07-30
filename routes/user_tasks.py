"""V24.2 SUPABASE-ONLY: user tasks / TODOs."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, session
from utils import login_required

user_tasks_bp = Blueprint('user_tasks_bp', __name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


@user_tasks_bp.route('/api/tasks', methods=['GET'])
@login_required
def list_tasks():
    """V24.2 SUPABASE-ONLY."""
    uid = session.get('user_id')
    status = request.args.get('status')
    from data_layer import select as _dl_select
    filters = {'owner_user_id': uid}
    if status and status != 'all':
        filters['status'] = status
    rows = _dl_select('user_tasks', filters=filters, limit=1000) or []
    # Custom sort — open pre done, po priority asc pa due_at asc
    def sort_key(r):
        status_order = 0 if r.get('status') == 'open' else 1
        return (status_order,
                int(r.get('priority') or 999),
                r.get('due_at') or 'zzzz',
                r.get('created_at') or '')
    rows.sort(key=sort_key)
    return jsonify({
        'tasks': [{
            'id': r.get('id'), 'title': r.get('title'),
            'description': r.get('description'), 'due_at': r.get('due_at'),
            'priority': r.get('priority'), 'status': r.get('status'),
            'linked_entity_type': r.get('linked_entity_type'),
            'linked_entity_id': r.get('linked_entity_id'),
            'created_at': r.get('created_at'), 'completed_at': r.get('completed_at'),
        } for r in rows]
    })


@user_tasks_bp.route('/api/tasks', methods=['POST'])
@login_required
def create_task():
    body = request.get_json(silent=True) or {}
    title = str(body.get('title') or '').strip()[:200]
    if not title:
        return jsonify({'error': 'title_required'}), 400
    tid = str(uuid.uuid4())
    from data_layer import insert as _dl_insert
    _dl_insert('user_tasks', {
        'id': tid, 'owner_user_id': session.get('user_id'),
        'title': title,
        'description': str(body.get('description') or '')[:1000],
        'due_at': str(body.get('due_at') or '') or None,
        'priority': int(body.get('priority') or 2),
        'status': 'open',
        'linked_entity_type': str(body.get('linked_entity_type') or '') or None,
        'linked_entity_id': str(body.get('linked_entity_id') or '') or None,
        'created_at': _now(),
    })
    return jsonify({'id': tid, 'title': title})


@user_tasks_bp.route('/api/tasks/<tid>', methods=['PATCH'])
@login_required
def update_task(tid):
    body = request.get_json(silent=True) or {}
    allowed = {'title': 200, 'description': 1000, 'due_at': 40, 'priority': None, 'status': 20}
    updates = {}
    for k, maxlen in allowed.items():
        if k in body:
            v = body[k]
            if maxlen and isinstance(v, str):
                v = v[:maxlen]
            updates[k] = v
    if not updates:
        return jsonify({'error': 'no_changes'}), 400
    from data_layer import update as _dl_update
    n = _dl_update('user_tasks', {'id': tid, 'owner_user_id': session.get('user_id')}, updates)
    if not n:
        return jsonify({'error': 'not_found'}), 404
    return jsonify({'updated': True})


@user_tasks_bp.route('/api/tasks/<tid>', methods=['DELETE'])
@login_required
def delete_task(tid):
    from data_layer import delete as _dl_delete
    n = _dl_delete('user_tasks', {'id': tid, 'owner_user_id': session.get('user_id')})
    return jsonify({'deleted': int(n or 0)})


@user_tasks_bp.route('/api/tasks/<tid>/complete', methods=['POST'])
@login_required
def complete_task(tid):
    from data_layer import update as _dl_update
    n = _dl_update('user_tasks', {'id': tid, 'owner_user_id': session.get('user_id')},
                   {'status': 'done', 'completed_at': _now()})
    if not n:
        return jsonify({'error': 'not_found'}), 404
    return jsonify({'completed': True})


@user_tasks_bp.route('/api/tasks/entity/<etype>/<eid>', methods=['GET'])
@login_required
def entity_tasks(etype, eid):
    """V24.2 SUPABASE-ONLY: svi taskovi (svih usera) vezani za dati entity."""
    from data_layer import select as _dl_select
    rows = _dl_select('user_tasks',
                      filters={'linked_entity_type': etype, 'linked_entity_id': eid},
                      limit=500) or []
    # Ime user-a — resolvuj preko in-memory cache-a
    from data_layer import select as _s
    users = _s('users', limit=5000) or []
    user_map = {u.get('id'): u.get('username') for u in users}
    rows.sort(key=lambda r: (r.get('status') != 'open', r.get('due_at') or 'zzzz'))
    return jsonify({
        'tasks': [{
            'id': r.get('id'), 'title': r.get('title'),
            'status': r.get('status'), 'due_at': r.get('due_at'),
            'priority': r.get('priority'),
            'owner_user_id': r.get('owner_user_id'),
            'username': user_map.get(r.get('owner_user_id'), '?'),
        } for r in rows]
    })
