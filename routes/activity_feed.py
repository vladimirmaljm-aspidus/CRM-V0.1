"""V24.1 SUPABASE-ONLY: real-time activity feed nad Supabase audit_logs."""
from __future__ import annotations

from flask import Blueprint, jsonify, request, session
from utils import login_required

activity_feed_bp = Blueprint('activity_feed_bp', __name__)


_RELEVANT = {'CREATE', 'EDIT', 'DELETE', 'LOGIN', 'ACCEPT', 'REJECT', 'UPLOAD', 'SEND'}
_MODULES_EXCLUDED = {'firewall', 'session'}


@activity_feed_bp.route('/api/activity/recent', methods=['GET'])
@login_required
def recent_activity():
    limit = max(1, min(int(request.args.get('limit', 25)), 100))
    from data_layer import select as _dl_select
    # PostgREST filter set nije jednostavan za "action IN AND module NOT IN"
    # + limit — povuci vise redova pa filtriraj u Pythonu (audit je mali).
    rows = _dl_select('audit_logs', order='-timestamp', limit=max(limit * 4, 200)) or []
    filtered = [r for r in rows
                if (r.get('action') in _RELEVANT
                    and r.get('module') not in _MODULES_EXCLUDED)]
    return jsonify({
        'entries': [{
            'timestamp': r.get('timestamp'),
            'username': r.get('username'),
            'action': r.get('action'),
            'module': r.get('module'),
            'details': r.get('details'),
        } for r in filtered[:limit]]
    })


@activity_feed_bp.route('/api/activity/mine', methods=['GET'])
@login_required
def my_activity():
    limit = max(1, min(int(request.args.get('limit', 25)), 100))
    uid = session.get('user_id')
    from data_layer import select as _dl_select
    rows = _dl_select('audit_logs',
                      filters={'user_id': uid},
                      order='-timestamp', limit=limit) or []
    return jsonify({
        'entries': [{
            'timestamp': r.get('timestamp'),
            'action': r.get('action'),
            'module': r.get('module'),
            'details': r.get('details'),
        } for r in rows]
    })
