"""V24.0 SUPABASE-ONLY STORE — jedinstvena tacka za sve CRM CRUD operacije.

Zameniuje `sqlite3.connect(DB_FILE) + cursor` pattern u kriticnim tokovima
(auth, settings, entity CRUD, magic-link JTI). Nema SQLite-a: sve ide u
Supabase preko `data_layer` facade-a.

Interfejs je namerno sitan i sličan onome sto su route fajlovi vec zvali:

    get_user_by_username(username)   -> dict | None
    get_user_by_id(user_id)          -> dict | None
    upsert_user(user_dict)           -> dict
    update_user_password(user_id, new_hash, now_iso)
    seed_admin_if_empty(admin_username, admin_password)

    list_entities(table)             -> list[dict]  (partners/products/…)
    get_entity(table, entity_id)     -> dict | None
    upsert_entity(table, item)       -> dict
    delete_entity(table, entity_id)  -> bool

    get_setting(key)                 -> value | None
    set_setting(key, value)

    is_jti_used(jti) / mark_jti_used(jti, token, ip)

Kolone koje idu direktno u Supabase kolone dolaze iz `SUPPORTED_TABLES`
whitelist-a (`routes/supabase_merge.py`). Sve ostalo se pakuje u JSONB
`data` kolonu. Pri citanju, `_rehydrate_row` spaja obe strane nazad u
flat dict koji stari frontend/kod ocekuje.
"""
from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# --------- USER (auth) ------------------------------------------------

_USER_COLS = {'id', 'username', 'password', 'role', 'full_name', 'email',
              'phone', 'notif_prefs', 'permissions', 'must_change_password',
              'locked_until', 'password_expires_at', 'signature',
              'totp_secret', 'totp_enabled', 'totp_recovery',
              'token_version', 'last_password_change_at',
              'last_login_country'}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _coerce_user_out(u: dict) -> dict:
    """Rehidrisi flat user dict — spoji `data` JSONB sa top-level kolonama."""
    if not isinstance(u, dict):
        return {}
    out = {}
    d = u.get('data')
    if isinstance(d, dict):
        out.update(d)
    elif isinstance(d, str):
        try: out.update(json.loads(d))
        except Exception: pass
    for k, v in u.items():
        if k == 'data' or v is None:
            continue
        out[k] = v
    # permissions moze biti string ili dict — normalizuj
    p = out.get('permissions')
    if isinstance(p, str):
        try: out['permissions'] = json.loads(p)
        except Exception: out['permissions'] = {}
    return out


def _split_user_for_upsert(user: dict) -> dict:
    """Podeli user dict na Supabase-whitelisted kolone + `data` JSONB."""
    row = {}
    extra = {}
    for k, v in user.items():
        if k in _USER_COLS:
            row[k] = v
        elif v is not None:
            extra[k] = v
    # notif_prefs / permissions moraju biti JSONB (dict), ne string
    for jkey in ('notif_prefs', 'permissions'):
        if jkey in row and isinstance(row[jkey], str):
            try: row[jkey] = json.loads(row[jkey])
            except Exception: row[jkey] = {}
    if extra:
        row['data'] = extra
    return row


def get_user_by_username(username: str) -> dict | None:
    if not username:
        return None
    try:
        from data_layer import select
        # PostgREST nema direktan LOWER() ali ILIKE bez wildcard-a funkcionise
        rows = select('users', filters={'username': ('ilike', str(username))}, limit=1) or []
        if not rows:
            # fallback: skeniraj sve (mala tabela — CRM interni useri)
            rows = [r for r in (select('users') or [])
                    if str(r.get('username', '')).lower() == str(username).lower()]
        return _coerce_user_out(rows[0]) if rows else None
    except Exception as e:
        logger.error(f'get_user_by_username({username}) failed: {e}')
        return None


def get_user_by_id(user_id: str) -> dict | None:
    if not user_id:
        return None
    try:
        from data_layer import select_one
        row = select_one('users', {'id': user_id})
        return _coerce_user_out(row) if row else None
    except Exception as e:
        logger.error(f'get_user_by_id({user_id}) failed: {e}')
        return None


def upsert_user(user: dict) -> dict:
    """Kreiraj ili azuriraj user-a. Zahteva 'id' polje."""
    if not user or not user.get('id'):
        raise ValueError('upsert_user requires id')
    from data_layer import upsert
    row = _split_user_for_upsert(user)
    return upsert('users', row, on_conflict='id')


