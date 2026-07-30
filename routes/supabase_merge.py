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


# Tabele koje uvek postoje u Supabase (posle supabase_v23_1.sql migracije).
# `bools`: kolone koje su BOOLEAN u Postgresu — moramo INTEGER 0/1 → bool coerce.
# `cols`:  bela lista kolona koje smemo poslati; sve ostalo NE ide u Supabase
#          nego se pakuje u `data` JSONB (koji je uvek prisutan). Ovim resavamo
#          PGRST204 "Could not find column" bez potrebe da menjamo transform.
# `id_key`: kljuc iz SQLite reda koji se mapira na Supabase PK (default 'id';
#           za audit_logs mapiramo na 'sync_id' jer im je PK BIGSERIAL).
SUPPORTED_TABLES = {
    'partners':           {'bools': ['is_portal_active', 'is_premium', 'kyc_approved', 'can_login'],
                           'cols':  ['id', 'auth_user_id', 'email', 'company_name', 'phone',
                                     'contact_person', 'country', 'city', 'street', 'tax_id',
                                     'portal_token', 'is_portal_active', 'portal_level',
                                     'is_premium', 'kyc_approved', 'can_login', 'data']},
    'products':           {'bools': [],
                           'cols': ['id', 'name', 'sku', 'hs_code', 'unit', 'supplier_id', 'data']},
    'deals':              {'bools': [],
                           'cols': ['id', 'buyer_id', 'source_offer_id', 'supplier_id',
                                    'product_id', 'status', 'total_amount', 'currency', 'data']},
    'demands':            {'bools': [], 'cols': ['id', 'buyer_id', 'data']},
    'offers':             {'bools': [], 'cols': ['id', 'offer_no', 'customer_id', 'data']},
    'invoices':           {'bools': [], 'cols': ['id', 'data']},
    'proformas':          {'bools': [], 'cols': ['id', 'data']},
    'shared_documents':   {'bools': [],
                           'cols': ['id', 'partner_id', 'title', 'category',
                                    'storage_bucket', 'storage_path', 'data']},
    'document_register':  {'bools': [],
                           'cols': ['id', 'doc_number', 'doc_type', 'year', 'seq',
                                    'entity_id', 'revision', 'issued_at', 'issued_by']},
    'document_revisions': {'bools': [],
                           'cols': ['id', 'doc_number', 'revision', 'entity_id',
                                    'snapshot', 'content_hash', 'binding_hash',
                                    'change_reason', 'changed_by', 'changed_at']},
    'offer_versions':     {'bools': [],
                           'cols': ['id', 'offer_id', 'version', 'snapshot',
                                    'changed_fields', 'change_reason',
                                    'changed_by', 'changed_by_role', 'changed_at', 'origin']},
    'entity_notes':       {'bools': ['pinned'],
                           'cols': ['id', 'entity_type', 'entity_id', 'body',
                                    'created_by', 'created_at', 'pinned', 'data']},
    'deal_documents':     {'bools': [],
                           'cols': ['id', 'deal_id', 'file_url', 'filename', 'doc_kind',
                                    'size_bytes', 'uploaded_by', 'uploaded_at', 'note', 'data']},
    'custom_reports':     {'bools': ['is_shared'],
                           'cols': ['id', 'owner_user_id', 'title', 'description',
                                    'sql_query', 'chart_type', 'is_shared',
                                    'created_at', 'updated_at']},
    'user_tasks':         {'bools': [],
                           'cols': ['id', 'owner_user_id', 'title', 'description',
                                    'due_at', 'priority', 'status', 'linked_entity_type',
                                    'linked_entity_id', 'created_at', 'completed_at']},
    'saved_filters':      {'bools': ['is_shared'],
                           'cols': ['id', 'owner_user_id', 'name', 'entity_type',
                                    'filter_json', 'is_shared', 'created_at', 'updated_at']},
    'kyc_submissions':    {'bools': [], 'cols': ['id', 'partner_id', 'status', 'data']},
    'portal_products':    {'bools': [], 'cols': ['id', 'partner_id', 'status', 'data']},
    'audit_logs':         {'bools': ['is_suspicious'],
                           'cols': ['sync_id', 'user_id', 'username', 'action', 'module',
                                    'details', 'timestamp', 'is_suspicious', 'ip_address',
                                    'user_agent', 'location', 'data'],
                           'id_key': 'sync_id',   # nase 'id' iz SQLite → sync_id kolone
                           'from_id': 'id'},
    'known_ips':          {'bools': [],
                           'cols': ['id', 'user_id', 'ip', 'country', 'city',
                                    'first_seen', 'last_seen', 'login_count']},
    'user_sessions':      {'bools': ['revoked'],
                           'cols': ['id', 'user_id', 'created_at', 'last_seen_at',
                                    'ip', 'country', 'user_agent', 'ua_family',
                                    'device_label', 'revoked', 'revoked_at', 'revoked_reason']},
    'settings':           {'bools': [], 'cols': ['key', 'value'], 'id_key': 'key'},
    'magic_link_used_jti': {'bools': [],
                            'cols': ['jti', 'token', 'used_at', 'client_ip'],
                            'id_key': 'jti'},
    'magic_login_tokens':  {'bools': [],
                            'cols': ['token', 'user_id', 'purpose', 'created_at',
                                     'expires_at', 'request_ip', 'used_at'],
                            'id_key': 'token'},
    'partner_inventory':  {'bools': [],
                           'cols': ['id', 'partner_id', 'product_id', 'qty_on_hand',
                                    'qty_reserved', 'unit', 'last_movement_at']},
    'inventory_movements':{'bools': [],
                           'cols': ['id', 'partner_id', 'product_id', 'kind', 'qty',
                                    'unit', 'deal_id', 'note', 'created_at', 'created_by']},
    'custom_field_defs':  {'bools': ['required', 'is_active'],
                           'cols': ['id', 'entity_type', 'field_key', 'field_label',
                                    'field_type', 'options_json', 'required',
                                    'display_order', 'is_active', 'created_at']},
    'api_keys':           {'bools': ['revoked'],
                           'cols': ['id', 'name', 'key_hash', 'key_prefix', 'owner_user_id',
                                    'scope', 'rate_limit_per_min', 'created_at',
                                    'last_used_at', 'revoked', 'revoked_at']},
    'outbound_webhooks':  {'bools': ['is_active'],
                           'cols': ['id', 'name', 'target_url', 'events', 'secret',
                                    'is_active', 'created_at', 'created_by',
                                    'last_fired_at', 'last_status', 'fail_count']},
    'users':              {'bools': ['must_change_password', 'totp_enabled'],
                           'cols': ['id', 'username', 'password', 'role', 'full_name', 'email',
                                    'phone', 'notif_prefs', 'permissions',
                                    'must_change_password', 'locked_until',
                                    'password_expires_at', 'signature',
                                    'totp_secret', 'totp_enabled', 'totp_recovery',
                                    'token_version', 'last_password_change_at',
                                    'last_login_country', 'data']},
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
    """Prilagodi jedan row za Supabase upsert:

    1. Salje SAMO whitelisted kolone (`table_info['cols']`) — sve ostalo se
       pakuje u `data` JSONB da PGRST204 "Could not find column" nestane.
    2. JSON string → dict za JSONB kolone.
    3. INTEGER 0/1 → bool za kolone iz `bools`.
    4. Prazan string → None za NOT NULL koje bi pukle na 23502.
    5. Poseban id-mapping ako je `id_key`/`from_id` definisan (npr. audit_logs).
    """
    bools = set(table_info.get('bools') or [])
    allowed_cols = set(table_info.get('cols') or [])
    id_key = table_info.get('id_key', 'id')
    from_id = table_info.get('from_id', 'id')

    # Kolone koje su JSONB u Supabase (znamo iz supabase_v23_1.sql)
    jsonb_cols = {'data', 'permissions', 'notif_prefs', 'snapshot',
                  'filter_json', 'options_json'}

    out = {}
    extra_data = {}  # nepoznate kolone koje pakujemo u 'data'

    for k, v in row.items():
        # Mapiraj id ako je remapiran (npr. audit_logs.id → sync_id)
        target_key = k
        if from_id != id_key and k == from_id:
            target_key = id_key

        # Coerce bool
        if target_key in bools:
            v = bool(v) if v is not None else False
        # Coerce JSONB stringify
        elif target_key in jsonb_cols and isinstance(v, str):
            try: v = json.loads(v)
            except Exception: pass
        # Prazan string → None (helps NOT NULL sa DEFAULT)
        elif isinstance(v, str) and v == '':
            v = None
        elif isinstance(v, str) and re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}', v):
            v = _iso_normalize(v)

        if target_key in allowed_cols:
            out[target_key] = v
        elif allowed_cols:
            # Nije u whitelist-u — sacuvaj u 'data' JSONB ako ta kolona postoji
            if 'data' in allowed_cols and v is not None:
                extra_data[k] = v
        else:
            # Bez whitelist-a: propusti sve (backward-compat)
            out[target_key] = v

    if extra_data and 'data' in allowed_cols:
        existing = out.get('data')
        if isinstance(existing, dict):
            # Merge — direktne kolone imaju prioritet nad 'data' JSONB
            merged = dict(extra_data)
            merged.update(existing)
            out['data'] = merged
        else:
            out['data'] = extra_data

    return out


