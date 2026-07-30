"""V24.4 SUPABASE-ONLY: report builder metadata (SQL run zahteva RPC funkciju u Supabase)."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, session, render_template
from utils import login_required, log_audit

reports_bp = Blueprint('reports_bp', __name__)


@reports_bp.route('/admin/reports', methods=['GET'])
@login_required
def reports_page():
    if session.get('role') != 'admin':
        return "Admin only.", 403
    return render_template('admin_reports.html')


VALID_CHART_TYPES = {'kpi', 'bar', 'line', 'pie', 'table'}

FORBIDDEN_KEYWORDS = {
    'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'REPLACE',
    'TRUNCATE', 'GRANT', 'REVOKE', 'PRAGMA', 'ATTACH', 'DETACH', 'VACUUM',
    'REINDEX', 'ANALYZE', 'BEGIN', 'COMMIT', 'ROLLBACK', 'SAVEPOINT',
}


def _validate_sql(sql: str) -> tuple[bool, str]:
    if not sql or not isinstance(sql, str):
        return False, 'SQL required'
    sql = sql.strip()
    if len(sql) > 5000:
        return False, 'SQL too long (max 5000 chars)'
    if ';' in sql.rstrip(';'):
        return False, 'Multiple statements not allowed'
    sql_clean = sql.rstrip(';').strip()
    first_word = re.match(r'^\s*(\w+)', sql_clean)
    if not first_word:
        return False, 'Invalid SQL'
    fw = first_word.group(1).upper()
    if fw not in ('SELECT', 'WITH'):
        return False, f'Must start with SELECT or WITH, got {fw}'
    upper = sql_clean.upper()
    for kw in FORBIDDEN_KEYWORDS:
        if re.search(r'\b' + kw + r'\b', upper):
            return False, f'Forbidden keyword: {kw}'
    return True, ''


@reports_bp.route('/api/reports', methods=['GET'])
@login_required
def reports_list():
    """V24.4 SUPABASE-ONLY."""
    uid = session.get('user_id')
    role = session.get('role')
    from data_layer import select as _dl_select
    all_rows = _dl_select('custom_reports', order='-updated_at', limit=1000) or []
    if role != 'admin':
        rows = [r for r in all_rows if r.get('owner_user_id') == uid or r.get('is_shared')]
    else:
        rows = all_rows
    return jsonify({
        'reports': [{
            'id': r.get('id'), 'owner_user_id': r.get('owner_user_id'),
            'title': r.get('title'), 'description': r.get('description'),
            'chart_type': r.get('chart_type'),
            'is_shared': bool(r.get('is_shared')),
            'created_at': r.get('created_at'), 'updated_at': r.get('updated_at'),
            'is_owner': r.get('owner_user_id') == uid,
        } for r in rows]
    })


@reports_bp.route('/api/reports', methods=['POST'])
@login_required
def reports_create():
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    body = request.get_json(silent=True) or {}
    title = str(body.get('title') or '').strip()[:200]
    description = str(body.get('description') or '').strip()[:500]
    sql = str(body.get('sql_query') or '').strip()
    chart_type = str(body.get('chart_type') or 'table').lower()
    is_shared = bool(body.get('is_shared'))
    if not title:
        return jsonify({'error': 'title_required'}), 400
    if chart_type not in VALID_CHART_TYPES:
        return jsonify({'error': f'chart_type must be one of {sorted(VALID_CHART_TYPES)}'}), 400
    ok, err = _validate_sql(sql)
    if not ok:
        return jsonify({'error': 'invalid_sql', 'detail': err}), 400
    rid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    from data_layer import insert as _dl_insert
    _dl_insert('custom_reports', {
        'id': rid, 'owner_user_id': session.get('user_id'),
        'title': title, 'description': description,
        'sql_query': sql, 'chart_type': chart_type,
        'is_shared': is_shared,
        'created_at': now, 'updated_at': now,
    })
    log_audit('CREATE', 'reports', f'Custom report "{title}" ({chart_type})')
    return jsonify({'id': rid, 'title': title, 'chart_type': chart_type})


@reports_bp.route('/api/reports/<rid>', methods=['GET'])
@login_required
def reports_get(rid):
    from data_layer import select_one as _dl_select_one
    r = _dl_select_one('custom_reports', {'id': rid})
    if not r:
        return jsonify({'error': 'not_found'}), 404
    uid = session.get('user_id')
    if r.get('owner_user_id') != uid and not r.get('is_shared') and session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403
    return jsonify({
        'id': r.get('id'), 'owner_user_id': r.get('owner_user_id'),
        'title': r.get('title'), 'description': r.get('description'),
        'sql_query': r.get('sql_query'), 'chart_type': r.get('chart_type'),
        'is_shared': bool(r.get('is_shared')),
        'created_at': r.get('created_at'), 'updated_at': r.get('updated_at'),
    })


@reports_bp.route('/api/reports/<rid>', methods=['DELETE'])
@login_required
def reports_delete(rid):
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    from data_layer import delete as _dl_delete
    n = _dl_delete('custom_reports', {'id': rid})
    return jsonify({'deleted': int(n or 0)})


@reports_bp.route('/api/reports/<rid>/run', methods=['GET'])
@login_required
def reports_run(rid):
    """V24.4: pokretanje custom SQL izvestaja zahteva Postgres RPC funkciju
    u Supabase-u (npr. `run_report(rid uuid)`). Za sada vraca 501 sa jasnom
    porukom umesto da lazno pokrece protiv prazne SQLite-e. Da bi radilo:
        1. U Supabase → SQL Editor kreiraj SECURITY DEFINER funkciju koja
           izvrsava sql_query kolonu bezbedno (RLS/whitelist).
        2. Ovde pozovi data_layer.rpc('run_report', {'rid': rid})."""
    from data_layer import select_one as _dl_select_one
    r = _dl_select_one('custom_reports', {'id': rid})
    if not r:
        return jsonify({'error': 'not_found'}), 404
    if r.get('owner_user_id') != session.get('user_id') and not r.get('is_shared') and session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403

    # Try RPC
    try:
        from data_layer import rpc as _dl_rpc
        data = _dl_rpc('run_report', {'report_id': rid}) or []
        columns = list(data[0].keys()) if data and isinstance(data[0], dict) else []
        rows = [[row.get(c) for c in columns] for row in data] if data else []
        log_audit('READ', 'reports',
                  f'Custom report run via Supabase RPC: "{r.get("title")}" ({len(data)} rows)')
        return jsonify({
            'id': rid, 'title': r.get('title'),
            'chart_type': r.get('chart_type'),
            'columns': columns,
            'rows': [dict(zip(columns, row)) for row in rows],
            'row_count': len(data),
        })
    except Exception as e:
        return jsonify({
            'error': 'rpc_not_configured',
            'detail': ('Report execution requires a Supabase RPC function `run_report(report_id)`. '
                       f'See routes/reports.py comment for setup. RPC error: {str(e)[:200]}'),
        }), 501