def update_user_password(user_id: str, new_hash: str, now_iso: str | None = None):
    """Postavi novi password hash + bump last_password_change_at."""
    from data_layer import update
    ts = now_iso or _now_iso()
    return update('users', {'id': user_id},
                  {'password': new_hash, 'last_password_change_at': ts})


def bump_token_version(user_id: str):
    u = get_user_by_id(user_id) or {}
    v = int(u.get('token_version', 1) or 1) + 1
    from data_layer import update
    update('users', {'id': user_id}, {'token_version': v})
    return v


def set_locked_until(user_id: str, until_iso: str | None):
    from data_layer import update
    update('users', {'id': user_id}, {'locked_until': until_iso})


def seed_admin_if_empty(admin_username: str, admin_password_hash: str) -> str | None:
    """Ako u users tabeli nema NIJEDNOG user-a, kreiraj admin nalog.
    Vraca id kreiranog admin-a, ili None ako je vec bilo user-a.
    Idempotentno preko username unique constraint-a."""
    try:
        from data_layer import count
        n = count('users')
        if n and int(n) > 0:
            return None
    except Exception as e:
        logger.error(f'seed_admin_if_empty: count failed: {e}')
        return None

    admin_id = str(uuid.uuid4())
    try:
        upsert_user({
            'id': admin_id,
            'username': admin_username,
            'password': admin_password_hash,
            'role': 'admin',
            'permissions': {},
            'token_version': 1,
        })
        logger.warning(f"SEED (Supabase): kreiran admin '{admin_username}'")
        return admin_id
    except Exception as e:
        logger.error(f'seed_admin_if_empty: upsert failed: {e}')
        return None


# --------- ENTITY CRUD (partners / products / offers / deals / …) ----

# whitelist tabela + kolona — dolaze iz SUPPORTED_TABLES definisanog u
# routes/supabase_merge.py. Ovde imamo lokalni cache dabi izbegli circular
# import (routes/supabase_merge.py takodje moze da importuje ovaj modul).
_ENTITY_COLS = {
    'partners': {'id', 'auth_user_id', 'email', 'company_name', 'phone',
                 'contact_person', 'country', 'city', 'street', 'tax_id',
                 'portal_token', 'is_portal_active', 'portal_level',
                 'is_premium', 'kyc_approved', 'can_login'},
    'products': {'id', 'name', 'sku', 'hs_code', 'unit', 'supplier_id'},
    'deals':    {'id', 'buyer_id', 'source_offer_id', 'supplier_id',
                 'product_id', 'status', 'total_amount', 'currency'},
    'demands':  {'id', 'buyer_id'},
    'offers':   {'id', 'offer_no', 'customer_id'},
    'shared_documents': {'id', 'partner_id', 'title', 'category',
                         'storage_bucket', 'storage_path'},
    'accounts':          {'id'},
    'transactions':      {'id'},
    'recurringExpenses': {'id'},
    'connections':       {'id'},
}

# V24.1: camelCase → snake_case Supabase table name mapping (Postgres
# konvencija je snake_case, frontend/kod salju camelCase).
_TABLE_ALIAS = {
    'recurringExpenses': 'recurring_expenses',
}


def _real_table(name: str) -> str:
    return _TABLE_ALIAS.get(name, name)


def _entity_split(table: str, item: dict) -> dict:
    """Isto kao za user: whitelist top-level, sve ostalo → data JSONB."""
    cols = _ENTITY_COLS.get(table, {'id'})
    row = {}
    extra = {}
    for k, v in item.items():
        if k in cols:
            row[k] = v
        elif v is not None:
            extra[k] = v
    if extra:
        row['data'] = extra
    return row


def _entity_join(row: dict) -> dict:
    """Spoji flat kolone + `data` JSONB nazad u dict za frontend."""
    if not isinstance(row, dict):
        return {}
    out = {}
    d = row.get('data')
    if isinstance(d, dict): out.update(d)
    elif isinstance(d, str):
        try: out.update(json.loads(d))
        except Exception: pass
    for k, v in row.items():
        if k == 'data' or v is None: continue
        out[k] = v
    return out


