"""Admin API endpointi za Supabase migraciju.

Kroz ove endpoint-e admin iz CRM UI-ja može:
  * Testirati Supabase konekciju
  * Videti trenutni status migracije (šta je gde)
  * Pokrenuti dry-run migracije
  * Pokrenuti pravu migraciju (background thread da ne blokira request)
  * Prebaciti feature flag (USE_SUPABASE_DB) live bez editovanja .env-a
  * Emergency rollback

Sve operacije su admin-only i loguju se u audit_log.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Blueprint, request, jsonify, session, render_template

from utils import login_required, log_audit

supabase_admin_bp = Blueprint('supabase_admin', __name__)


@supabase_admin_bp.route('/admin/supabase', methods=['GET'])
@login_required
def supabase_admin_page():
    """Admin panel — dugmad za migraciju, status displej."""
    if session.get('role') != 'admin':
        return "Admin only.", 403
    return render_template('supabase_admin.html')


# ==========================================================
#  ERROR LOG — admin uvid u sve 5xx greške i security incidents
# ==========================================================

_ERROR_BUFFER = []       # in-memory ring buffer, poslednjih 500 grešaka
_ERROR_BUFFER_MAX = 500
_error_lock = threading.Lock()


def record_error(context: str, exc: Exception | str, request_id: str | None = None, meta: dict | None = None):
    """Poziva se iz svih backend-a kad neka operacija baci grešku koju
    treba da admin vidi. Čuva se u in-memory buffer-u + audit_log."""
    import traceback
    tb = ""
    msg = str(exc)
    if isinstance(exc, Exception):
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-2000:]

    entry = {
        "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "context": context,
        "message": msg[:500],
        "traceback": tb,
        "request_id": request_id,
        "meta": meta or {},
    }
    with _error_lock:
        _ERROR_BUFFER.append(entry)
        if len(_ERROR_BUFFER) > _ERROR_BUFFER_MAX:
            _ERROR_BUFFER.pop(0)

    try:
        log_audit('ERROR', context, f'{msg[:400]} (req={request_id or "-"})',
                  is_suspicious=False)
    except Exception:
        pass


@supabase_admin_bp.route('/admin/errors', methods=['GET'])
@login_required
def admin_errors_page():
    """HTML stranica za admin — pregled poslednjih grešaka."""
    if session.get('role') != 'admin':
        return "Admin only.", 403
    return render_template('admin_errors.html')


@supabase_admin_bp.route('/api/admin/errors', methods=['GET'])
@login_required
def admin_errors_api():
    """JSON — vraća poslednjih N zapisa iz error buffer-a."""
    if session.get('role') != 'admin':
        return jsonify({"error": "Admin only."}), 403
    limit = int(request.args.get('limit', 100))
    with _error_lock:
        entries = list(reversed(_ERROR_BUFFER[-limit:]))
    return jsonify({"entries": entries, "total": len(_ERROR_BUFFER)})


@supabase_admin_bp.route('/api/admin/errors/clear', methods=['POST'])
@login_required
def admin_errors_clear():
    if session.get('role') != 'admin':
        return jsonify({"error": "Admin only."}), 403
    with _error_lock:
        n = len(_ERROR_BUFFER)
        _ERROR_BUFFER.clear()
    log_audit('EDIT', 'system', f'Admin cleared error buffer ({n} entries)', is_suspicious=False)
    return jsonify({"status": "ok", "cleared": n})

# In-memory migracija stanje — vidljivo kroz /status endpoint
_migration_state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "log": [],
    "tables": {},
    "error": None,
}
_migration_lock = threading.Lock()


def _admin_only():
    """Vraća None ako je admin, ili (response, code) tuple ako nije."""
    if session.get('role') != 'admin':
        log_audit('SECURITY', 'system',
                  f'Non-admin tried supabase-admin endpoint from '
                  f'{request.headers.get("X-Forwarded-For", request.remote_addr)}',
                  is_suspicious=True)
        return jsonify({"error": "Admin only."}), 403
    return None


@supabase_admin_bp.route('/api/supabase/status', methods=['GET'])
@login_required
def supabase_status():
    """Vraća pun status Supabase integracije — flag-ovi, konekcija, brojevi
    redova u SQLite vs Supabase, migracija progress."""
    r = _admin_only()
    if r: return r

    from config import DB_FILE, PORTAL_DB_FILE, AUDIT_DB_FILE

    # Env flag-ovi
    flags = {
        "USE_SUPABASE_AUTH": os.environ.get("USE_SUPABASE_AUTH", "false"),
        "USE_SUPABASE_DB": os.environ.get("USE_SUPABASE_DB", "false"),
        "USE_SUPABASE_STORAGE": os.environ.get("USE_SUPABASE_STORAGE", "false"),
        "DUAL_WRITE_MODE": os.environ.get("DUAL_WRITE_MODE", "false"),
        "DB_BACKEND": os.environ.get("DB_BACKEND", "rest"),
    }

    # SQLite brojevi
    sqlite_counts = {}
    for label, path in (("crm", DB_FILE), ("portal", PORTAL_DB_FILE), ("audit", AUDIT_DB_FILE)):
        if os.path.exists(path):
            try:
                conn = sqlite3.connect(path, timeout=5.0)
                for (tname,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall():
                    try:
                        cnt = conn.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
                        sqlite_counts[f"{label}.{tname}"] = int(cnt)
                    except Exception:
                        pass
                conn.close()
            except Exception as e:
                sqlite_counts[f"{label}.error"] = str(e)

    # Supabase brojevi (samo ako je kljuc podesen)
    supabase_counts = {}
    supabase_ok = False
    supabase_error = None
    try:
        from data_layer import get_backend, count as db_count
        backend = get_backend()
        supabase_ok = True
        for t in ("partners", "products", "deals", "kyc_submissions",
                  "portal_products", "audit_logs"):
            try:
                supabase_counts[t] = db_count(t)
            except Exception as e:
                supabase_counts[t] = f"error: {e.__class__.__name__}"
    except Exception as e:
        supabase_error = f"{e.__class__.__name__}: {e}"

    # Auth status
    auth_ok = False
    auth_users = None
    try:
        from auth_supabase import admin_client, use_supabase_auth
        if use_supabase_auth():
            client = admin_client()
            resp = client.auth.admin.list_users(page=1, per_page=1)
            auth_ok = True
            auth_users = "reachable"
    except Exception as e:
        auth_users = f"error: {e.__class__.__name__}"

    return jsonify({
        "flags": flags,
        "supabase": {
            "connection_ok": supabase_ok,
            "backend": os.environ.get("DB_BACKEND", "rest") if supabase_ok else None,
            "error": supabase_error,
            "counts": supabase_counts,
            "auth_ok": auth_ok,
            "auth_users": auth_users,
        },
        "sqlite_counts": sqlite_counts,
        "migration": dict(_migration_state),
    })


@supabase_admin_bp.route('/api/supabase/dry-run', methods=['POST'])
@login_required
def supabase_dry_run():
    """Pokreće dry-run migracije — samo broji, ne piše. Sinhrono jer je brz."""
    r = _admin_only()
    if r: return r

    import io
    import contextlib

    buf = io.StringIO()
    try:
        script = Path(__file__).resolve().parent.parent / "scripts" / "migrate_data_to_supabase.py"
        result = subprocess.run(
            [sys.executable, str(script), "--dry-run"],
            capture_output=True, text=True, timeout=120,
        )
        output = (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Dry-run timed out (>120s)."}), 500
    except Exception as e:
        return jsonify({"error": f"{e.__class__.__name__}: {e}"}), 500

    log_audit('EDIT', 'system', 'Supabase migration dry-run executed',
              is_suspicious=False)
    return jsonify({"status": "ok", "output": output[-8000:]})


def _run_migration_thread(tables=None):
    """Pozadinski thread — poziva migration script i puni _migration_state."""
    global _migration_state
    with _migration_lock:
        _migration_state.update({
            "running": True,
            "started_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            "finished_at": None,
            "log": [],
            "tables": {},
            "error": None,
        })

    script = Path(__file__).resolve().parent.parent / "scripts" / "migrate_data_to_supabase.py"
    cmd = [sys.executable, str(script), "--confirm"]
    if tables:
        cmd += ["--tables", ",".join(tables)]

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        for line in iter(proc.stdout.readline, ''):
            with _migration_lock:
                _migration_state["log"].append(line.rstrip("\n"))
                # keep last 500 lines to bound memory
                if len(_migration_state["log"]) > 500:
                    _migration_state["log"] = _migration_state["log"][-500:]
        proc.wait()
        with _migration_lock:
            _migration_state["running"] = False
            _migration_state["finished_at"] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            if proc.returncode != 0:
                _migration_state["error"] = f"Exit code {proc.returncode}"
    except Exception as e:
        with _migration_lock:
            _migration_state["running"] = False
            _migration_state["finished_at"] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            _migration_state["error"] = f"{e.__class__.__name__}: {e}"

    log_audit('EDIT', 'system',
              f'Supabase migration finished (error={_migration_state.get("error")})',
              is_suspicious=False)


@supabase_admin_bp.route('/api/supabase/migrate', methods=['POST'])
@login_required
def supabase_migrate():
    """Pokreće pravu migraciju u background thread-u. Frontend polluje /status."""
    r = _admin_only()
    if r: return r

    if _migration_state["running"]:
        return jsonify({"error": "Migration already running."}), 409

    body = request.get_json(silent=True) or {}
    tables = body.get("tables")

    t = threading.Thread(target=_run_migration_thread, kwargs={"tables": tables}, daemon=True)
    t.start()
    log_audit('EDIT', 'system',
              f'Supabase migration started (tables={tables or "all"})',
              is_suspicious=False)
    return jsonify({"status": "started", "message": "Migration running in background."})


@supabase_admin_bp.route('/api/supabase/set-flag', methods=['POST'])
@login_required
def supabase_set_flag():
    """Sets a feature flag u trenutnom procesu (RAM). Za trajno menjanje treba
    ručno editovati .env. Ovo je za brzo prebacivanje (rollback).

    Body: {"flag": "USE_SUPABASE_DB", "value": "true|false"}"""
    r = _admin_only()
    if r: return r

    body = request.get_json(silent=True) or {}
    flag = str(body.get("flag", "")).strip().upper()
    value = str(body.get("value", "")).strip().lower()

    allowed = {"USE_SUPABASE_AUTH", "USE_SUPABASE_DB",
               "USE_SUPABASE_STORAGE", "DUAL_WRITE_MODE"}
    if flag not in allowed:
        return jsonify({"error": f"Flag '{flag}' not allowed."}), 400
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        return jsonify({"error": "Value must be true/false."}), 400

    old = os.environ.get(flag, "false")
    os.environ[flag] = value
    log_audit('EDIT', 'system',
              f'Supabase flag {flag}: {old} → {value} (in-process only)',
              is_suspicious=True)  # bezbednosno relevantno

    # Data layer keš — bustuj da novi backend uzme
    try:
        from data_layer import reset as _reset_layer
        _reset_layer()
    except Exception:
        pass

    return jsonify({
        "status": "ok",
        "flag": flag,
        "old": old,
        "new": value,
        "note": "In-process only. For permanent change, edit .env and Reload.",
    })
