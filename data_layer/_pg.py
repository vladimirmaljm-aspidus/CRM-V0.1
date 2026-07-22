"""Postgres backend — direktna psycopg konekcija (traži TCP outbound).

Aktivira se sa `DB_BACKEND=postgres`. Koristimo `psycopg[binary,pool]`
sa connection pool-om, tako da svaka HTTP zahtev-thread dobija spremnu
konekciju bez TLS handshake overhead-a.

Isti interfejs kao REST backend — može se prebacivati bez izmene poziva.

Prednosti nad REST backend-om:
  * ~10× brže po zahtevu (direktan TCP + prepared statements)
  * Kompleksni SQL bez RPC funkcija
  * Prave transakcije preko više tabela
  * Nema PostgREST rate limita

Zahtev: hosting koji dozvoljava outbound TCP na Supabase pooler
(port 6543) ili direktnu bazu (port 5432). PythonAnywhere Hacker plan
i skuplji podržavaju ovo; Free plan ne.
"""
from __future__ import annotations
import json
import os
import threading
from typing import Any

_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as e:
            raise RuntimeError(
                "psycopg[pool] nije instaliran. "
                "Pokreni: pip install 'psycopg[binary,pool]>=3.1'"
            ) from e

        db_url = os.environ.get("SUPABASE_DB_URL", "").strip()
        if not db_url:
            raise RuntimeError(
                "SUPABASE_DB_URL nije postavljen u .env. Vidi "
                "docs/SETUP_PYTHONANYWHERE.md korak 1B."
            )
        # min_size=1 da se ne pravi bespotrebno mnogo idle konekcija na
        # Supabase Free planu (koji ima limit konkurentnih konekcija).
        # max_size=10 je dovoljno za portal saobraćaj koji nije baš 100 rps.
        _pool = ConnectionPool(
            conninfo=db_url,
            min_size=1,
            max_size=10,
            timeout=15.0,           # čekaj max 15s za slobodnu konekciju
            open=True,
            kwargs={"autocommit": False},
        )
        return _pool