def list_entities(table: str, limit: int = 5000) -> list[dict]:
    from data_layer import select
    real = _real_table(table)
    try:
        rows = select(real, limit=limit) or []
        return [_entity_join(r) for r in rows if isinstance(r, dict)]
    except Exception as e:
        logger.info(f'list_entities({table}) failed: {e}')
        return []


def get_entity(table: str, entity_id: str) -> dict | None:
    if not entity_id:
        return None
    from data_layer import select_one
    real = _real_table(table)
    try:
        row = select_one(real, {'id': entity_id})
        return _entity_join(row) if row else None
    except Exception as e:
        logger.info(f'get_entity({table}/{entity_id}) failed: {e}')
        return None


def upsert_entity(table: str, item: dict) -> dict:
    if not item or not item.get('id'):
        raise ValueError('upsert_entity requires id')
    from data_layer import upsert
    real = _real_table(table)
    # Kolone su definisane pod originalnim (frontend) imenom
    row = _entity_split(table, item)
    result = upsert(real, row, on_conflict='id')
    return _entity_join(result if isinstance(result, dict) else row)


def delete_entity(table: str, entity_id: str) -> bool:
    if not entity_id:
        return False
    from data_layer import delete
    real = _real_table(table)
    try:
        n = delete(real, {'id': entity_id})
        return int(n or 0) > 0
    except Exception as e:
        logger.info(f'delete_entity({table}/{entity_id}) failed: {e}')
        return False


# --------- SETTINGS -----------------------------------------------------

def get_setting(key: str):
    """Vraca deserijalizovanu vrednost iz settings tabele ili None."""
    if not key: return None
    try:
        from data_layer import select_one
        row = select_one('settings', {'key': key})
        if not row: return None
        v = row.get('value')
        # Settings.value je enkriptovan string (od utils.encrypt_data);
        # decryption ide u pozivajucem kodu (utils.decrypt_data). Ovde ga
        # samo prosledjujemo.
        return v
    except Exception as e:
        logger.info(f'get_setting({key}) failed: {e}')
        return None


def set_setting(key: str, encrypted_value: str):
    if not key:
        raise ValueError('set_setting requires key')
    from data_layer import upsert
    upsert('settings', {'key': key, 'value': encrypted_value}, on_conflict='key')


def delete_setting(key: str):
    from data_layer import delete
    return delete('settings', {'key': key})


# --------- MAGIC LINK JTI (single-use) ---------------------------------

def is_jti_used(jti: str) -> bool:
    if not jti: return True
    try:
        from data_layer import select_one
        return select_one('magic_link_used_jti', {'jti': jti}) is not None
    except Exception:
        return False


def mark_jti_used(jti: str, token: str, client_ip: str | None) -> bool:
    """Vraca True ako je jti novi (uspesno rezervisan), False ako je vec bio."""
    if not jti: return False
    try:
        from data_layer import insert
        insert('magic_link_used_jti', {
            'jti': jti,
            'token': token,
            'used_at': _now_iso(),
            'client_ip': (client_ip or '')[:64],
        })
        return True
    except Exception as e:
        # Postgres unique_violation → jti vec upotrebljen
        if '23505' in str(e) or 'duplicate' in str(e).lower():
            return False
        logger.info(f'mark_jti_used({jti}): {e}')
        # Best-effort: pretpostavi da je nov ako Supabase pukne, nastavi
        return True


# --------- AUDIT LOG ---------------------------------------------------

def append_audit(action: str, module: str, details: str,
                 user_id: str | None = None, username: str | None = None,
                 ip_address: str | None = None, is_suspicious: bool = False,
                 location: str | None = None, user_agent: str | None = None):
    """Best-effort audit append u Supabase. Nikad ne baca — audit ne sme
    da srusi glavni request."""
    try:
        from data_layer import insert
        insert('audit_logs', {
            'sync_id': str(uuid.uuid4()),
            'user_id': user_id,
            'username': username,
            'action': action,
            'module': module,
            'details': details[:2000] if details else '',
            'timestamp': _now_iso(),
            'is_suspicious': bool(is_suspicious),
            'ip_address': ip_address,
            'user_agent': (user_agent or '')[:200],
            'location': location,
        })
    except Exception as e:
        logger.debug(f'append_audit skipped: {type(e).__name__}: {str(e)[:120]}')
