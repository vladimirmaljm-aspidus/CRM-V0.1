import sqlite3
from flask import Blueprint, request, jsonify, session
from config import AUDIT_DB_FILE
from utils import log_audit, login_required

audit_bp = Blueprint('audit', __name__)

@audit_bp.route('/api/audit/event', methods=['POST'])
@login_required
def log_client_event():
    data = request.json
    log_audit(data.get('action', 'UNKNOWN'), data.get('module', 'system'), data.get('details', ''))
    return jsonify({"status": "success"})

@audit_bp.route('/api/audit_logs', methods=['GET'])
@login_required
def get_audit_logs():
    if session.get('role') != 'admin':
        log_audit('SECURITY', 'audit', 'Unauthorized attempt to access audit logs', is_suspicious=True)
        return jsonify({"error": "Unauthorized"}), 403
    
    conn = None
    logs = []
    try:
        conn = sqlite3.connect(AUDIT_DB_FILE, timeout=30.0)
        conn.execute('PRAGMA journal_mode=WAL;')
        c = conn.cursor()
        c.execute('SELECT id, username, action, module, details, ip_address, user_agent, timestamp, is_suspicious, location FROM audit_logs ORDER BY timestamp DESC')
        logs = [{"id": r[0], "username": r[1], "action": r[2], "module": r[3], "details": r[4], "ip": r[5], "user_agent": r[6], "timestamp": r[7], "is_suspicious": bool(r[8]), "location": r[9] if r[9] else 'N/A'} for r in c.fetchall()]
    finally:
        if conn: conn.close()
        
    return jsonify(logs)