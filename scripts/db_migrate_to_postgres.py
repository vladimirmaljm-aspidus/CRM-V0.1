#!/usr/bin/env python3
"""
MIGRACIJA: SQLite → PostgreSQL

Čita sve tabele iz izvorne SQLite baze i upisuje ih u ciljnu PostgreSQL
bazu preko psycopg (v3) driver-a. Radi u batchevima i sa transakcijom
po tabeli tako da parcijalni pad ne ostavi bazu u pola-stanja.

Šta radi:
  1. Otvori SQLite u read-only režimu i izlistaj tabele.
  2. Za svaku tabelu:
     • CREATE TABLE IF NOT EXISTS sa mapiranim tipovima (TEXT/INTEGER/REAL/BLOB/NUMERIC).
     • TRUNCATE (opcionalno, --truncate) ako želite čist restart.
     • COPY / INSERT batch od 500 redova.
  3. Na kraju: uporedi COUNT(*) po tabeli između SQLite i Postgres.
  4. Ne dira Postgres šeme koje nisu u SQLite.

Zahteva: psycopg[binary] >= 3.1 (dodaj u requirements.txt ako se koristi).

Primer:
    python scripts/db_migrate_to_postgres.py \\
        --sqlite /home/aspidus/data/aspidus_crm.db \\
        --pg "postgresql://aspidus:pass@db.example.com:5432/aspidus_prod" \\
        --schema public

Opcije:
    --truncate     briše target tabelu pre insert-a (BEZBEDNO SAMO NA PRAZNOJ BAZI)
    --skip-tables  lista tabela odvojenih zarezom (npr. audit_logs,search_index_*)
    --dry-run      samo štampa šta bi radio, ne piše
"""
from __future__ import annotations
import argparse
import fnmatch
import re
import sqlite3
import sys
import time
from typing import Iterable

BATCH = 500


def _log(msg: str):
    print(msg, file=sys.stderr, flush=True)


SQLITE_TO_PG = {
    "INTEGER": "BIGINT",
    "INT": "BIGINT",
    "BIGINT": "BIGINT",
    "SMALLINT": "SMALLINT",
    "REAL": "DOUBLE PRECISION",
    "FLOAT": "DOUBLE PRECISION",
    "DOUBLE": "DOUBLE PRECISION",
    "NUMERIC": "NUMERIC",
    "DECIMAL": "NUMERIC",
    "TEXT": "TEXT",
    "CLOB": "TEXT",
    "VARCHAR": "TEXT",
    "CHAR": "TEXT",
    "BLOB": "BYTEA",
    "BOOLEAN": "BOOLEAN",
    "DATE": "DATE",
    "DATETIME": "TIMESTAMP",
    "TIMESTAMP": "TIMESTAMP",
}


def _sqlite_type_to_pg(t: str) -> str:
    if not t:
        return "TEXT"
    tu = t.upper().strip()
    # očisti od (N) ako postoji
    base = re.split(r"[( ]", tu, 1)[0]
    return SQLITE_TO_PG.get(base, "TEXT")


def _list_tables(sc: sqlite3.Connection) -> list[str]:
    return [r[0] for r in sc.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()]


def _table_schema(sc: sqlite3.Connection, table: str) -> list[tuple[str, str, bool]]:
    """Vraća listu (col_name, col_type, is_pk)."""
    rows = sc.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [(r[1], r[2], bool(r[5])) for r in rows]


def _pg_create_table(pg_cur, schema: str, table: str, cols: list[tuple[str, str, bool]]):
    col_defs = []
    pk_cols = []
    for name, sqlite_type, is_pk in cols:
        pg_type = _sqlite_type_to_pg(sqlite_type)
        col_defs.append(f'"{name}" {pg_type}')
        if is_pk:
            pk_cols.append(f'"{name}"')
    if pk_cols:
        col_defs.append(f'PRIMARY KEY ({", ".join(pk_cols)})')
    sql = f'CREATE TABLE IF NOT EXISTS "{schema}"."{table}" ({", ".join(col_defs)})'
    pg_cur.execute(sql)


