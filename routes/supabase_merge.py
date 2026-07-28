"""
V23.1 — SUPABASE MERGE WIZARD
==============================
Admin-only UI + endpointi za bezbedno prebacivanje lokalnih SQLite podataka u
Supabase, sa preview-om i per-row error reportom.

Zamenjuje potrebu za pokretanjem migrate_data_to_supabase.py kroz shell
(koji je bio krhak i tesko dijagnostikovan iz UI).

Endpointi:
  GET  /admin/supabase/merge                  — UI stranica
  GET  /api/admin/supabase/merge/status       — lokalni broj + Supabase broj po tabeli
  GET  /api/admin/supabase/merge/preview/<t>  — prvih N redova + transform preview
  POST /api/admin/supabase/merge/push/<t>     — push jedne tabele, per-row error report
  POST /api/admin/supabase/merge/push-all     — push svih tabela (opciono)

Kljucne razlike vs migrate_data_to_supabase.py:
  * Ne zaustavlja se na prvoj gresci — svaka row-a nezavisno, error se ponisti u JSON.
  * Ako target tabela ne postoji u Supabase, vraca jasnu poruku "run schemas/supabase_schema.sql".
  * JSONB / TEXT konverzija je defanzivna (json.loads od string → dict).
  * Booleani se automatski coerce-uju iz 0/1.
  * ISO timestamp normalizacija za Postgres.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request, session, render_template

from config import DB_FILE
from utils import login_required, log_audit

supabase_merge_bp = Blueprint('supabase_merge_bp', __name__)


# Tabele koje uvek postoje u Supabase (osnovni skup iz schemas/supabase_schema.sql).
# Value je lista "known bool columns" za koje moramo INTEGER→bool coerce.
SUPPORTED_TABLES = {
    'partners':           {'bools': ['is_portal_active', 'is_premium', 'kyc_approved', 'can_login']},
    'products':           {'bools': []},
    'deals':              {'bools': []},
    'demands':            {'bools': []},
    'offers':             {'bools': []},
    'invoices':           {'bools': []},
    'proformas':          {'bools': []},
    'shared_documents':   {'bools': []},
    'document_register':  {'bools': []},
    'document_revisions': {'bools': []},
    'offer_versions':     {'bools': []},
    'entity_notes':       {'bools': ['pinned']},
    'deal_documents':     {'bools': []},
    'custom_reports':     {'bools': ['is_shared']},
    'user_tasks':         {'bools': []},
    'saved_filters':      {'bools': ['is_shared']},
    'kyc_submissions':    {'bools': []},
    'portal_products':    {'bools': []},
    'audit_logs':         {'bools': ['is_suspicious']},
    'known_ips':          {'bools': []},
    'user_sessions':      {'bools': ['revoked']},
    'settings':           {'bools': []},
    'partner_inventory':  {'bools': []},
    'inventory_movements':{'bools': []},
    'custom_field_defs':  {'bools': ['required', 'is_active']},
    'api_keys':           {'bools': ['revoked']},
    'outbound_webhooks':  {'bools': ['is_active']},
}


def _admin_only():
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    return None


def _sqlite_table_exists(conn, name):
    r = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(r)


def _sqlite_count(conn, table):
    try:
        return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    except Exception:
        return -1


def _iso_normalize(v):
    """Postgres timestamp ne voli 'Z' varijantu bez timezone — sada je OK ako je ISO."""
    if not v or not isinstance(v, str):
        return v
    # Ako izgleda kao "2026-07-28T18:00:00Z" — ostavi ('Z' je legit UTC oznaka)
    return v


def _coerce_row(row, table_info):
    """Prilagodi jedan row: JSON string → dict, 0/1 → bool na bool kolonama."""
    bools = set(table_info.get('bools') or [])
    out = {}
    for k, v in row.items():
        if k in bools:
            out[k] = bool(v) if v is not None else False
        elif isinstance(v, str) and k in ('data', 'permissions', 'notif_prefs',
                                          'snapshot', 'changedFields', 'filter_json',
                                          'options_json'):
            try:
                out[k] = json.loads(v)
            except Exception:
                out[k] = v
        elif isinstance(v, str) and re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}', v):
            out[k] = _iso_normalize(v)
        else:
            out[k] = v
    return out


@supabase_merge_bp.route('/admin/supabase/merge', methods=['GET'])
@login_required
def merge_page():
    if session.get('role') != 'admin':
        return "Admin only.", 403
    return render_template('admin_supabase_merge.html')


@supabase_merge_bp.route('/api/admin/supabase/merge/status', methods=['GET'])
@login_required
def merge_status():
    err = _admin_only()
    if err: return err

    supabase_ok = False
    supabase_error = None
    supabase_counts = {}
    try:
        from data_layer import get_backend, count as db_count
        _ = get_backend()
        supabase_ok = True
    except Exception as e:
        supabase_error = f"{type(e).__name__}: {e}"

    # SQLite counts (from all three DBs)
    from config import DB_FILE as _DB, PORTAL_DB_FILE, AUDIT_DB_FILE
    dbs = {'crm': _DB, 'portal': PORTAL_DB_FILE, 'audit': AUDIT_DB_FILE}
    local_counts = {}
    for label, path in dbs.items():
        try:
            with sqlite3.connect(path, timeout=5.0) as conn:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                for (tname,) in rows:
                    if tname in SUPPORTED_TABLES:
                        try:
                            n = conn.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
                        except Exception:
                            n = -1
                        local_counts[tname] = {'db': label, 'count': int(n)}
        except Exception:
            pass

    # Supabase counts (only if backend ok)
    if supabase_ok:
        for t in SUPPORTED_TABLES.keys():
            try:
                supabase_counts[t] = int(db_count(t))
            except Exception as e:
                supabase_counts[t] = -1  # table missing or error

    # Compose per-table view
    tables = []
    for t in sorted(SUPPORTED_TABLES.keys()):
        loc = local_counts.get(t) or {'db': '?', 'count': 0}
        sup = supabase_counts.get(t, -1)
        diff = None
        if loc['count'] >= 0 and sup >= 0:
            diff = loc['count'] - sup
        tables.append({
            'name': t,
            'local_db': loc['db'],
            'local_count': loc['count'],
            'supabase_count': sup,
            'diff': diff,
            'supabase_exists': sup != -1,
        })

    return jsonify({
        'supabase_ok': supabase_ok,
        'supabase_error': supabase_error,
        'tables': tables,
    })


def _open_local_for(table):
    """Otvara tacan .db u kome se tabela nalazi."""
    from config import DB_FILE as _DB, PORTAL_DB_FILE, AUDIT_DB_FILE
    for path in (_DB, PORTAL_DB_FILE, AUDIT_DB_FILE):
        try:
            conn = sqlite3.connect(path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            if _sqlite_table_exists(conn, table):
                return conn
            conn.close()
        except Exception:
            continue
    return None


@supabase_merge_bp.route('/api/admin/supabase/merge/preview/<table>', methods=['GET'])
@login_required
def merge_preview(table):
    err = _admin_only()
    if err: return err
    if table not in SUPPORTED_TABLES:
        return jsonify({'error': 'table_not_supported'}), 400
    limit = min(int(request.args.get('limit', 5)), 20)

    conn = _open_local_for(table)
    if not conn:
        return jsonify({'error': 'table_not_found_locally'}), 404
    try:
        cursor = conn.execute(f'SELECT * FROM "{table}" LIMIT ?', (limit,))
        cols = [d[0] for d in cursor.description]
        rows = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    coerced = [_coerce_row(r, SUPPORTED_TABLES[table]) for r in rows]
    return jsonify({
        'table': table,
        'columns': cols,
        'sample_raw': rows,
        'sample_coerced': coerced,
        'note': 'coerced = kako će red izgledati poslat u Supabase',
    })


@supabase_merge_bp.route('/api/admin/supabase/merge/push/<table>', methods=['POST'])
@login_required
def merge_push_table(table):
    err = _admin_only()
    if err: return err
    if table not in SUPPORTED_TABLES:
        return jsonify({'error': 'table_not_supported'}), 400

    body = request.get_json(silent=True) or {}
    dry_run = bool(body.get('dry_run', False))
    limit = body.get('limit')
    if limit is not None:
        try: limit = int(limit)
        except Exception: limit = None

    conn = _open_local_for(table)
    if not conn:
        return jsonify({'error': 'table_not_found_locally'}), 404

    try:
        # Load all rows (safe — SQLite streams, but we cap at 10k per call)
        max_rows = 10000
        cursor = conn.execute(f'SELECT * FROM "{table}" LIMIT ?', (max_rows,))
        rows = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    if limit:
        rows = rows[:limit]

    if not rows:
        return jsonify({'table': table, 'processed': 0, 'ok': 0, 'errors': [], 'skipped': True})

    if dry_run:
        # Just show what would be pushed
        return jsonify({
            'table': table,
            'processed': len(rows),
            'ok': 0,
            'dry_run': True,
            'first_row_coerced': _coerce_row(rows[0], SUPPORTED_TABLES[table]),
        })

    try:
        from data_layer import upsert as db_upsert
    except Exception as e:
        return jsonify({'error': f'data_layer_import_failed: {e}'}), 500

    ok = 0
    errors = []
    for idx, raw in enumerate(rows):
        try:
            coerced = _coerce_row(raw, SUPPORTED_TABLES[table])
            if coerced.get('id') is None:
                errors.append({'idx': idx, 'reason': 'missing_id', 'row_preview': str(raw)[:120]})
                continue
            db_upsert(table, coerced, on_conflict='id')
            ok += 1
        except Exception as e:
            errors.append({
                'idx': idx,
                'id': raw.get('id'),
                'reason': f'{type(e).__name__}: {str(e)[:250]}',
            })
            # Stop early if we have many errors — likely schema mismatch
            if len(errors) >= 20:
                errors.append({'idx': -1, 'reason': 'stopped_after_20_errors — proveri Supabase schemu za ovu tabelu'})
                break

    log_audit('EDIT', 'supabase_merge',
              f'Pushed {ok}/{len(rows)} rows to {table} ({len(errors)} errors)')

    return jsonify({
        'table': table,
        'processed': len(rows),
        'ok': ok,
        'errors': errors[:30],
        'error_count': len(errors),
    })


@supabase_merge_bp.route('/api/admin/supabase/merge/push-all', methods=['POST'])
@login_required
def merge_push_all():
    """Pushuje SVE tabele iz SUPPORTED_TABLES po redu. Vraca per-tabelu report."""
    err = _admin_only()
    if err: return err

    try:
        from data_layer import upsert as db_upsert
    except Exception as e:
        return jsonify({'error': f'data_layer_import_failed: {e}'}), 500

    report = {}
    for table in SUPPORTED_TABLES.keys():
        conn = _open_local_for(table)
        if not conn:
            report[table] = {'status': 'skipped_no_local'}
            continue
        try:
            cursor = conn.execute(f'SELECT * FROM "{table}" LIMIT 10000')
            rows = [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

        if not rows:
            report[table] = {'status': 'empty', 'ok': 0, 'errors': 0}
            continue

        ok = 0
        err_count = 0
        first_error = None
        for raw in rows:
            try:
                coerced = _coerce_row(raw, SUPPORTED_TABLES[table])
                if coerced.get('id') is None:
                    err_count += 1
                    continue
                db_upsert(table, coerced, on_conflict='id')
                ok += 1
            except Exception as e:
                err_count += 1
                if first_error is None:
                    first_error = f'{type(e).__name__}: {str(e)[:200]}'
                if err_count >= 20:
                    break
        report[table] = {
            'status': 'ok' if err_count == 0 else 'partial',
            'ok': ok, 'errors': err_count,
            'first_error': first_error,
        }

    log_audit('EDIT', 'supabase_merge', f'Push-all completed: {json.dumps(report)[:500]}',
              is_suspicious=False)
    return jsonify({'report': report})
