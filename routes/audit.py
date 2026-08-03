from flask import Blueprint, request, jsonify, session
from utils import log_audit, login_required, decrypt_data
import supabase_store as store

audit_bp = Blueprint('audit', __name__)

def user_has_permission(perm_key):
    """Admin uvek; inače proverava eksplicitnu permisiju iz korisničkog naloga.
    V24.0 SUPABASE-ONLY: čita user-a preko supabase_store.get_user_by_id
    (rehidrira top-level kolone + JSONB data, uključujući `permissions`)."""
    if session.get('role') == 'admin':
        return True
    if 'user_id' not in session:
        return False
    user = store.get_user_by_id(session['user_id']) or {}
    perms = user.get('permissions') or {}
    if isinstance(perms, str):
        # Legacy — permissions sačuvane kao JSON string
        try:
            import json as _json
            perms = _json.loads(perms)
        except Exception:
            perms = {}
    return bool(isinstance(perms, dict) and perms.get(perm_key))

@audit_bp.route('/api/audit/event', methods=['POST'])
@login_required
def log_client_event():
    data = request.json
    log_audit(data.get('action', 'UNKNOWN'), data.get('module', 'system'), data.get('details', ''))
    return jsonify({"status": "success"})

@audit_bp.route('/api/audit_logs', methods=['GET'])
@login_required
def get_audit_logs():
    # Admin ili radnik kome je admin eksplicitno dodelio 'audit_view'.
    if not user_has_permission('audit_view'):
        log_audit('SECURITY', 'audit', 'Unauthorized attempt to access audit logs', is_suspicious=True)
        return jsonify({"error": "Unauthorized"}), 403

    # V24.0 SUPABASE-ONLY: čita audit_logs preko data_layer.select.
    # Redosled je po timestamp DESC (Supabase audit_logs.timestamp je TIMESTAMPTZ).
    try:
        from data_layer import select as _dl_select
        rows = _dl_select('audit_logs', order='-timestamp', limit=1000) or []
        logs = [{
            "id": r.get('id') or r.get('sync_id'),
            "username": r.get('username'),
            "action": r.get('action'),
            "module": r.get('module'),
            "details": r.get('details'),
            "ip": r.get('ip_address'),
            "user_agent": r.get('user_agent'),
            "timestamp": r.get('timestamp'),
            "is_suspicious": bool(r.get('is_suspicious')),
            "location": r.get('location') or 'N/A',
        } for r in rows]
    except Exception:
        logs = []

    return jsonify(logs)
