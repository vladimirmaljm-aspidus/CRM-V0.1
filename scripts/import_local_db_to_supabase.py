#!/usr/bin/env python3
"""
V23.1 #1 — SAFE LOCAL .db → SUPABASE IMPORT

Uvod: Render brise interne podatke (ephemeral). Prije prelaska na potpuno
Supabase-only rezim, moramo lokalne SQLite baze (PythonAnywhere ili exportovane
sa Render-a pre reset-a) sacuvati u Supabase.

Upotreba:
    python scripts/import_local_db_to_supabase.py --db path/to/aspidus_crm.db --dry-run
    python scripts/import_local_db_to_supabase.py --db aspidus_crm.db --confirm

Sta radi:
  1. Cita sve tabele iz SQLite (users, partners, products, deals, offers, invoices,
     proformas, demands, transactions, accounts, offer_versions, document_register,
     document_revisions, entity_notes, deal_documents, custom_reports, user_tasks,
     saved_filters, itd)
  2. Za svaku tabelu poziva Supabase REST upsert (id, data ILI ceo row).
     UPSERT znaci: postojeci redovi se prepisuju, novi se dodaju. Bez duplikata.
  3. Loguje uspehe/greske u out/import_YYYYMMDD.log.

Bezbednosne mere:
  * Prvo `--dry-run` — samo prebroji sta bi bilo poslato, bez pisanja.
  * `--confirm` obavezan za stvarni upload.
  * Rate limit: 100 rows / batch, 200ms pauza.
  * Ako Supabase vrati 4xx, upis se OBUSTAVLJA i log pokazuje bad row.
  * Backup Supabase-a se PRE toga preporucuje kroz Admin Health > Backup Now.
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('import')


# Tabele u SQLite koje mapiramo 1:1 na Supabase.
# Format: sqlite_name -> supabase_name (isto ako nema promene).
TABLE_MAP = {
    'users': 'users',
    'partners': 'partners',
    'products': 'products',
    'deals': 'deals',
    'demands': 'demands',
    'accounts': 'accounts',
    'transactions': 'transactions',
    'recurringExpenses': 'recurring_expenses',
    'offers': 'offers',
    'invoices': 'invoices',
    'proformas': 'proformas',
    'shared_documents': 'shared_documents',
    'connections': 'connections',
    'document_register': 'document_register',
    'document_revisions': 'document_revisions',
    'offer_versions': 'offer_versions',
    'entity_notes': 'entity_notes',
    'deal_documents': 'deal_documents',
    'custom_reports': 'custom_reports',
    'user_tasks': 'user_tasks',
    'saved_filters': 'saved_filters',
    'user_sessions': 'user_sessions',
    'known_ips': 'known_ips',
    'file_text': 'file_text',
    'settings': 'settings',
    'partner_inventory': 'partner_inventory',
    'inventory_movements': 'inventory_movements',
}


def _table_exists(conn, name):
    r = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(r)


def _rows_of(conn, table):
    """Ucitaj sve redove iz SQLite tabele. Vrati listu dict-ova."""
    cursor = conn.execute(f'SELECT * FROM "{table}"')
    cols = [d[0] for d in cursor.description]
    rows = []
    for r in cursor.fetchall():
        d = {}
        for i, c in enumerate(cols):
            v = r[i]
            # SQLite tekstualni JSON -> Python dict (Supabase JSONB polja)
            if isinstance(v, str) and c in ('data', 'permissions', 'notif_prefs',
                                             'snapshot', 'changedFields', 'filter_json'):
                try:
                    v = json.loads(v)
                except Exception:
                    pass
            d[c] = v
        rows.append(d)
    return rows


def _push_batch(supabase, table, batch):
    """Salje batch na Supabase preko postgrest upsert."""
    resp = supabase.table(table).upsert(batch).execute()
    return resp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True, help='Putanja do SQLite .db fajla')
    ap.add_argument('--tables', help='Zarez razdvojene tabele (samo ove)')
    ap.add_argument('--exclude', help='Zarez razdvojene tabele da preskocim')
    ap.add_argument('--dry-run', action='store_true', help='Samo prebroji, ne salji')
    ap.add_argument('--confirm', action='store_true', help='Stvarno napravi upload')
    ap.add_argument('--batch-size', type=int, default=100)
    ap.add_argument('--sleep-ms', type=int, default=200, help='Pauza izmedju batch-eva')
    args = ap.parse_args()

    if not args.dry_run and not args.confirm:
        log.error('Moras dati --dry-run ili --confirm. Prekid.')
        sys.exit(1)

    if not os.path.exists(args.db):
        log.error(f'Ne postoji fajl: {args.db}')
        sys.exit(1)

    only = set((args.tables or '').split(',')) if args.tables else None
    exclude = set((args.exclude or '').split(',')) if args.exclude else set()

    # Ucitaj Supabase klijenta (isti kao data_layer koristi)
    if not args.dry_run:
        try:
            from data_layer import get_supabase_client
            supabase = get_supabase_client()
        except Exception as e:
            log.error(f'Supabase klijent nije dostupan: {e}')
            log.error('Postavi SUPABASE_URL i SUPABASE_SERVICE_KEY u .env, pa pokreni ponovo.')
            sys.exit(1)
    else:
        supabase = None

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = os.path.join(_ROOT, 'out', f'import_{ts}.log')
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    fh = logging.FileHandler(log_path)
    fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    log.addHandler(fh)

    log.info(f'==== Import from {args.db} → Supabase (dry={args.dry_run}) ====')

    conn = sqlite3.connect(args.db)
    total_pushed = 0
    total_errors = 0
    summary = {}

    for sqlite_name, supabase_name in TABLE_MAP.items():
        if only and sqlite_name not in only:
            continue
        if sqlite_name in exclude:
            continue
        if not _table_exists(conn, sqlite_name):
            log.info(f'  SKIP {sqlite_name}: tabela ne postoji u ovom .db')
            continue
        rows = _rows_of(conn, sqlite_name)
        if not rows:
            log.info(f'  {sqlite_name}: 0 redova (prazna)')
            summary[sqlite_name] = 0
            continue
        log.info(f'  {sqlite_name} → {supabase_name}: {len(rows)} redova')
        summary[sqlite_name] = len(rows)

        if args.dry_run:
            total_pushed += len(rows)
            continue

        # batch upload
        for i in range(0, len(rows), args.batch_size):
            batch = rows[i:i+args.batch_size]
            try:
                _push_batch(supabase, supabase_name, batch)
                total_pushed += len(batch)
                log.info(f'    batch {i//args.batch_size+1}: {len(batch)} uploaded (running total {total_pushed})')
            except Exception as e:
                total_errors += 1
                log.error(f'    batch failed at row {i}: {e}')
                # zadrzi prvi bad row u log
                log.error(f'    first row of failed batch: {json.dumps(batch[0], default=str)[:500]}')
                # PREKID — bezbedno je stati odmah nego naprosto slati dalje
                log.error('Import PREKINUT zbog greske. Popravite pa pokrenite ponovo (upsert je idempotent).')
                sys.exit(2)
            time.sleep(args.sleep_ms / 1000.0)

    conn.close()

    log.info('=' * 60)
    log.info('SUMMARY:')
    for t, n in summary.items():
        log.info(f'  {t}: {n}')
    log.info(f'Total rows {"would be" if args.dry_run else ""} pushed: {total_pushed}')
    log.info(f'Errors: {total_errors}')
    log.info(f'Log fajl: {log_path}')
    log.info('=' * 60)


if __name__ == '__main__':
    main()