# =========================================================================
#  V23.1C — LIVE MIRROR helper (za sve save endpoint-e u aplikaciji)
# =========================================================================
# Zove ga se BEST-EFFORT posle svakog SQLite upisa da isti red završi i u
# Supabase (Render brise SQLite pri deploy-u — bez mirror-a se novi klijenti,
# ponude i deals GUBE). Ne baca izuzetak nikad — samo loguje warning.

def mirror_to_supabase(table: str, row: dict) -> bool:
    """Upsert jednog reda u Supabase koristeci vec-postojeci `_coerce_row`
    whitelist. Vraca True ako je uspesno, False u suprotnom (pa i tada
    aplikacija nastavlja normalno — SQLite je već zapisao).

    Poziva se iz routes/data.py `save_single_item` posle conn.commit()."""
    if not row or not isinstance(row, dict):
        return False
    info = SUPPORTED_TABLES.get(table)
    if not info:
        # Nema mapiranja — ne mirror-uj (npr. auth tabele ne idu u Supabase kroz ovo)
        return False
    try:
        coerced = _coerce_row(row, info)
        # ID mora postojati
        id_key = info.get('id_key', 'id')
        if coerced.get(id_key) is None:
            return False
        from data_layer import upsert as _db_upsert
        _db_upsert(table, coerced, on_conflict=id_key)
        return True
    except Exception as e:
        # Log-only. Ne rusimo save.
        import logging
        logging.getLogger(__name__).info(
            f'Supabase mirror skipped for {table}/{row.get("id","?")}: {type(e).__name__}: {str(e)[:200]}')
        return False


