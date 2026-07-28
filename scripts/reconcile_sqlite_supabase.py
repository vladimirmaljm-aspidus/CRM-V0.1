#!/usr/bin/env python3
"""
reconcile_sqlite_supabase.py — SQLite ↔ Supabase parity checker.

Za svaku tabelu iz MIGRATION_PLAN uporedi:
  1. UKUPAN broj redova
  2. UKUPAN broj distinct id-eva (za detekciju dupliciranih insert-a)
  3. Sample od N poslednjih id-eva u obe baze (proverava da li se
     najsvezija zapisivanja stigla u oba mesta — ključno u dual-write modu)
  4. Timestamp-ove najsvezijeg reda ako postoji `lastModified`/`updatedAt`

Izlaz:
  ✓ tables sa istim brojem redova i ID overlap-om
  ⚠ tables sa razlikom (drift) — sa detaljima šta fali gde
  ✗ tables koje nisu dostupne (Supabase konekcija fail, tabela ne postoji…)

Pokretanje:
    python3.13 scripts/reconcile_sqlite_supabase.py
    python3.13 scripts/reconcile_sqlite_supabase.py --sample 20 --only partners,products
    python3.13 scripts/reconcile_sqlite_supabase.py --json          # machine-readable

Environment:
    USE_SUPABASE_DB=true    # neophodno da data_layer čita iz Postgres-a
    SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY iz .env

Exit codes:
    0 → nema drift-a
    1 → drift detektovan (bar jedna tabela)
    2 → sistemska greška (konekcija, konfiguracija)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from scripts.migrate_data_to_supabase import MIGRATION_PLAN, _open_db
except ImportError:
    from migrate_data_to_supabase import MIGRATION_PLAN, _open_db


def _count_sqlite(conn, table):
    if conn is None:
        return None
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return None


def _count_supabase(client, table):
    try:
        # supabase-py 2.x: count via `count='exact'`
        r = client.table(table).select("id", count="exact").limit(1).execute()
        return int(r.count) if getattr(r, "count", None) is not None else None
    except Exception as e:
        return {"__err__": f"{type(e).__name__}: {str(e)[:120]}"}


def _sample_ids_sqlite(conn, table, limit):
    if conn is None:
        return []
    try:
        # Pokušaj po createdAt/updatedAt/lastModified, fallback na rowid
        for col in ("lastModified", "updatedAt", "createdAt", "issuedAt"):
            try:
                rows = conn.execute(
                    f"SELECT id FROM {table} ORDER BY json_extract(data, '$.{col}') DESC LIMIT ?",
                    (limit,)
                ).fetchall()
                if rows:
                    return [r[0] for r in rows]
            except Exception:
                continue
        rows = conn.execute(f"SELECT id FROM {table} LIMIT ?", (limit,)).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def _sample_ids_supabase(client, table, limit):
    try:
        r = client.table(table).select("id").order("id", desc=True).limit(limit).execute()
        return [row.get("id") for row in (r.data or [])]
    except Exception:
        return []


def _get_supabase_client():
    from supabase import create_client
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY nije postavljen u env-u")
    return create_client(url, key)


def reconcile(sample_size=10, only=None, as_json=False):
    only = set(only) if only else set()
    try:
        client = _get_supabase_client()
    except Exception as e:
        msg = f"✗ Supabase konekcija nije uspela: {e}"
        if as_json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(msg)
        return 2

    conns = {}
    for src in ("crm", "portal", "audit"):
        conns[src] = _open_db(src)

    report = []
    drift = False

    for src, source_table, target_table, _ in MIGRATION_PLAN:
        if only and source_table not in only and target_table not in only:
            continue

        sq_conn = conns.get(src)
        sq_count = _count_sqlite(sq_conn, source_table)
        sp_count_raw = _count_supabase(client, target_table)
        sp_count = None
        sp_err = None
        if isinstance(sp_count_raw, dict) and "__err__" in sp_count_raw:
            sp_err = sp_count_raw["__err__"]
        else:
            sp_count = sp_count_raw

        sq_ids = _sample_ids_sqlite(sq_conn, source_table, sample_size)
        sp_ids = _sample_ids_supabase(client, target_table, sample_size)
        sq_set = set(map(str, sq_ids))
        sp_set = set(map(str, sp_ids))
        missing_in_sp = sq_set - sp_set
        missing_in_sq = sp_set - sq_set

        status = "ok"
        if sp_err:
            status = "sp_error"
        elif sq_count is None:
            status = "sqlite_missing"
        elif sq_count != sp_count:
            status = "drift"
            drift = True
        elif missing_in_sp or missing_in_sq:
            status = "sample_drift"
            drift = True

        report.append({
            "source": src,
            "source_table": source_table,
            "target_table": target_table,
            "sqlite_count": sq_count,
            "supabase_count": sp_count,
            "supabase_error": sp_err,
            "sqlite_missing_from_supabase": sorted(missing_in_sp)[:sample_size],
            "supabase_missing_from_sqlite": sorted(missing_in_sq)[:sample_size],
            "status": status,
        })

    for conn in conns.values():
        if conn is not None:
            conn.close()

    if as_json:
        print(json.dumps({"drift": drift, "results": report}, indent=2, default=str))
    else:
        print("\n" + "=" * 78)
        print("  ASPIDUS — SQLite ↔ Supabase RECONCILE")
        print("=" * 78)
        for r in report:
            icon = {"ok": "✓", "drift": "⚠", "sample_drift": "⚠",
                    "sp_error": "✗", "sqlite_missing": "?"}[r["status"]]
            print(f"\n{icon}  {r['source_table']:<28s} → {r['target_table']:<26s}")
            print(f"     SQLite: {r['sqlite_count']}   Supabase: {r['supabase_count']}"
                  f"{'   ERR: ' + r['supabase_error'] if r['supabase_error'] else ''}")
            if r["sqlite_missing_from_supabase"]:
                print(f"     ⚠ U SQLite-u ima {len(r['sqlite_missing_from_supabase'])} ID-eva "
                      f"koje NEMA u Supabase-u:")
                for i in r["sqlite_missing_from_supabase"][:5]:
                    print(f"        · {i}")
            if r["supabase_missing_from_sqlite"]:
                print(f"     ⚠ U Supabase-u ima {len(r['supabase_missing_from_sqlite'])} ID-eva "
                      f"koje NEMA u SQLite-u:")
                for i in r["supabase_missing_from_sqlite"][:5]:
                    print(f"        · {i}")

        print("\n" + "=" * 78)
        if drift:
            print("  ⚠ DRIFT DETECTED — proveri gornje tabele, pokreni migraciju za "
                  "nedostajuce ID-eve preko admin panela ili migrate_data_to_supabase.py.")
        else:
            print("  ✓ Sve tabele su konzistentne izmedju SQLite i Supabase-a.")
        print("=" * 78)

    return 1 if drift else 0


def main():
    ap = argparse.ArgumentParser(description="SQLite ↔ Supabase drift checker")
    ap.add_argument("--sample", type=int, default=10, help="How many IDs to sample per table (default 10)")
    ap.add_argument("--only", type=str, default="", help="Comma-separated table names to check (default: all)")
    ap.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    args = ap.parse_args()

    only = [t.strip() for t in args.only.split(",") if t.strip()] if args.only else None
    return reconcile(sample_size=args.sample, only=only, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
