#!/usr/bin/env python3
"""
FULL RESTORE — Aspidus CRM

Vraća sve iz .tar.gz arhive napravljene sa db_export_full.py:
  • Baze (aspidus_crm.db, aspidus_portal.db, aspidus_audit.db)
  • uploads/ i portal_uploads/
  • vault.key + secret.key

Bezbednosne mere pre restore-a:
  1. Kvarantinira POSTOJEĆE fajlove (rename u *.pre_restore.<ts>)
     — NE briše ništa, ako restore krene loše sve se može ručno vratiti.
  2. Ekstraktuje arhivu u tmp folder, verifikuje integrity_check svake baze,
     upoređuje row-count sa meta.json.
  3. Tek nakon uspešne verifikacije premešta u ciljne putanje.
  4. Piše restore log u backups/restore_<ts>.log.

Zahteva potvrdu (--confirm) jer prepisuje bazu.
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DATA_DIR = os.getenv("DATA_DIR", _ROOT)
INSTANCE_DIR = os.path.join(DATA_DIR, "instance")
DB_MAP = {
    "aspidus_crm.db": os.path.join(DATA_DIR, "aspidus_crm.db"),
    "aspidus_portal.db": os.path.join(DATA_DIR, "aspidus_portal.db"),
    "aspidus_audit.db": os.path.join(DATA_DIR, "aspidus_audit.db"),
}
KEYS_MAP = {
    "vault.key": os.path.join(DATA_DIR, "vault.key"),
    "secret.key": os.path.join(INSTANCE_DIR, "secret.key"),
}
UPLOAD_DIRS = [
    ("uploads", os.path.join(DATA_DIR, "uploads")),
    ("portal_uploads", os.path.join(DATA_DIR, "portal_uploads")),
]


def _log(msg: str, log_fh=None):
    print(msg, file=sys.stderr, flush=True)
    if log_fh:
        log_fh.write(msg + "\n")
        log_fh.flush()


def _safe_extract(tar: tarfile.TarFile, dest_dir: str):
    """
    Bezbedno ekstraktuje tar. Odbija path-traversal (../).
    """
    dest_abs = os.path.abspath(dest_dir)
    for m in tar.getmembers():
        target = os.path.abspath(os.path.join(dest_dir, m.name))
        if not target.startswith(dest_abs + os.sep) and target != dest_abs:
            raise RuntimeError(f"Odbijen tar entry (path traversal): {m.name}")
        if m.issym() or m.islnk():
            raise RuntimeError(f"Odbijen tar entry (symlink): {m.name}")
    tar.extractall(dest_dir)


def _integrity(path: str) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30.0)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row[0] if row else "unknown"
    finally:
        conn.close()


def _table_counts(path: str) -> dict:
    out = {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30.0)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for (tname,) in rows:
            try:
                out[tname] = conn.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
            except Exception as e:
                out[tname] = f"ERR:{e}"
    finally:
        conn.close()
    return out


def _quarantine(path: str, ts: str) -> str | None:
    """Preimenuje postojeći fajl u *.pre_restore.<ts> i briše -wal/-shm ostatke."""
    if not os.path.exists(path):
        return None
    dst = f"{path}.pre_restore.{ts}"
    os.rename(path, dst)
    # SQLite prateći fajlovi — moraju u kvarantin sa istim ts-om da nova WAL sesija ne pomeša state
    for sfx in ("-wal", "-shm", "-journal"):
        sp = path + sfx
        if os.path.exists(sp):
            os.rename(sp, f"{sp}.pre_restore.{ts}")
    return dst


def _quarantine_dir(path: str, ts: str) -> str | None:
    if not os.path.isdir(path):
        return None
    dst = f"{path}.pre_restore.{ts}"
    os.rename(path, dst)
    return dst


def restore(archive_path: str, confirm: bool, force: bool, keep_quarantine: bool):
    ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    log_dir = os.path.join(_ROOT, "backups")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"restore_{ts}.log")
    log_fh = open(log_path, "w", encoding="utf-8")

    try:
        _log(f"=== Aspidus CRM restore — {ts} UTC ===", log_fh)
        _log(f"Archive: {archive_path}", log_fh)
        _log(f"DATA_DIR: {DATA_DIR}", log_fh)

        if not os.path.exists(archive_path):
            _log(f"[FATAL] Arhiv ne postoji: {archive_path}", log_fh)
            return 2

        # 1) Ekstraktuj u tmp
        tmp = tempfile.mkdtemp(prefix="aspidus_restore_")
        _log(f"Extract → {tmp}", log_fh)
        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                _safe_extract(tar, tmp)
        except Exception as e:
            _log(f"[FATAL] Extract propao: {e}", log_fh)
            shutil.rmtree(tmp, ignore_errors=True)
            return 3

        # 2) Meta
        meta_path = os.path.join(tmp, "meta.json")
        if not os.path.exists(meta_path):
            _log("[FATAL] meta.json ne postoji u arhivi.", log_fh)
            shutil.rmtree(tmp, ignore_errors=True)
            return 4
        meta = json.load(open(meta_path, "r", encoding="utf-8"))
        _log(f"Backup verzija: {meta.get('backup_format_version')}, "
             f"app: {meta.get('app_version')}, kreiran: {meta.get('created_utc')}", log_fh)

        # 3) Verifikacija baza u tmp-u
        _log("--- Verifikacija baza pre restore-a ---", log_fh)
        all_ok = True
        for arcname in DB_MAP:
            src = os.path.join(tmp, "databases", arcname)
            if not os.path.exists(src):
                m = meta.get("databases", {}).get(arcname, {})
                if m.get("present"):
                    _log(f"[FAIL] {arcname}: meta kaže present=true ali fajl nedostaje.", log_fh)
                    all_ok = False
                else:
                    _log(f"[SKIP] {arcname}: nije bio u backupu.", log_fh)
                continue
            integ = _integrity(src)
            if integ != "ok":
                _log(f"[FAIL] {arcname}: integrity_check={integ}", log_fh)
                all_ok = False
                continue
            counts = _table_counts(src)
            meta_counts = meta.get("databases", {}).get(arcname, {}).get("tables", {})
            mismatch = []
            for t, c in meta_counts.items():
                if isinstance(c, int) and counts.get(t) != c:
                    mismatch.append(f"{t}={counts.get(t)}≠{c}")
            if mismatch:
                _log(f"[WARN] {arcname}: row-count razlike (novi backup fajl?): {', '.join(mismatch[:5])}", log_fh)
            _log(f"[OK]   {arcname}: integrity=ok, tabela={len(counts)}, "
                 f"redova={sum(v for v in counts.values() if isinstance(v, int))}", log_fh)

        if not all_ok and not force:
            _log("[ABORT] Neka baza je pala integrity_check. Koristite --force da nastavite.", log_fh)
            shutil.rmtree(tmp, ignore_errors=True)
            return 5

        if not confirm:
            _log("[DRY-RUN] Verifikacija završena. Za pravi restore dodajte --confirm.", log_fh)
            shutil.rmtree(tmp, ignore_errors=True)
            return 0

        # 4) Kvarantin postojećih fajlova
        _log("--- Kvarantin postojećih fajlova ---", log_fh)
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(INSTANCE_DIR, exist_ok=True)
        quarantined = []
        for arcname, dst in DB_MAP.items():
            q = _quarantine(dst, ts)
            if q:
                _log(f"  {dst} → {q}", log_fh)
                quarantined.append(q)
        for kname, dst in KEYS_MAP.items():
            q = _quarantine(dst, ts)
            if q:
                _log(f"  {dst} → {q}", log_fh)
                quarantined.append(q)
        for _, dst in UPLOAD_DIRS:
            q = _quarantine_dir(dst, ts)
            if q:
                _log(f"  {dst}/ → {q}/", log_fh)
                quarantined.append(q)

        # 5) Premesti nove fajlove
        _log("--- Premeštanje novih fajlova ---", log_fh)
        for arcname, dst in DB_MAP.items():
            src = os.path.join(tmp, "databases", arcname)
            if os.path.exists(src):
                shutil.copy2(src, dst)
                _log(f"  → {dst}", log_fh)
        for kname, dst in KEYS_MAP.items():
            src = os.path.join(tmp, "keys", kname)
            if os.path.exists(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                try:
                    os.chmod(dst, 0o600)
                except Exception:
                    pass
                _log(f"  → {dst}", log_fh)
        for arcname, dst in UPLOAD_DIRS:
            src = os.path.join(tmp, arcname)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
                _log(f"  → {dst}/", log_fh)
            else:
                os.makedirs(dst, exist_ok=True)

        # 6) Post-restore verifikacija
        _log("--- Post-restore verifikacija ---", log_fh)
        for arcname, dst in DB_MAP.items():
            if os.path.exists(dst):
                integ = _integrity(dst)
                counts = _table_counts(dst)
                total = sum(v for v in counts.values() if isinstance(v, int))
                _log(f"[POST] {arcname}: integrity={integ}, redova={total}", log_fh)

        # 7) Cleanup
        if not keep_quarantine and quarantined:
            _log(f"[NOTE] Kvarantinirani fajlovi zadržani ({len(quarantined)}) — obrišite ručno kad potvrdite da sve radi.", log_fh)

        shutil.rmtree(tmp, ignore_errors=True)
        _log(f"✓ Restore završen. Log: {log_path}", log_fh)
        return 0
    finally:
        log_fh.close()


def main():
    ap = argparse.ArgumentParser(description="Aspidus CRM — Full Restore")
    ap.add_argument("--archive", required=True, help="Putanja do .tar.gz backup arhive")
    ap.add_argument("--confirm", action="store_true", help="Bez ovoga radi samo dry-run verifikaciju")
    ap.add_argument("--force", action="store_true", help="Nastavi i ako integrity_check padne")
    ap.add_argument("--keep-quarantine", action="store_true", help="(Kompatibilnost) — kvarantin se uvek čuva, ova opcija sada služi samo za dokumentaciju")
    args = ap.parse_args()
    sys.exit(restore(args.archive, args.confirm, args.force, args.keep_quarantine))


if __name__ == "__main__":
    main()
