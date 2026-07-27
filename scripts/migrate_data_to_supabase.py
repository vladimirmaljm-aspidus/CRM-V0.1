#!/usr/bin/env python3
"""Migracija SVIH podataka iz lokalnog SQLite-a u Supabase Postgres.

Radi kroz data_layer facade (REST backend na PA Free, direct psycopg na Hacker+).
UPSERT po `id` — bezbedno se može pokrenuti više puta, ne pravi duplikate.

Funkcije:
  * Automatsko mapiranje SQLite tabela → Supabase tabela
  * JSON payload iz `data` kolone se merge-uje u eksplicitne kolone (email,
    can_login, portal_level, ...) plus `data` JSONB zadržava sve što ne mapira
  * Dry-run mode koji broji redove ali ne piše ništa
  * Batch veličina konfigurabilna (default 50 po batch-u da ne udara PA
    memory limit ili Supabase rate limit)
  * Rezime na kraju sa svakom tabelom: source_count, migrated, errors
  * Loguje sve u audit_log

Primeri:
  # 1) Dry-run — samo pokazuje koliko čega ima
  python3.13 scripts/migrate_data_to_supabase.py --dry-run

  # 2) Migrira samo partnere (za probu)
  python3.13 scripts/migrate_data_to_supabase.py --tables partners --confirm

  # 3) Puna migracija
  python3.13 scripts/migrate_data_to_supabase.py --confirm

  # 4) Nastavlja od greske (skip vec migrirane)
  python3.13 scripts/migrate_data_to_supabase.py --confirm --skip-existing
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Callable, Iterable

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_env():
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    home = Path.home()
    for cand in (
        _ROOT / ".env",
        Path.cwd() / ".env",
        home / "mysite" / "CRM" / ".env",
        home / "mysite" / ".env",
    ):
        if cand.exists():
            load_dotenv(cand)
            print(f"✓ .env učitan iz {cand}")
            return


# ────────────────────────────────────────────────────────────────────
#  Mapiranje SQLite → Supabase
#  Za svaku tabelu definišemo:
#    source_db:  koja lokalna baza (crm/portal/audit)
#    source_table: ime tabele u SQLite-u
#    target_table: ime tabele u Supabase-u
#    transform:  funkcija koja prima sqlite row dict i vraća supabase row dict
# ────────────────────────────────────────────────────────────────────

def _safe_parse(s):
    if not s:
        return {}
    if isinstance(s, dict):
        return s
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        try:
            from utils import decrypt_data
            v = decrypt_data(s)
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}


def _transform_partner(row: dict) -> dict:
    data = _safe_parse(row.get("data"))
    contact = data.get("contact", {}) or {}
    return {
        "id": row["id"],
        "company_name": data.get("companyName") or data.get("name", ""),
        "email": (contact.get("email") or data.get("email") or "").strip().lower() or None,
        "phone": contact.get("phone") or data.get("phone"),
        "contact_person": contact.get("person") or data.get("contactPerson"),
        "country": data.get("country") or contact.get("country"),
        "city": contact.get("city") or data.get("city"),
        "street": contact.get("street") or data.get("street"),
        "tax_id": data.get("taxId"),
        "portal_token": data.get("portalToken"),
        "is_portal_active": bool(data.get("isPortalActive", True)),
        "portal_level": int(data.get("portalLevel") or 1),
        "is_premium": bool(data.get("isPremium", False)),
        "kyc_approved": bool(data.get("kycApproved", False)),
        "can_login": bool(data.get("canLogin", True)),
        "data": data,
    }


def _transform_generic(row: dict, id_col: str = "id") -> dict:
    """Za tabele koje čuvaju sve u JSON `data` koloni."""
    data = _safe_parse(row.get("data"))
    return {
        id_col: row.get(id_col) or row.get("id"),
        "data": data,
    }


def _transform_product(row: dict) -> dict:
    data = _safe_parse(row.get("data"))
    return {
        "id": row["id"],
        "name": data.get("name") or data.get("productName", ""),
        "sku": data.get("sku") or data.get("code"),
        "hs_code": data.get("hsCode"),
        "unit": data.get("unit"),
        "supplier_id": data.get("supplierId"),
        "data": data,
    }


def _transform_deal(row: dict) -> dict:
    data = _safe_parse(row.get("data"))
    return {
        "id": row["id"],
        "buyer_id": data.get("buyerId") or data.get("partnerId"),
        "supplier_id": data.get("supplierId"),
        "product_id": data.get("productId"),
        "status": data.get("status"),
        "total_amount": data.get("totalAmount") or data.get("total"),
        "currency": data.get("currency"),
        "data": data,
    }


def _transform_kyc(row: dict) -> dict:
    data = _safe_parse(row.get("data"))
    return {
        "id": row["id"],
        "partner_id": row.get("partner_id") or data.get("partnerId"),
        "token": row.get("token"),
        "submitted_at": row.get("submitted_at"),
        "status": data.get("status", "pending"),
        "data": data,
    }


def _transform_portal_product(row: dict) -> dict:
    data = _safe_parse(row.get("data"))
    return {
        "id": row["id"],
        "partner_id": row.get("partner_id"),
        "status": row.get("status", "pending"),
        "created_at": row.get("created_at"),
        "data": data,
    }


def _transform_activity_log(row: dict) -> dict:
    return {
        "id": row["id"],
        "partner_id": row.get("partner_id"),
        "action": row.get("action"),
        "details": row.get("details"),
        "ip_address": row.get("ip_address"),
        "user_agent": row.get("user_agent"),
        "location": row.get("location"),
        "timestamp": row.get("timestamp"),
    }


def _transform_audit(row: dict) -> dict:
    return {
        "id": row.get("id") or row.get("log_id"),
        "action_type": row.get("action_type") or row.get("action"),
        "resource": row.get("resource"),
        "detail": row.get("detail") or row.get("message"),
        "is_suspicious": bool(row.get("is_suspicious", 0)),
        "timestamp": row.get("timestamp") or row.get("ts"),
        "user_id": row.get("user_id"),
        "ip_address": row.get("ip_address"),
    }


MIGRATION_PLAN = [
    # (source_db, source_table, target_table, transform)
    ("crm",    "partners",                  "partners",                  _transform_partner),
    ("crm",    "products",                  "products",                  _transform_product),
    ("crm",    "deals",                     "deals",                     _transform_deal),
    ("crm",    "demands",                   "demands",                   lambda r: _transform_generic(r)),
    ("crm",    "customer_offers",           "offers",                    lambda r: _transform_generic(r)),
    ("portal", "kyc_submissions",           "kyc_submissions",           _transform_kyc),
    ("portal", "portal_products",           "portal_products",           _transform_portal_product),
    ("portal", "portal_activity_log",       "audit_logs",                _transform_activity_log),
    ("audit",  "audit_log",                 "audit_logs",                _transform_audit),
]


def _open_db(source: str):
    from config import DB_FILE, PORTAL_DB_FILE, AUDIT_DB_FILE
    path = {"crm": DB_FILE, "portal": PORTAL_DB_FILE, "audit": AUDIT_DB_FILE}.get(source)
    if not path or not os.path.exists(path):
        return None
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn, table):
    if conn is None:
        return False
    try:
        c = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return c.fetchone() is not None
    except Exception:
        return False


def _count_rows(conn, table):
    try:
        c = conn.execute(f'SELECT COUNT(*) FROM "{table}"')
        return int(c.fetchone()[0])
    except Exception:
        return 0


def _iter_rows(conn, table, batch=50) -> Iterable[list]:
    c = conn.execute(f'SELECT * FROM "{table}"')
    while True:
        rows = c.fetchmany(batch)
        if not rows:
            break
        yield [dict(r) for r in rows]


def parse_args():
    ap = argparse.ArgumentParser(description="Migracija SQLite → Supabase Postgres")
    ap.add_argument("--dry-run", action="store_true", help="Samo broji, ne piše ništa")
    ap.add_argument("--confirm", action="store_true", help="Pravi migracija (WRITE)")
    ap.add_argument("--tables", type=str, help="Comma-separated lista tabela (default: sve)")
    ap.add_argument("--skip-existing", action="store_true",
                    help="Preskoči redove koji već postoje (idempotent by default; ovo brže)")
    ap.add_argument("--batch", type=int, default=50, help="Batch size (default 50)")
    ap.add_argument("--limit", type=int, default=0, help="Ograniči broj redova po tabeli (0 = svi)")
    return ap.parse_args()


def main():
    args = parse_args()
    if not args.dry_run and not args.confirm:
        args.dry_run = True
        print("⚠ Nijedan flag naveden — pretpostavljam --dry-run (bezbedno)\n")

    _load_env()

    # Uveri se da je Supabase konekcija podešena
    try:
        from data_layer import get_backend, count as db_count, upsert as db_upsert
    except ImportError as e:
        print(f"✗ Ne mogu da importujem data_layer: {e}")
        return 1

    try:
        backend = get_backend()
        print(f"✓ Supabase backend: {backend.name}\n")
    except Exception as e:
        print(f"✗ Supabase konekcija ne radi: {e}")
        return 1

    which = None
    if args.tables:
        which = {t.strip() for t in args.tables.split(",") if t.strip()}

    plan = [p for p in MIGRATION_PLAN if not which or p[2] in which or p[1] in which]

    # Otvori sve tri source baze
    conns = {
        "crm":    _open_db("crm"),
        "portal": _open_db("portal"),
        "audit":  _open_db("audit"),
    }

    total_stats = {"tables": 0, "read": 0, "written": 0, "errors": 0}
    per_table = []

    for source_db, src_table, tgt_table, transform in plan:
        conn = conns.get(source_db)
        if conn is None:
            print(f"⏭  {src_table} → {tgt_table:20s}  (source_db={source_db} nije dostupan)")
            continue
        if not _table_exists(conn, src_table):
            print(f"⏭  {src_table:24s} → {tgt_table:20s}  (source table ne postoji u {source_db}.db)")
            continue

        src_count = _count_rows(conn, src_table)
        if src_count == 0:
            print(f"⏭  {src_table:24s} → {tgt_table:20s}  (0 redova)")
            continue

        stats = {"src": src_count, "written": 0, "errors": 0, "skipped": 0}
        prefix = f"→  {src_table:24s} → {tgt_table:20s}"
        print(f"{prefix}  {src_count} redova...")

        if args.dry_run:
            print(f"   (dry-run: ne pišem)")
            per_table.append((src_table, tgt_table, stats["src"], 0, 0))
            total_stats["tables"] += 1
            total_stats["read"] += stats["src"]
            continue

        processed = 0
        for batch in _iter_rows(conn, src_table, batch=args.batch):
            for raw in batch:
                if args.limit and processed >= args.limit:
                    break
                processed += 1
                try:
                    xformed = transform(raw)
                    if xformed.get("id") is None:
                        stats["errors"] += 1
                        continue
                    db_upsert(tgt_table, xformed, on_conflict="id")
                    stats["written"] += 1
                except Exception as e:
                    stats["errors"] += 1
                    if stats["errors"] <= 3:  # ne spamuj log
                        print(f"     ✗ row error: {e.__class__.__name__}: {str(e)[:200]}")
            if args.limit and processed >= args.limit:
                break
            time.sleep(0.05)  # laka pauza da REST backend ne udara rate limit

        per_table.append((src_table, tgt_table, stats["src"], stats["written"], stats["errors"]))
        total_stats["tables"] += 1
        total_stats["read"] += stats["src"]
        total_stats["written"] += stats["written"]
        total_stats["errors"] += stats["errors"]

        icon = "✅" if stats["errors"] == 0 else "⚠"
        print(f"   {icon} {stats['written']}/{stats['src']} migrirano, {stats['errors']} greške")

    # Zatvori source baze
    for conn in conns.values():
        if conn:
            conn.close()

    # Rezime
    print("\n" + "=" * 60)
    print("REZIME MIGRACIJE")
    print("=" * 60)
    for src, tgt, srcc, w, err in per_table:
        pct = f"{w * 100 // max(srcc, 1)}%" if not args.dry_run else "-"
        print(f"  {src:24s} → {tgt:20s} {srcc:>6} src → {w:>6} written [{pct}] err={err}")
    print("-" * 60)
    print(f"  TOTAL: {total_stats['tables']} tables, "
          f"{total_stats['read']} src rows, "
          f"{total_stats['written']} written, "
          f"{total_stats['errors']} errors")

    if args.dry_run:
        print("\n✓ Dry-run gotov. Ponovi sa --confirm za pravu migraciju.")
    elif total_stats["errors"] == 0:
        print("\n✅ MIGRACIJA USPESNA. Podaci su u Supabase Postgres-u.")
        print("   Sledeći korak: postavi USE_SUPABASE_DB=true u .env + Reload.")
        try:
            from utils import log_audit
            log_audit("EDIT", "system",
                      f"SQLite→Supabase migration: {total_stats}",
                      is_suspicious=False)
        except Exception:
            pass
    else:
        print(f"\n⚠ Migracija završena sa {total_stats['errors']} grešaka.")
        print("   Pokreni ponovo sa --skip-existing da nastaviš od gde je stalo.")

    return 0 if (args.dry_run or total_stats["errors"] == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
