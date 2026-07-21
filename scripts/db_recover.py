#!/usr/bin/env python3
"""HITAN DB RECOVERY — spašava podatke iz malformed SQLite baze.

Uzrok problema: SQLite `database disk image is malformed` — najčešće
posledica prekinutog upisa (power/kill) tokom WAL commit-a, ili istovremenog
upisa iz dva procesa preko različitih file system-a (može se desiti na
deljenim hosting servisima).

Ova skripta ne diramo tekst PostgreSQL — samo SQLite recovery kroz Python.
Ne treba spolja instalirati ništa.

Kako se koristi (na PythonAnywhere Bash konzoli):

    cd ~/mysite/CRM
    # 1. Zaustavi web app (kroz Web tab u PA UI-u — klik Reload / Stop)

    # 2. Backup postojeće (verovatno oštećene) baze
    mkdir -p ~/emergency_backup
    cp /home/aspidus/aspidus_crm.db ~/emergency_backup/aspidus_crm.db.malformed.$(date +%s)

    # 3. Pokreni recovery
    python3 scripts/db_recover.py /home/aspidus/aspidus_crm.db /tmp/aspidus_crm.RECOVERED.db

    # 4. Ako je izveštaj OK (integrity: ok), zameni:
    cp /tmp/aspidus_crm.RECOVERED.db /home/aspidus/aspidus_crm.db

    # 5. Reload web app kroz PA UI

Skripta:
  • Kopira sve READABLE redove iz svake tabele
  • Fallback na per-rowid brute-force za corrupted tabele
  • Bypass UTF-8 error kroz text_factory=bytes
  • VACUUM na kraju za defragmentaciju
  • Vraća exit code 0 samo ako je nova baza integritet = 'ok'
"""

import os
import sqlite3
import sys


def recover(src_path, dst_path, verbose=True):
    if not os.path.exists(src_path):
        print(f'✗ Izvor ne postoji: {src_path}')
        return False

    if os.path.exists(dst_path):
        os.remove(dst_path)

    # Konektuj oba sa text_factory=bytes da izbegnemo UTF-8 crash na oštećenom tekst polju
    sc = sqlite3.connect(src_path, timeout=30)
    sc.text_factory = bytes
    dc = sqlite3.connect(dst_path, timeout=30)
    dc.text_factory = bytes
    dc.execute('PRAGMA journal_mode=WAL')

    # 1. Kopiraj sve CREATE TABLE naredbe
    sc2 = sqlite3.connect(src_path, timeout=30)
    sc2.text_factory = bytes
    try:
        schema = sc2.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    except Exception as e:
        print(f'✗ Ne mogu da pročitam sqlite_master: {e}')
        return False

    for (name, sql) in schema:
        if sql:
            sql_str = sql.decode() if isinstance(sql, bytes) else sql
            try:
                dc.execute(sql_str)
            except Exception as e:
                if verbose:
                    print(f'  schema {name}: {e}')

    # 2. Rekonstruiši indekse (bezbedno)
    try:
        indices = sc2.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        ).fetchall()
    except Exception:
        indices = []

    # 3. Kopiraj podatke, tabela po tabela
    stats = {}
    for (name, _sql) in schema:
        name_str = name.decode() if isinstance(name, bytes) else name
        try:
            cols = sc.execute(f'PRAGMA table_info({name_str})').fetchall()
            col_count = len(cols)
        except Exception as e:
            print(f'  {name_str}: PRAGMA table_info fail: {e}')
            stats[name_str] = ('SKIP', 0, 0)
            continue

        if col_count == 0:
            stats[name_str] = ('EMPTY_SCHEMA', 0, 0)
            continue

        placeholders = ','.join(['?'] * col_count)
        ok = 0
        err = 0
        method = 'SELECT'

        # Strategija 1: SELECT * FROM tbl
        try:
            rows = sc.execute(f'SELECT * FROM {name_str}').fetchall()
            for row in rows:
                try:
                    dc.execute(
                        f'INSERT OR IGNORE INTO {name_str} VALUES ({placeholders})', row
                    )
                    ok += 1
                except Exception:
                    err += 1
            dc.commit()
        except Exception:
            # Strategija 2: brute per-rowid (za corrupted tabelu)
            method = 'per-rowid'
            for rid in range(1, 500_001):
                try:
                    row = sc.execute(
                        f'SELECT * FROM {name_str} WHERE _rowid_=?', (rid,)
                    ).fetchone()
                    if row is None:
                        continue
                    dc.execute(
                        f'INSERT OR IGNORE INTO {name_str} VALUES ({placeholders})', row
                    )
                    ok += 1
                except Exception:
                    err += 1
            dc.commit()

        stats[name_str] = (method, ok, err)
        if verbose:
            print(f'  {name_str}: OK={ok:6d}  FAIL={err:4d}  ({method})')

    # 4. Vrati indekse
    for (sql,) in indices:
        sql_str = sql.decode() if isinstance(sql, bytes) else sql
        try:
            dc.execute(sql_str)
        except Exception:
            pass
    dc.commit()

    # 5. VACUUM za defragmentaciju
    try:
        dc.execute('VACUUM')
        dc.commit()
    except Exception as e:
        print(f'  VACUUM: {e}')

    # 6. Verifikacija integriteta
    try:
        integ = dc.execute('PRAGMA integrity_check').fetchall()
    except Exception as e:
        print(f'✗ integrity_check pao: {e}')
        return False

    integ_str = [(x[0].decode() if isinstance(x[0], bytes) else x[0]) for x in integ[:5]]

    sc.close()
    sc2.close()
    dc.close()
    # Veličinu čitamo POSLE zatvaranja da WAL bude checkpoint-ovan u glavni fajl.
    dst_size = os.path.getsize(dst_path)

    print(f'\n{"=" * 60}')
    print(f'  Recovery gotov')
    print(f'  Izvor:     {src_path} ({os.path.getsize(src_path)} bytes)')
    print(f'  Rezultat:  {dst_path} ({dst_size} bytes)')
    print(f'  Integritet: {integ_str}')
    print(f'{"=" * 60}')

    return integ_str == ['ok']


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        print('Upotreba: python3 db_recover.py <src.db> <dst.db>')
        sys.exit(2)
    src, dst = sys.argv[1], sys.argv[2]
    success = recover(src, dst)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