def _copy_rows(sc: sqlite3.Connection, pg_conn, schema: str, table: str, cols: list[tuple[str, str, bool]], truncate: bool):
    col_names = [c[0] for c in cols]
    cols_quoted = ", ".join(f'"{c}"' for c in col_names)
    placeholders = ", ".join(["%s"] * len(col_names))
    insert_sql = f'INSERT INTO "{schema}"."{table}" ({cols_quoted}) VALUES ({placeholders})'

    with pg_conn.cursor() as pcur:
        if truncate:
            pcur.execute(f'TRUNCATE "{schema}"."{table}"')

        total = 0
        cur = sc.execute(f'SELECT {cols_quoted} FROM "{table}"')
        batch: list[tuple] = []
        for row in cur:
            batch.append(tuple(row))
            if len(batch) >= BATCH:
                pcur.executemany(insert_sql, batch)
                total += len(batch)
                batch.clear()
        if batch:
            pcur.executemany(insert_sql, batch)
            total += len(batch)
    return total


def migrate(sqlite_path: str, pg_url: str, schema: str, truncate: bool, skip_patterns: Iterable[str], dry_run: bool):
    try:
        import psycopg  # psycopg 3
    except ImportError:
        _log("[FATAL] Nedostaje psycopg. Instaliraj: pip install 'psycopg[binary]>=3.1'")
        return 2

    sc = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True, timeout=30.0)
    try:
        tables = _list_tables(sc)
        _log(f"SQLite tabela: {len(tables)}")

        skip_patterns = list(skip_patterns) if skip_patterns else []

        if dry_run:
            for t in tables:
                if any(fnmatch.fnmatch(t, p) for p in skip_patterns):
                    _log(f"  [SKIP] {t}")
                    continue
                n = sc.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                cols = _table_schema(sc, t)
                _log(f"  [DRY] {t}: {n} redova, {len(cols)} kolona")
            return 0

        pg_conn = psycopg.connect(pg_url)
        try:
            with pg_conn.cursor() as pcur:
                pcur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            pg_conn.commit()

            results: dict[str, tuple[int, int]] = {}
            for t in tables:
                if any(fnmatch.fnmatch(t, p) for p in skip_patterns):
                    _log(f"  [SKIP] {t}")
                    continue
                cols = _table_schema(sc, t)
                if not cols:
                    _log(f"  [SKIP] {t} (nema kolona)")
                    continue

                started = time.time()
                _log(f"  [TABLE] {t}: create + copy…")
                try:
                    with pg_conn.cursor() as pcur:
                        _pg_create_table(pcur, schema, t, cols)
                    pg_conn.commit()

                    inserted = _copy_rows(sc, pg_conn, schema, t, cols, truncate)
                    pg_conn.commit()

                    # verifikacija
                    src_cnt = sc.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    with pg_conn.cursor() as pcur:
                        pcur.execute(f'SELECT COUNT(*) FROM "{schema}"."{t}"')
                        dst_cnt = pcur.fetchone()[0]
                    results[t] = (src_cnt, dst_cnt)
                    elapsed = time.time() - started
                    ok = "✓" if src_cnt == dst_cnt else "✗"
                    _log(f"    {ok} src={src_cnt} dst={dst_cnt} ({inserted} umetnuto, {elapsed:.2f}s)")
                except Exception as e:
                    pg_conn.rollback()
                    _log(f"    [ERR] {t}: {e}")
                    results[t] = (-1, -1)

            _log("\n=== Rezime ===")
            mismatched = [t for t, (s, d) in results.items() if s != d]
            if mismatched:
                _log(f"⚠ Neuparene tabele: {', '.join(mismatched)}")
                return 3
            _log(f"✓ Sve {len(results)} tabela migrirano bez razlike u broju redova.")
            return 0
        finally:
            pg_conn.close()
    finally:
        sc.close()


def main():
    ap = argparse.ArgumentParser(description="SQLite → PostgreSQL migracija")
    ap.add_argument("--sqlite", required=True, help="Putanja do SQLite baze")
    ap.add_argument("--pg", required=True, help="PostgreSQL URI (postgresql://user:pass@host:5432/db)")
    ap.add_argument("--schema", default="public", help="Target schema (default: public)")
    ap.add_argument("--truncate", action="store_true", help="TRUNCATE target tabelu pre insert-a")
    ap.add_argument("--skip-tables", default="", help="Zarez-separisana lista glob patterna (npr. audit_logs,search_index_*)")
    ap.add_argument("--dry-run", action="store_true", help="Ne piše ništa, samo prikaz")
    args = ap.parse_args()

    skip = [s.strip() for s in args.skip_tables.split(",") if s.strip()]
    rc = migrate(args.sqlite, args.pg, args.schema, args.truncate, skip, args.dry_run)
    sys.exit(rc)


if __name__ == "__main__":
    main()
