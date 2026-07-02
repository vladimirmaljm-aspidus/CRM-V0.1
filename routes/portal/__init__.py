import sqlite3
import json
import time
from flask import Blueprint
from config import PORTAL_DB_FILE
from utils import decrypt_data, FirewallCache

portal_bp = Blueprint('portal', __name__)

portal_otps = {}
portal_auth_sessions = {}

def init_portal_db():
    conn = None
    try:
        conn = sqlite3.connect(PORTAL_DB_FILE, timeout=30.0)
        conn.execute('PRAGMA journal_mode=WAL;')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS kyc_submissions 
                     (id TEXT PRIMARY KEY, partner_id TEXT, token TEXT, data JSON, submitted_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS portal_products 
                     (id TEXT PRIMARY KEY, partner_id TEXT, data JSON, status TEXT, created_at TEXT)''')
        conn.commit()
    finally:
        if conn: conn.close()

init_portal_db()

def check_portal_rate_limit(ip):
    if ip in FirewallCache.whitelist: return True
    now = time.time()
    FirewallCache.portal_attempts[ip] = [t for t in FirewallCache.portal_attempts.get(ip, []) if now - t < 60]
    if len(FirewallCache.portal_attempts.get(ip, [])) > 50: return False
    FirewallCache.portal_attempts.setdefault(ip, []).append(now)
    return True

def safe_parse(data_str):
    try:
        return json.loads(data_str)
    except:
        return decrypt_data(data_str)

# Učitavanje svih modula kako bi rute bile aktivne
from . import auth, data, actions