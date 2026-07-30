"""In-memory mock backend za testove i lokalni razvoj bez Supabase-a.

Cuva redove u dict[table -> list[dict]] i implementira isti interfejs kao
`_rest.RestBackend` (select/insert/update/upsert/delete/count/rpc/health).
Dovoljno je da svi routes koji sada idu kroz `data_layer` mogu da rade
bez Supabase env vars (obavezno za CI/testove).

Nije thread-safe za paralelne upise — dovoljno je za test client. U
produkciji koristi REST backend.
"""
from __future__ import annotations
import copy
import threading
import time


class MockBackend:
    name = "mock"

    def __init__(self):
        self._tables: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def _rows(self, table: str) -> list[dict]:
        if table not in self._tables:
            self._tables[table] = []
        return self._tables[table]

    def _match(self, row: dict, filters: dict | None) -> bool:
        if not filters:
            return True
        for col, val in filters.items():
            if isinstance(val, tuple) and len(val) == 2 and isinstance(val[0], str):
                op, v = val
                op = op.lower()
                cv = row.get(col)
                if op == "eq" and cv != v: return False
                elif op == "neq" and cv == v: return False
                elif op == "gt"  and not (cv is not None and cv > v):  return False
                elif op == "gte" and not (cv is not None and cv >= v): return False
                elif op == "lt"  and not (cv is not None and cv < v):  return False
                elif op == "lte" and not (cv is not None and cv <= v): return False
                elif op == "like":
                    pat = str(v).replace('%', '')
                    if pat not in (str(cv) if cv is not None else ''): return False
                elif op == "ilike":
                    pat = str(v).replace('%', '').lower()
                    if pat not in (str(cv).lower() if cv is not None else ''): return False
                elif op == "in":
                    if cv not in list(v): return False
                elif op == "is":
                    if cv is not v and cv != v: return False
            else:
                if row.get(col) != val:
                    return False
        return True

    def _apply_order(self, rows: list[dict], order):
        if not order:
            return rows
        items = [order] if isinstance(order, str) else list(order)
        for it in reversed(items):
            if not it: continue
            desc = it.startswith('-')
            col = it[1:] if desc else it
            rows = sorted(rows, key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        return rows

    # ---- CRUD ---------------------------------------------------------

    def select(self, table, filters=None, columns="*", order=None, limit=None):
        with self._lock:
            rows = [copy.deepcopy(r) for r in self._rows(table) if self._match(r, filters)]
        rows = self._apply_order(rows, order)
        if limit is not None:
            rows = rows[:int(limit)]
        return rows

    def select_one(self, table, filters, columns="*"):
        rows = self.select(table, filters, columns, limit=1)
        return rows[0] if rows else None

    def insert(self, table, row):
        with self._lock:
            self._rows(table).append(copy.deepcopy(row))
            return copy.deepcopy(row)

    def update(self, table, filters, patch):
        updated = []
        with self._lock:
            for r in self._rows(table):
                if self._match(r, filters):
                    r.update(copy.deepcopy(patch))
                    updated.append(copy.deepcopy(r))
        return updated

    def upsert(self, table, row, on_conflict="id"):
        pk = row.get(on_conflict)
        with self._lock:
            for i, existing in enumerate(self._rows(table)):
                if existing.get(on_conflict) == pk:
                    merged = copy.deepcopy(existing)
                    merged.update(copy.deepcopy(row))
                    self._rows(table)[i] = merged
                    return copy.deepcopy(merged)
            self._rows(table).append(copy.deepcopy(row))
            return copy.deepcopy(row)

    def delete(self, table, filters):
        with self._lock:
            before = len(self._rows(table))
            self._tables[table] = [r for r in self._rows(table) if not self._match(r, filters)]
            return before - len(self._rows(table))

    def count(self, table, filters=None):
        with self._lock:
            return sum(1 for r in self._rows(table) if self._match(r, filters))

    def rpc(self, name, args=None):
        # Testovi ne pozivaju RPC — vrati [] da ne bi rusio
        return []

    def health(self) -> dict:
        return {"tables": len(self._tables),
                "rows": sum(len(v) for v in self._tables.values()),
                "ts": int(time.time())}

    # ---- utility za testove --------------------------------------------

    def _reset(self):
        with self._lock:
            self._tables.clear()

    def _seed(self, table: str, rows: list[dict]):
        with self._lock:
            self._tables.setdefault(table, []).extend(copy.deepcopy(rows))
