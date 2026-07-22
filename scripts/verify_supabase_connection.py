#!/usr/bin/env python3
"""Verifikacija Supabase konekcije — pokreni na PythonAnywhere-u posle podešavanja .env.

Proverava:
  1) .env fajl je učitan i sadrži sve potrebne varijable
  2) Postgres konekcija radi i vidi svih 15 tabela
  3) Storage bucket-i `partner-docs` i `offer-pdfs` postoje
  4) Auth admin API je dostupan sa service_role ključem

Pokreni:
    python3.11 scripts/verify_supabase_connection.py

Ako pukne, izlaz kopiraj i pošalji — tu je sve što treba za dijagnostiku.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

# ANSI boje
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"

_HAS_ANY_FAILURE = False


def ok(msg: str):
    print(f"{GREEN}✓{RESET} {msg}")


def fail(msg: str):
    global _HAS_ANY_FAILURE
    _HAS_ANY_FAILURE = True
    print(f"{RED}✗{RESET} {msg}")


def warn(msg: str):
    print(f"{YELLOW}⚠{RESET} {msg}")


def section(title: str):
    print(f"\n{DIM}── {title} ──{RESET}")


# ==========================================================================
# 1) .ENV fajl
# ==========================================================================
def load_env():
    try:
        from dotenv import load_dotenv, find_dotenv
    except ImportError:
        fail("python-dotenv nije instaliran. Pokreni:  pip3.11 install --user python-dotenv")
        sys.exit(1)

    # Prvo probaj CWD, pa parent folder (za slučaj kad se pokreće iz scripts/)
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
        Path.home() / "mysite" / ".env",
    ]
    for p in candidates:
        if p.is_file():
            load_dotenv(p)
            ok(f".env loaded from {p}")
            return
    # Fallback: find_dotenv
    p = find_dotenv()
    if p:
        load_dotenv(p)
        ok(f".env loaded from {p}")
        return
    fail(".env fajl nije nadjen. Kreiraj ga u istom folderu gde je app.py.")
    sys.exit(1)


# ==========================================================================
# 2) Env varijable
# ==========================================================================
def check_env_vars():
    required = [
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
        "SUPABASE_DB_URL",
    ]
    section("Environment variables")
    got = {}
    for k in required:
        v = os.environ.get(k, "").strip()
        got[k] = v
        if not v:
            fail(f"{k} nije postavljen (prazan ili nedostaje)")
            continue
        if "<" in v or v.startswith("POPUNI") or v == "sb_secret_XXXX":
            fail(f"{k} sadrži placeholder — nisi zamenio pravu vrednost")
            continue
        if k == "SUPABASE_URL":
            ok(f"{k}: {v}")
        elif k == "SUPABASE_JWT_SECRET":
            ok(f"{k}: {v[:6]}… ({len(v)} chars)")
        elif k == "SUPABASE_DB_URL":
            # Sakrij lozinku u prikazu
            masked = _mask_db_url(v)
            ok(f"{k}: {masked}")
        else:
            ok(f"{k}: {v[:20]}… (present)")
    return got


def _mask_db_url(url: str) -> str:
    """postgresql://user:PASS@host:port/db → postgresql://user:***@host:port/db"""
    try:
        head, tail = url.split("://", 1)
        if "@" in tail and ":" in tail.split("@")[0]:
            userpart, hostpart = tail.split("@", 1)
            user = userpart.split(":", 1)[0]
            return f"{head}://{user}:***@{hostpart}"
    except Exception:
        pass
    return url


