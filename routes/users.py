import json
import sqlite3
import uuid
import re
from flask import Blueprint, request, jsonify, session
from werkzeug.security import generate_password_hash
from config import DB_FILE
from utils import log_audit, login_required

users_bp = Blueprint('users', __name__)

def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=60.0) 
    conn.execute('PRAGMA journal_mode=WAL;') 
    conn.execute('PRAGMA synchronous=NORMAL;')
    conn.execute('PRAGMA busy_timeout=60000;')
    return conn

def is_strong_password(password):
    """Vojni standard: Min 10 karaktera, 1 veliko slovo, 1 broj."""
    if len(password) < 10: return False
    if not re.search(r"[A-Z]", password): return False
    if not re.search(r"[0-9]", password): return False
    return True

@users_bp.route('/api/users', methods=['GET', 'POST'])
@login_required
def manage_users():
    if session.get('role') != 'admin': return jsonify({"error": "Unauthorized"}), 403
    
    if request.method == 'GET':
        conn = None
        users = []
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('SELECT id, username, role, permissions FROM users')
            users = [{"id": r[0], "username": r[1], "role": r[2], "permissions": json.loads(r[3]) if r[3] else {}} for r in c.fetchall()]
        except Exception as e:
            return jsonify({"error": f"Database error. ({str(e)})"}), 500
        finally:
            if conn: conn.close()
        return jsonify(users)
    else:
        data = request.json
        user_id = data.get('id')
        new_username = data.get('username', '').strip()
        role = data.get('role', 'worker')
        perms = json.dumps(data.get('permissions', {}))
        
        if not new_username:
            return jsonify({"error": "missing_username"}), 400
            
        action_log = ''
        msg_log = ''
        
        conn = None
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('BEGIN TRANSACTION;')
            
            if user_id:
                c.execute('SELECT id FROM users WHERE LOWER(username) = LOWER(?) AND id != ?', (new_username, user_id))
            else:
                c.execute('SELECT id FROM users WHERE LOWER(username) = LOWER(?)', (new_username,))
                
            if c.fetchone():
                conn.rollback()
                return jsonify({"error": "user_exists"}), 409

            if not user_id:
                if not data.get('password'):
                    conn.rollback()
                    return jsonify({"error": "missing_password"}), 400
                    
                if not is_strong_password(data['password']):
                    conn.rollback()
                    return jsonify({"error": "Lozinka mora imati najmanje 10 karaktera, jedno veliko slovo i jedan broj."}), 400
                    
                user_id = str(uuid.uuid4())
                # Korišćenje najjačeg SCRYPT algoritma
                safe_hash = generate_password_hash(data['password'], method='scrypt:32768:8:1')
                c.execute('INSERT INTO users (id, username, password, role, permissions) VALUES (?, ?, ?, ?, ?)', 
                          (user_id, new_username, safe_hash, role, perms))
                action_log = 'CREATE'
                msg_log = f'Created user: {new_username}'
            else:
                if data.get('password'):
                    if not is_strong_password(data['password']):
                        conn.rollback()
                        return jsonify({"error": "Lozinka mora imati najmanje 10 karaktera, jedno veliko slovo i jedan broj."}), 400
                    safe_hash = generate_password_hash(data['password'], method='scrypt:32768:8:1')
                    c.execute('UPDATE users SET username=?, password=?, role=?, permissions=? WHERE id=?', 
                              (new_username, safe_hash, role, perms, user_id))
                else:
                    c.execute('UPDATE users SET username=?, role=?, permissions=? WHERE id=?', 
                              (new_username, role, perms, user_id))
                action_log = 'EDIT'
                msg_log = f'Updated user: {new_username}'
                
            conn.commit()
            
            if action_log:
                log_audit(action_log, 'users', msg_log, is_suspicious=False)
                
            return jsonify({"status": "success", "id": user_id})
            
        except Exception as e:
            if conn: conn.rollback()
            return jsonify({"error": f"Internal server error. ({str(e)})"}), 500
        finally:
            if conn: conn.close()

@users_bp.route('/api/users/<user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    if session.get('role') != 'admin': return jsonify({"error": "Unauthorized"}), 403
    
    if user_id == session.get('user_id'):
        return jsonify({"error": "cannot_delete_self"}), 400
        
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('BEGIN TRANSACTION;')
        c.execute('DELETE FROM users WHERE id=?', (user_id,))
        conn.commit()
        
        log_audit('DELETE', 'users', f'Deleted user ID: {user_id}', is_suspicious=False)
        return jsonify({"status": "success"})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"error": f"Internal server error. ({str(e)})"}), 500
    finally:
        if conn: conn.close()