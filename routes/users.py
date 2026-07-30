"""V24.1 SUPABASE-ONLY: users CRUD (list/create/update/delete) direktno u Supabase."""
import json
import uuid
import re
from flask import Blueprint, request, jsonify, session
from werkzeug.security import generate_password_hash
from utils import log_audit, login_required
import supabase_store as store

users_bp = Blueprint('users', __name__)


def is_strong_password(password):
    """Vojni standard: Min 10 karaktera, 1 veliko slovo, 1 broj."""
    if len(password) < 10: return False
    if not re.search(r"[A-Z]", password): return False
    if not re.search(r"[0-9]", password): return False
    return True


@users_bp.route('/api/users', methods=['GET', 'POST'])
@login_required
def manage_users():
    if session.get('role') != 'admin':
        return jsonify({"error": "Unauthorized"}), 403

    if request.method == 'GET':
        try:
            from data_layer import select
            rows = select('users') or []
            users = []
            for r in rows:
                perms = r.get('permissions') or {}
                if isinstance(perms, str):
                    try: perms = json.loads(perms)
                    except Exception: perms = {}
                users.append({
                    "id": r.get('id'),
                    "username": r.get('username'),
                    "role": r.get('role'),
                    "permissions": perms,
                })
            return jsonify(users)
        except Exception as e:
            return jsonify({"error": f"Database error. ({str(e)})"}), 500

    # POST — create/update
    data = request.get_json(silent=True) or {}
    user_id = data.get('id')
    new_username = str(data.get('username', '')).strip()
    role = data.get('role', 'worker')
    perms = data.get('permissions', {})
    if isinstance(perms, str):
        try: perms = json.loads(perms)
        except Exception: perms = {}

    if not new_username:
        return jsonify({"error": "missing_username"}), 400

    try:
        # Provera duplog username-a
        from data_layer import select as _dl_select
        all_users = _dl_select('users') or []
        for u in all_users:
            if str(u.get('username', '')).lower() == new_username.lower():
                if not user_id or u.get('id') != user_id:
                    return jsonify({"error": "user_exists"}), 409

        # Provera da li user_id postoji
        existing = store.get_user_by_id(user_id) if user_id else None

        if not existing:
            if not data.get('password'):
                return jsonify({"error": "missing_password"}), 400
            if not is_strong_password(data['password']):
                return jsonify({"error": "Lozinka mora imati najmanje 10 karaktera, jedno veliko slovo i jedan broj."}), 400
            if not user_id:
                user_id = str(uuid.uuid4())
            pw_hash = generate_password_hash(data['password'], method='scrypt:32768:8:1')
            store.upsert_user({
                'id': user_id,
                'username': new_username,
                'password': pw_hash,
                'role': role,
                'permissions': perms,
                'token_version': 1,
            })
            log_audit('CREATE', 'users', f'Created user: {new_username}', is_suspicious=False)
        else:
            row = {
                'id': user_id,
                'username': new_username,
                'role': role,
                'permissions': perms,
            }
            if data.get('password'):
                if not is_strong_password(data['password']):
                    return jsonify({"error": "Lozinka mora imati najmanje 10 karaktera, jedno veliko slovo i jedan broj."}), 400
                row['password'] = generate_password_hash(data['password'], method='scrypt:32768:8:1')
            store.upsert_user(row)
            log_audit('EDIT', 'users', f'Updated user: {new_username}', is_suspicious=False)

        return jsonify({"status": "success", "id": user_id})

    except Exception as e:
        return jsonify({"error": f"Internal server error. ({str(e)})"}), 500


@users_bp.route('/api/users/<user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    if session.get('role') != 'admin':
        return jsonify({"error": "Unauthorized"}), 403
    if user_id == session.get('user_id'):
        return jsonify({"error": "cannot_delete_self"}), 400
    try:
        from data_layer import delete as _dl_delete
        _dl_delete('users', {'id': user_id})
        log_audit('DELETE', 'users', f'Deleted user ID: {user_id}', is_suspicious=False)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": f"Internal server error. ({str(e)})"}), 500