def _rehydrate_row(row: dict, table_info: dict) -> dict:
    """Rekonstruise SQLite-shape dict iz Supabase reda.

    Reverse od `_coerce_row`: uzima top-level whitelisted kolone i sve iz
    JSONB `data` polja, spaja ih u jedan dict koji izgleda kao da je nikad
    ni napustio SQLite. Direktne kolone imaju prioritet nad `data` (isto
    kao pri upisu). Ovo je kljucno za `fetch_from_supabase` read-fallback.
    """
    if not isinstance(row, dict):
        return {}
    base = {}
    data_blob = row.get('data')
    if isinstance(data_blob, dict):
        base.update(data_blob)
    elif isinstance(data_blob, str):
        try:
            parsed = json.loads(data_blob)
            if isinstance(parsed, dict):
                base.update(parsed)
        except Exception:
            pass
    for k, v in row.items():
        if k == 'data':
            continue
        if v is None:
            continue
        base[k] = v
    id_key = table_info.get('id_key', 'id') if isinstance(table_info, dict) else 'id'
    if id_key != 'id' and id_key in row and row.get(id_key) is not None:
        base.setdefault('id', row[id_key])
    return base


def fetch_from_supabase(table: str, limit: int = 5000) -> list:
    """Vraca listu SQLite-shape dictova iz Supabase za datu tabelu.

    Koristi se u `get_data` kao READ-FALLBACK kada je SQLite prazan (npr.
    posle Render deploy-a koji brise efemerni disk). Best-effort — vraca []
    ako Supabase nije dostupan ili tabela nije mapirana.
    """
    info = SUPPORTED_TABLES.get(table)
    if not info:
        return []
    try:
        from data_layer import select as _db_select
        rows = _db_select(table, limit=limit) or []
        return [_rehydrate_row(r, info) for r in rows if isinstance(r, dict)]
    except Exception as e:
        import logging
        logging.getLogger(__name__).info(
            f'Supabase read-fallback failed for {table}: {type(e).__name__}: {str(e)[:200]}')
        return []