class PgBackend:
    name = "postgres"

    def __init__(self):
        # Odmah pokušaj da otvoriš pool — greška odmah nego skrivena.
        _get_pool()

    # ---- helpers --------------------------------------------------------

    def _build_where(self, filters: dict | None):
        """Vraća (sql_where, params_list). Prazan filter → ('', [])."""
        if not filters:
            return "", []
        parts, params = [], []
        for col, val in filters.items():
            if isinstance(val, tuple) and len(val) == 2 and isinstance(val[0], str):
                op, v = val
                op = op.lower()
                if op == "eq":     parts.append(f'"{col}" = %s'); params.append(v)
                elif op == "neq":  parts.append(f'"{col}" <> %s'); params.append(v)
                elif op == "gt":   parts.append(f'"{col}" > %s'); params.append(v)
                elif op == "gte":  parts.append(f'"{col}" >= %s'); params.append(v)
                elif op == "lt":   parts.append(f'"{col}" < %s'); params.append(v)
                elif op == "lte":  parts.append(f'"{col}" <= %s'); params.append(v)
                elif op == "like": parts.append(f'"{col}" LIKE %s'); params.append(v)
                elif op == "ilike":parts.append(f'"{col}" ILIKE %s'); params.append(v)
                elif op == "in":
                    vals = list(v)
                    if not vals:
                        # IN () je nevalidan SQL — vrati where koji ne matchuje ništa
                        parts.append("FALSE")
                    else:
                        placeholders = ",".join(["%s"] * len(vals))
                        parts.append(f'"{col}" IN ({placeholders})')
                        params.extend(vals)
                elif op == "is":
                    if v is None: parts.append(f'"{col}" IS NULL')
                    else:         parts.append(f'"{col}" IS %s'); params.append(v)
                else:
                    raise ValueError(f"Nepoznat operator '{op}'")
            else:
                parts.append(f'"{col}" = %s')
                params.append(val)
        return " WHERE " + " AND ".join(parts), params

    def _build_order(self, order):
        if not order:
            return ""
        items = [order] if isinstance(order, str) else list(order)
        pieces = []
        for it in items:
            if not it: continue
            if it.startswith("-"):
                pieces.append(f'"{it[1:]}" DESC')
            else:
                pieces.append(f'"{it}" ASC')
        return " ORDER BY " + ", ".join(pieces) if pieces else ""

    def _rows_to_dicts(self, cur):
        cols = [d.name for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _jsonify(self, row: dict) -> dict:
        """Serialise dict/list Python vrednosti u JSON string za JSONB kolone
        koje psycopg neće da adaptira samostalno."""
        out = {}
        for k, v in row.items():
            if isinstance(v, (dict, list)):
                out[k] = json.dumps(v, ensure_ascii=False)
            else:
                out[k] = v
        return out

    # ---- CRUD -----------------------------------------------------------

    def select(self, table, filters=None, columns="*", order=None, limit=None):
        pool = _get_pool()
        where, params = self._build_where(filters)
        order_sql = self._build_order(order)
        limit_sql = f" LIMIT {int(limit)}" if limit is not None else ""
        sql = f'SELECT {columns} FROM "{table}"{where}{order_sql}{limit_sql}'
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return self._rows_to_dicts(cur)

    def select_one(self, table, filters, columns="*"):
        rows = self.select(table, filters, columns, limit=1)
        return rows[0] if rows else None

    def insert(self, table, row):
        pool = _get_pool()
        row = self._jsonify(row)
        cols = list(row.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        col_list = ", ".join(f'"{c}"' for c in cols)
        sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders}) RETURNING *'
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, [row[c] for c in cols])
                out = self._rows_to_dicts(cur)
                conn.commit()
                return out[0] if out else row

    def update(self, table, filters, patch):
        pool = _get_pool()
        patch = self._jsonify(patch)
        set_cols = list(patch.keys())
        set_sql = ", ".join(f'"{c}" = %s' for c in set_cols)
        set_params = [patch[c] for c in set_cols]
        where, where_params = self._build_where(filters)
        sql = f'UPDATE "{table}" SET {set_sql}{where} RETURNING *'
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, set_params + where_params)
                out = self._rows_to_dicts(cur)
                conn.commit()
                return out

    def upsert(self, table, row, on_conflict="id"):
        pool = _get_pool()
        row = self._jsonify(row)
        cols = list(row.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        col_list = ", ".join(f'"{c}"' for c in cols)
        update_set = ", ".join(
            f'"{c}" = EXCLUDED."{c}"' for c in cols if c != on_conflict
        )
        sql = (
            f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders}) '
            f'ON CONFLICT ("{on_conflict}") DO UPDATE SET {update_set} RETURNING *'
        )
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, [row[c] for c in cols])
                out = self._rows_to_dicts(cur)
                conn.commit()
                return out[0] if out else row

    def delete(self, table, filters):
        pool = _get_pool()
        where, params = self._build_where(filters)
        sql = f'DELETE FROM "{table}"{where}'
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                n = cur.rowcount
                conn.commit()
                return int(n or 0)

    def count(self, table, filters=None):
        pool = _get_pool()
        where, params = self._build_where(filters)
        sql = f'SELECT COUNT(*) FROM "{table}"{where}'
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return int(cur.fetchone()[0])

    def rpc(self, name, args=None):
        # RPC funkcija — poziv se translira u SELECT * FROM fn(args...)
        args = args or {}
        pool = _get_pool()
        keys = list(args.keys())
        # Named args u pgcall: fn(key1 := %s, key2 := %s)
        arg_list = ", ".join(f"{k} := %s" for k in keys)
        sql = f'SELECT * FROM {name}({arg_list})'
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, [args[k] for k in keys])
                return self._rows_to_dicts(cur)

    # ---- health ---------------------------------------------------------

    def health(self) -> dict:
        pool = _get_pool()
        cnt = self.count("partners")
        stats = pool.get_stats()
        return {
            "partners_count": cnt,
            "pool_size": stats.get("pool_size"),
            "pool_available": stats.get("pool_available"),
        }
