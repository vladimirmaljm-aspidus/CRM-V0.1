#!/usr/bin/env python3
"""Jednokratna migracija: pravi Supabase Auth naloge za sve postojeće
partnere u CRM bazi i šalje im reset-password mail.

Pokreće se ručno posle deploy-a Faze 1, PRE nego što se USE_SUPABASE_AUTH
postavi na true. Ovako partneri imaju već napravljen Auth nalog i mail sa
linkom za postavljanje lozinke.

Sigurne osobine:
  * Idempotentno — ako Auth user već postoji, samo preskočimo kreiranje.
  * Dry-run mode (default) samo štampa šta bi radio, ne dira Supabase.
  * Flag --send-emails šalje reset-mail; bez njega samo pravi naloge.
  * Flag --limit N ograničava koliko partnera obradi (za probu).
  * Loguje sve u audit_log tabelu tako da admin vidi trag.

Primeri:
  python3.13 scripts/migrate_partners_to_supabase.py --dry-run
  python3.13 scripts/migrate_partners_to_supabase.py --limit 3
  python3.13 scripts/migrate_partners_to_supabase.py --send-emails
  python3.13 scripts/migrate_partners_to_supabase.py --send-emails --only-active
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path


def _find_project_root():
    """Nadji CRM koren gde su config.py i .env — kombinacija cwd, script parent i ~/mysite/CRM."""
    here = Path(__file__).resolve().parent
    for cand in (here.parent, here, Path.cwd(), Path.home() / "mysite" / "CRM"):
        if (cand / "config.py").exists():
            return cand
    return here.parent


ROOT = _find_project_root()
sys.path.insert(0, str(ROOT))

# Ucitaj .env pre importa aplikacionih modula
try:
    from dotenv import load_dotenv
    for env_path in (ROOT / ".env", Path.home() / "mysite" / ".env"):
        if env_path.exists():
            load_dotenv(env_path)
            print(f"✓ .env učitan iz {env_path}")
            break
except ImportError:
    print("⚠ python-dotenv nije instaliran — koristim samo shell env")


def parse_args():
    ap = argparse.ArgumentParser(description="Migracija partnera na Supabase Auth")
    ap.add_argument("--dry-run", action="store_true", help="Samo pokaži šta bi radilo, ne dira Supabase")
    ap.add_argument("--send-emails", action="store_true", help="Pošalji reset-password email po nalogu")
    ap.add_argument("--only-active", action="store_true", help="Preskoči partnere sa isPortalActive=false")
    ap.add_argument("--only-email", type=str, help="Obradi SAMO datog partnera (email substring match)")
    ap.add_argument("--skip-create", action="store_true", help="Preskoči create_user, samo šalji reset (za postojeće naloge)")
    ap.add_argument("--limit", type=int, default=0, help="Ograniči broj partnera (0 = svi)")
    ap.add_argument("--sleep", type=float, default=1.5, help="Pauza između poziva Supabase-u (sekunde), default 1.5")
    return ap.parse_args()


def _load_partners():
    from config import DB_FILE
    from utils import decrypt_data

    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    try:
        c = conn.cursor()
        c.execute("SELECT id, data FROM partners")
        out = []
        for pid, data_str in c.fetchall():
            try:
                data = json.loads(data_str) if data_str else {}
            except (json.JSONDecodeError, TypeError, ValueError):
                data = decrypt_data(data_str) or {}
            if not isinstance(data, dict):
                continue
            email = (data.get("contact", {}) or {}).get("email") or data.get("email") or ""
            email = str(email).strip().lower()
            if not email:
                continue
            out.append({
                "id": pid,
                "email": email,
                "companyName": data.get("companyName", ""),
                "isPortalActive": data.get("isPortalActive", True),
            })
        return out
    finally:
        conn.close()


def main():
    args = parse_args()

    # Provera env varijabli
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        if not os.environ.get(k):
            print(f"✗ {k} nije postavljen u .env. Prekidam.")
            return 1

    try:
        from auth_supabase import create_or_get_auth_user, send_password_reset
    except ImportError as e:
        print(f"✗ Ne mogu da importujem auth_supabase: {e}")
        print("  Proveri da li si u pravom radnom folderu i da li je supabase paket instaliran.")
        return 1

    partners = _load_partners()
    if not partners:
        print("Nema partnera sa email adresom u bazi. Ništa za migraciju.")
        return 0

    if args.only_active:
        partners = [p for p in partners if p["isPortalActive"] is not False]

    if args.only_email:
        needle = args.only_email.lower()
        partners = [p for p in partners if needle in p["email"].lower()]
        if not partners:
            print(f"Nijedan partner ne odgovara --only-email={args.only_email!r}.")
            return 0

    if args.limit and args.limit > 0:
        partners = partners[: args.limit]

    print(f"\nMigracija — {len(partners)} partner(a).")
    print(f"  dry_run={args.dry_run} send_emails={args.send_emails} only_active={args.only_active} "
          f"skip_create={args.skip_create} sleep={args.sleep}s\n")

    stats = {"created": 0, "existing": 0, "errors": 0, "reset_sent": 0, "reset_failed": 0}
    log_lines = []

    for i, p in enumerate(partners, 1):
        prefix = f"[{i}/{len(partners)}] {p['email']:40s}"
        if args.dry_run:
            action = "reset-only" if args.skip_create else "create+reset"
            print(f"{prefix}  (dry-run: {action})")
            continue

        uid = None
        if not args.skip_create:
            uid, status = create_or_get_auth_user(
                p["email"],
                partner_id=p["id"],
                company_name=p["companyName"],
                email_confirm=True,
            )
            if status == "created":
                stats["created"] += 1
                marker = "NEW "
            elif status == "existing":
                stats["existing"] += 1
                marker = "EXST"
            else:
                stats["errors"] += 1
                print(f"{prefix}  ✗ {status}")
                log_lines.append(f"{p['id']} {p['email']} auth_create_failed: {status}")
                time.sleep(args.sleep)
                continue
        else:
            marker = "SKIP"

        line = f"{prefix}  ✓ {marker} uid={(uid or '-')[:8]}"
        if args.send_emails:
            ok, detail = send_password_reset(p["email"])
            if ok:
                stats["reset_sent"] += 1
                line += "  📧 reset sent"
            else:
                stats["reset_failed"] += 1
                line += f"  ✗ reset failed: {detail}"
        print(line)
        log_lines.append(f"{p['id']} {p['email']} action=create_reset reset_sent={args.send_emails}")
        time.sleep(args.sleep)

    print("\n── Rezime ──")
    for k, v in stats.items():
        print(f"  {k:15s} {v}")

    # Zapiši u audit log
    try:
        from utils import log_audit
        summary = f"Supabase auth migration: {json.dumps(stats)}"
        log_audit("EDIT", "portal", summary, is_suspicious=False)
    except Exception:
        pass

    print("\n✅ Gotovo. Sledeći korak: postavi USE_SUPABASE_AUTH=true u .env i Reload web app-a.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