# ==========================================================================
# 3) Postgres konekcija + tabele
# ==========================================================================
def check_postgres(db_url: str):
    section("Postgres connectivity")
    try:
        import psycopg
    except ImportError:
        fail("psycopg nije instaliran. Pokreni:  pip3.11 install --user 'psycopg[binary,pool]'")
        return

    expected_tables = {
        "audit_logs", "deals", "demands", "document_register",
        "document_revisions", "kyc_submissions", "offer_versions",
        "offers", "partners", "portal_hidden_items", "portal_products",
        "products", "profile_change_requests", "shared_documents",
        "storage_objects",
    }
    try:
        with psycopg.connect(db_url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                ver = cur.fetchone()[0]
                ok(f"Connected to {ver.split(',')[0]}")
                cur.execute(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname='public' ORDER BY tablename"
                )
                tables = {r[0] for r in cur.fetchall()}
                missing = expected_tables - tables
                extra = tables - expected_tables
                if missing:
                    fail(f"Nedostaju tabele: {', '.join(sorted(missing))}")
                    fail("→ pokreni schemas/supabase_schema.sql u Supabase SQL Editor-u")
                else:
                    ok(f"Found {len(tables)} tables in public schema")
                    print(f"   {DIM}{', '.join(sorted(tables))}{RESET}")
                if extra:
                    warn(f"Neočekivane tabele (nije problem, samo info): {', '.join(sorted(extra))}")
    except psycopg.OperationalError as e:
        fail(f"Postgres konekcija propala: {e}")
        if "authentication" in str(e).lower() or "password" in str(e).lower():
            fail("→ Verovatno je DB lozinka pogrešna. Vrati se na Korak 1B u SETUP_PYTHONANYWHERE.md.")
        elif "could not translate" in str(e).lower():
            fail("→ SUPABASE_DB_URL host je pogrešan ili nema mreže.")
    except Exception as e:
        fail(f"Neočekivana greška: {e}")


# ==========================================================================
# 4) Supabase Storage + Auth
# ==========================================================================
def check_supabase(url: str, service_key: str):
    try:
        from supabase import create_client
    except ImportError:
        fail("supabase paket nije instaliran. Pokreni:  pip3.11 install --user supabase")
        return

    try:
        client = create_client(url, service_key)
    except Exception as e:
        fail(f"Ne mogu da kreiram Supabase klijenta: {e}")
        return

    # ---- Storage ----
    section("Storage buckets")
    try:
        buckets = client.storage.list_buckets()
        names = {b.name if hasattr(b, "name") else b.get("name", "") for b in buckets}
        for wanted in ("partner-docs", "offer-pdfs"):
            if wanted in names:
                ok(f"Bucket '{wanted}' exists")
            else:
                fail(f"Bucket '{wanted}' NE POSTOJI")
                fail("→ kreiraj ga u Storage → New bucket (Private, 10 MB)")
    except Exception as e:
        fail(f"Ne mogu da čitam Storage bucket-e: {e}")

    # ---- Auth admin API ----
    section("Auth admin API")
    try:
        # list users (page=1, per_page=1) — samo test da API radi
        resp = client.auth.admin.list_users(page=1, per_page=1)
        # Supabase Python SDK vraća list ili objekat sa users; podržavamo oba
        n_users = 0
        if hasattr(resp, "users"):
            n_users = len(resp.users)
        elif isinstance(resp, list):
            n_users = len(resp)
        elif isinstance(resp, dict) and "users" in resp:
            n_users = len(resp["users"])
        ok(f"Auth admin API reachable ({n_users} users on page 1)")
    except Exception as e:
        fail(f"Auth admin API ne radi: {e}")
        if "Invalid API key" in str(e) or "unauthorized" in str(e).lower():
            fail("→ SUPABASE_SERVICE_ROLE_KEY je pogrešan. Proveri korak 1 iz SETUP.")


# ==========================================================================
# 5) JWT Secret sanity
# ==========================================================================
def check_jwt_secret(secret: str):
    section("JWT Secret sanity")
    if len(secret) < 20:
        fail(f"JWT Secret je previše kratak ({len(secret)} chars). Verovatno pogrešna vrednost.")
        return
    # Proveri da MOŽEMO da potpišemo dummy token
    try:
        import jwt
    except ImportError:
        warn("PyJWT nije instaliran. Preskačem JWT sanity check.")
        warn("→ instalira se automatski kad dodam auth kod u Fazi 1.")
        return
    try:
        token = jwt.encode({"sub": "test", "role": "authenticated"}, secret, algorithm="HS256")
        decoded = jwt.decode(token, secret, algorithms=["HS256"])
        if decoded.get("sub") == "test":
            ok("JWT Secret validan (sign + verify radi)")
        else:
            fail("JWT Secret encode/decode neusaglašen")
    except Exception as e:
        fail(f"JWT Secret test propao: {e}")


# ==========================================================================
def main():
    print(f"{DIM}=== Aspidus × Supabase — verifikacija setup-a ==={RESET}")
    load_env()
    env = check_env_vars()
    if env.get("SUPABASE_DB_URL"):
        check_postgres(env["SUPABASE_DB_URL"])
    if env.get("SUPABASE_URL") and env.get("SUPABASE_SERVICE_ROLE_KEY"):
        check_supabase(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
    if env.get("SUPABASE_JWT_SECRET"):
        check_jwt_secret(env["SUPABASE_JWT_SECRET"])

    print()
    if _HAS_ANY_FAILURE:
        print(f"{RED}✗ Ima grešaka — pogledaj gore.{RESET}")
        sys.exit(1)
    print(f"{GREEN}✅ SVE JE POVEZANO. Spreman si za Fazu 1.{RESET}")


if __name__ == "__main__":
    main()