def fetch_one_from_supabase(table: str, row_id: str) -> dict | None:
    """Isto kao fetch_from_supabase ali za jedan red po ID-u."""
    info = SUPPORTED_TABLES.get(table)
    if not info or not row_id:
        return None
    try:
        from data_layer import select_one as _db_select_one
        id_key = info.get('id_key', 'id')
        row = _db_select_one(table, {id_key: row_id})
        if not row:
            return None
        return _rehydrate_row(row, info)
    except Exception as e:
        import logging
        logging.getLogger(__name__).info(
            f'Supabase read-one fallback failed for {table}/{row_id}: {type(e).__name__}: {str(e)[:200]}')
        return None


def backfill_sqlite_from_supabase(table: str, conn, encrypt_fn=None) -> int:
    """Kad SQLite ostane bez podataka, restore-uje ga iz Supabase u istoj
    formi kakvu su get_data endpoint-i ocekivali (`{id, data}` gde je `data`
    JSON blob). Vraca broj upisanih redova. Idempotentno preko UPSERT-a.
    """
    rows = fetch_from_supabase(table)
    if not rows:
        return 0
    written = 0
    c = conn.cursor()
    for item in rows:
        try:
            item_id = item.get('id') or item.get('key')
            if not item_id:
                continue
            payload = encrypt_fn(item) if callable(encrypt_fn) else json.dumps(item)
            c.execute(f'INSERT OR REPLACE INTO "{table}" (id, data) VALUES (?, ?)',
                      (item_id, payload))
            written += 1
        except Exception:
            continue
    try:
        conn.commit()
    except Exception:
        pass
    return written


def mirror_delete_to_supabase(table: str, row_id: str) -> bool:
    """Isto ali za delete."""
    if not row_id:
        return False
    info = SUPPORTED_TABLES.get(table)
    if not info:
        return False
    try:
        from data_layer import delete as _db_delete
        id_key = info.get('id_key', 'id')
        _db_delete(table, {id_key: row_id})
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).info(
            f'Supabase delete-mirror skipped for {table}/{row_id}: {type(e).__name__}: {str(e)[:200]}')
        return False


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


@supabase_merge_bp.route('/api/admin/supabase/mirror-health', methods=['GET'])
@login_required
def mirror_health():
    """V23.1C: brzo dijagnostikuje da li LIVE MIRROR uopste radi.
    Vraca:
      - supabase_reachable: da li backend odgovara
      - mirror_test: probni upsert test row-a → čita ga nazad → briše
      - error_details: puna poruka ako nešto padne
    Admin otvori /admin/supabase/merge (nova sekcija) i vidi da li novi
    partneri ID mogu da završe u Supabase-u odmah pri save-u."""
    err = _admin_only()
    if err: return err

    import uuid as _uuid
    test_id = 'mirror-test-' + _uuid.uuid4().hex[:12]
    result = {
        'supabase_reachable': False,
        'mirror_test_ok': False,
        'reachability_error': None,
        'mirror_error': None,
        'read_back_ok': False,
    }

    # 1. Reachability
    try:
        from data_layer import get_backend, select as _db_select
        _ = get_backend()
        _db_select('partners', limit=1)
        result['supabase_reachable'] = True
    except Exception as e:
        result['reachability_error'] = f'{type(e).__name__}: {str(e)[:250]}'
        return jsonify(result)

    # 2. Test upsert into a low-risk table (settings) with test key
    try:
        from data_layer import upsert as _db_upsert, select as _db_select, delete as _db_delete
        _db_upsert('settings', {'key': test_id, 'value': 'mirror-health-check'}, on_conflict='key')
        rows = _db_select('settings', filters={'key': test_id})
        if rows:
            result['read_back_ok'] = True
        _db_delete('settings', {'key': test_id})
        result['mirror_test_ok'] = True
    except Exception as e:
        result['mirror_error'] = f'{type(e).__name__}: {str(e)[:250]}'

    return jsonify(result)


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
