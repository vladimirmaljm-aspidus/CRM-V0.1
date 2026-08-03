"""V25 SUPABASE-ONLY database module.

Sve CRM tabele su u Supabase Postgres bazi (vidi schemas/supabase_v23_1.sql,
schemas/supabase_fix_v25.sql, schemas/supabase_backfill_v25.sql).
Ovaj modul je sužen na `seed_admin_supabase()` (kreira admin nalog pri prvom
bootu ako u Supabase users tabeli nema korisnika) i `init_db()` (no-op —
legacy kompatibilnost sa app.py koji je još uvek poziva).

Aplikacija ne koristi SQLite ni u jednom toku — svi podaci idu u Supabase
preko `data_layer` facade (REST/Postgres/Mock).
"""
import os
import logging

logger = logging.getLogger(__name__)


def seed_admin_supabase():
    """V24.0: Osigurava da u Supabase users tabeli postoji admin nalog.
    Idempotentno: ako vec postoji makar jedan user, ne dira nista.
    Ako je Supabase down/ne-konfigurisan, samo loguje i preskace — app
    treba da nastavi da radi.
    """
    try:
        from werkzeug.security import generate_password_hash
        import supabase_store as store
        username = (os.getenv('ADMIN_USERNAME') or 'admin').strip()
        password = os.getenv('ADMIN_PASSWORD') or 'Admin12345'
        pw_hash = generate_password_hash(password, method='scrypt:32768:8:1')
        created = store.seed_admin_if_empty(username, pw_hash)
        if created:
            logger.warning(f'SEED (Supabase): admin "{username}" upisan u Supabase users tabelu.')
        else:
            logger.info('SEED (Supabase): users tabela vec ima korisnike, seed preskocen.')
    except Exception as e:
        logger.warning(f'SEED (Supabase) skipped: {type(e).__name__}: {str(e)[:200]}')


def init_db():
    """V25 SUPABASE-ONLY: no-op.

    Sve tabele već postoje u Supabase (kreirane kroz schemas/supabase_v23_1.sql
    i primenjene kroz schemas/supabase_fix_v25.sql). Ova funkcija je ostala
    samo radi backward-compat sa app.py koji je je pozivao pri bootu.
    """
    logger.debug('init_db() no-op (V25 Supabase-only mode).')
    return
