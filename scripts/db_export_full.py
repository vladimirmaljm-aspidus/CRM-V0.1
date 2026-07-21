#!/usr/bin/env python3
"""
FULL BACKUP — Aspidus CRM

Kreira KOMPLETAN backup u jednom .tar.gz fajlu koji uključuje:
  • Sve 3 SQLite baze (aspidus_crm.db, aspidus_portal.db, aspidus_audit.db)
    → svaka se prvo kopira preko sqlite3 backup API-ja (konzistentna kopija
      i tokom rada aplikacije), zatim VACUUM u tmp fajl (defragmentacija),
      pa se u arhivu ubacuje čista .db (bez -wal/-shm ostataka).
  • uploads/ (svi klijentski dokumenti/fajlovi)
  • portal_uploads/ (KYC dokumenti klijenata sa portala)
  • instance/secret.key (potpisivanje sesija — kritično za continuity)
  • vault.key (Fernet ključ — BEZ NJEGA se dešifrovani SMTP/API ključevi ne mogu čitati)
  • meta.json (verzija app-a, timestamp, integrity_check rezultat, row count po tabeli)
  • RESTORE.md (uputstvo za oporavak — u samom ZIP-u, tako da ne može da se izgubi)

Poziva se ručno:
    python scripts/db_export_full.py                    # snima u ./backups/
    python scripts/db_export_full.py --out /tmp         # snima u /tmp
    python scripts/db_export_full.py --stdout > b.tgz   # cev (za HTTP endpoint)

Ne zahteva sqlite3 CLI — koristi Python-only sqlite3 modul.
Ne oslanja se na spoljne biblioteke izvan CRM requirements.txt.
"""
from __future__ import annotations
import argparse
import io
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
import hashlib
from typing import Optional

# Import config direktno — radi i kada je skript pokrenut iz repo root-a
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Ne importujemo config.py — on povlači cryptography koji na produkciji
# ne mora biti dostupan CLI okruženju. Umesto toga rekonstruišemo iste
# putanje istom logikom (env DATA_DIR ili BASE_DIR).
DATA_DIR = os.getenv("DATA_DIR", _ROOT)
INSTANCE_DIR = os.path.join(DATA_DIR, "instance")
DB_FILE = os.path.join(DATA_DIR, "aspidus_crm.db")
PORTAL_DB_FILE = os.path.join(DATA_DIR, "aspidus_portal.db")
AUDIT_DB_FILE = os.path.join(DATA_DIR, "aspidus_audit.db")
UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")
PORTAL_UPLOAD_FOLDER = os.path.join(DATA_DIR, "portal_uploads")
KEY_FILE = os.path.join(DATA_DIR, "vault.key")
SECRET_KEY_FILE = os.path.join(INSTANCE_DIR, "secret.key")

APP_VERSION = os.getenv("APP_VERSION", "1.0.0")


def _log(msg: str, quiet: bool = False):
    if not quiet:
        print(msg, file=sys.stderr, flush=True)


def _sqlite_online_backup(src_path: str, dst_path: str) -> None:
    """
    Kopira SQLite bazu preko oficijalnog backup API-ja.
    Ovo je JEDINI ispravan način da se napravi konzistentna kopija baze
    dok aplikacija radi (WAL fajl može biti aktivan). NE sme se koristiti
    obični shutil.copy — može uhvatiti bazu u nekonzistentnom stanju.
    """
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, timeout=30.0)
    try:
        dst = sqlite3.connect(dst_path, timeout=30.0)
        try:
            with dst:
                src.backup(dst, pages=1000, sleep=0.001)
        finally:
            dst.close()
    finally:
        src.close()


def _vacuum(path: str) -> None:
    """Defragmentacija — smanjuje veličinu i sređuje strukturu."""
    conn = sqlite3.connect(path, timeout=30.0)
    try:
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()


def _integrity(path: str) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30.0)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row[0] if row else "unknown"
    finally:
        conn.close()


def _list_tables_with_counts(path: str) -> dict:
    out = {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30.0)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (tname,) in rows:
            try:
                cnt = conn.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
            except Exception as e:
                cnt = f"ERROR: {e}"
            out[tname] = cnt
    finally:
        conn.close()
    return out


def _add_dir_to_tar(tar: tarfile.TarFile, src_dir: str, arc_prefix: str, quiet: bool = False) -> tuple[int, int]:
    """
    Dodaje ceo folder u tar. Vraća (broj fajlova, ukupna veličina u bajtovima).
    Bezbedno preskače fajlove koji ne mogu da se pročitaju.
    """
    n_files = 0
    total_bytes = 0
    if not os.path.isdir(src_dir):
        return (0, 0)
    for root, _, files in os.walk(src_dir):
        for fname in files:
            src_path = os.path.join(root, fname)
            rel = os.path.relpath(src_path, src_dir)
            arcname = os.path.join(arc_prefix, rel)
            try:
                size = os.path.getsize(src_path)
                tar.add(src_path, arcname=arcname, recursive=False)
                n_files += 1
                total_bytes += size
            except Exception as e:
                _log(f"  [WARN] Ne mogu dodati {src_path}: {e}", quiet)
    return (n_files, total_bytes)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


RESTORE_MD = """# ASPIDUS CRM — Full Backup restore

Ovaj arhiv je KOMPLETAN snapshot Aspidus CRM instance.

## Sadržaj arhive

```
meta.json                       # metapodaci, row-count po tabeli, SHA256 baza
databases/aspidus_crm.db        # glavna baza (partneri, ponude, dilovi, …)
databases/aspidus_portal.db     # portal (klijentske sesije, KYC, magic-linkovi)
databases/aspidus_audit.db      # audit log
keys/vault.key                  # Fernet ključ (dešifruje SMTP/API u settings)
keys/secret.key                 # SECRET_KEY (sesijski kolačići)
uploads/                        # dokumenti otpremljeni iz CRM-a
portal_uploads/                 # KYC dokumenti klijenata sa portala
```

## KAKO VRATITI (restore)

**Opcija A — komanda (preporučeno):**

```bash
python scripts/db_import_full.py --archive ASPIDUS_FULL_BACKUP_YYYY-MM-DD.tar.gz --confirm
```

Skript automatski:
1. Zaustavlja se ako je aplikacija aktivna (dodajte --force da preskočite).
2. Kvarantinira postojeće baze/ključeve pre restore-a
   (postojeći fajlovi se ne brišu, samo preimenuju u `*.pre_restore.<timestamp>`).
3. Vraća sve baze, ključeve i uploads foldere.
4. Verifikuje integritet baze i upoređuje row-count sa meta.json.

**Opcija B — ručno:**

1. `tar -xzf ASPIDUS_FULL_BACKUP_YYYY-MM-DD.tar.gz`
2. Zaustavite aplikaciju.
3. Prekopirajte:
   - `databases/*.db` → `$DATA_DIR/`
   - `keys/vault.key` → `$DATA_DIR/vault.key`
   - `keys/secret.key` → `$DATA_DIR/instance/secret.key`
   - `uploads/*` → `$DATA_DIR/uploads/`
   - `portal_uploads/*` → `$DATA_DIR/portal_uploads/`
4. Pokrenite aplikaciju. Pri startu se automatski proverava integrity_check
   i pravi WAL fajlovi kad zatreba.

## KAKO PRENETI NA DRUGI SERVER

Isto kao restore, samo na drugom serveru. Ako se `DATA_DIR` razlikuje,
postavite env `DATA_DIR=/moja/nova/putanja` pre pokretanja skripta.

## VAŽNO — vault.key

`vault.key` sadrži AES-128 ključ kojim su šifrovana OSETLJIVA podešavanja
(SMTP lozinke, API tokeni, delovi KYC-a). Bez ovog fajla ta polja se
NE mogu dešifrovati. Ako ga izgubite, pripremite se da ručno unesete
sve API ključeve iznova. Zato je uključen u backup — nikad ga ne delite
zajedno sa backupom prema neovlašćenoj strani.

## MIGRACIJA NA POSTGRESQL

Postoji poseban skript koji radi 1:1 mapiranje SQLite → PostgreSQL:

```bash
python scripts/db_migrate_to_postgres.py \\
    --sqlite databases/aspidus_crm.db \\
    --pg "postgresql://user:pass@host:5432/aspidus"
```

Skript ne briše ništa iz Postgres baze — samo dodaje/kopira. Sami odlučite
da li ćete unapred da napravite `TRUNCATE` na target bazi.
"""


def build_backup(out_path: Optional[str], out_stream=None, quiet: bool = False) -> str:
    """
    Pravi full backup arhivu. Ako je out_stream zadat, upisuje u njega
    (koristi se za HTTP streaming). U suprotnom snima u out_path fajl.
    Vraća putanju do fajla ILI 'stream' ako je pisao u stream.
    """
    started = time.time()
    ts = time.strftime("%Y-%m-%d_%H-%M-%S", time.gmtime())

    if out_stream is None:
        if out_path is None:
            out_path = os.path.join(_ROOT, "backups", f"ASPIDUS_FULL_BACKUP_{ts}.tar.gz")
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        tar_fp = open(out_path, "wb")
    else:
        tar_fp = out_stream

    # 1) Backup + VACUUM sve baze u tmp direktorijum (radi cistoce)
    tmp = tempfile.mkdtemp(prefix="aspidus_backup_")
    try:
        db_specs = [
            ("aspidus_crm.db", DB_FILE),
            ("aspidus_portal.db", PORTAL_DB_FILE),
            ("aspidus_audit.db", AUDIT_DB_FILE),
        ]

        db_meta = {}
        for arcname, src in db_specs:
            if not os.path.exists(src):
                _log(f"  [SKIP] {arcname} — ne postoji ({src})", quiet)
                db_meta[arcname] = {"present": False}
                continue
            _log(f"  [DB]   {arcname}: online backup → tmp", quiet)
            tmp_db = os.path.join(tmp, arcname)
            _sqlite_online_backup(src, tmp_db)
            _log(f"  [DB]   {arcname}: VACUUM", quiet)
            try:
                _vacuum(tmp_db)
            except Exception as e:
                _log(f"  [WARN] VACUUM {arcname} propao: {e} (nastavlja se sa raw kopijom)", quiet)
            integ = _integrity(tmp_db)
            tables = _list_tables_with_counts(tmp_db)
            db_meta[arcname] = {
                "present": True,
                "size_bytes": os.path.getsize(tmp_db),
                "integrity_check": integ,
                "sha256": _sha256_file(tmp_db),
                "tables": tables,
                "table_count": len(tables),
                "row_total": sum(v for v in tables.values() if isinstance(v, int)),
            }

        # 2) Napravi tar.gz
        with tarfile.open(fileobj=tar_fp, mode="w:gz", compresslevel=6) as tar:
            # 2a) baze
            for arcname, _src in db_specs:
                tmp_db = os.path.join(tmp, arcname)
                if os.path.exists(tmp_db):
                    tar.add(tmp_db, arcname=f"databases/{arcname}")

            # 2b) uploads
            _log(f"  [FILES] uploads/", quiet)
            u_n, u_b = _add_dir_to_tar(tar, UPLOAD_FOLDER, "uploads", quiet)
            _log(f"          {u_n} fajlova, {u_b/1024/1024:.2f} MB", quiet)

            _log(f"  [FILES] portal_uploads/", quiet)
            p_n, p_b = _add_dir_to_tar(tar, PORTAL_UPLOAD_FOLDER, "portal_uploads", quiet)
            _log(f"          {p_n} fajlova, {p_b/1024/1024:.2f} MB", quiet)

            # 2c) ključevi (kritični za continuity)
            keys_meta = {}
            for kname, ksrc in [("vault.key", KEY_FILE), ("secret.key", SECRET_KEY_FILE)]:
                if os.path.exists(ksrc):
                    tar.add(ksrc, arcname=f"keys/{kname}")
                    keys_meta[kname] = {"present": True, "size": os.path.getsize(ksrc)}
                else:
                    keys_meta[kname] = {"present": False}
                    _log(f"  [SKIP] keys/{kname} — ne postoji", quiet)

            # 2d) meta.json
            meta = {
                "backup_format_version": 1,
                "app_version": APP_VERSION,
                "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "created_by": os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
                "hostname": os.uname().nodename if hasattr(os, "uname") else "unknown",
                "data_dir": DATA_DIR,
                "databases": db_meta,
                "uploads": {"file_count": u_n, "bytes": u_b},
                "portal_uploads": {"file_count": p_n, "bytes": p_b},
                "keys": keys_meta,
                "python_version": sys.version.split()[0],
            }
            meta_bytes = json.dumps(meta, indent=2, ensure_ascii=False).encode("utf-8")
            info = tarfile.TarInfo(name="meta.json")
            info.size = len(meta_bytes)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(meta_bytes))

            # 2e) RESTORE.md
            restore_bytes = RESTORE_MD.encode("utf-8")
            info = tarfile.TarInfo(name="RESTORE.md")
            info.size = len(restore_bytes)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(restore_bytes))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if out_stream is None:
            tar_fp.close()

    elapsed = time.time() - started
    if out_stream is None:
        size_mb = os.path.getsize(out_path) / 1024 / 1024
        _log(f"\n✓ Backup gotov za {elapsed:.1f}s — {out_path} ({size_mb:.2f} MB)", quiet)
        return out_path
    else:
        _log(f"\n✓ Backup stream gotov za {elapsed:.1f}s", quiet)
        return "stream"


def main():
    ap = argparse.ArgumentParser(description="Aspidus CRM — Full Backup")
    ap.add_argument("--out", default=None, help="Izlazna putanja (podrazumevano ./backups/…)")
    ap.add_argument("--stdout", action="store_true", help="Pošalji tar.gz na stdout umesto u fajl")
    ap.add_argument("--quiet", action="store_true", help="Ne štampaj progress")
    args = ap.parse_args()

    if args.stdout:
        # Piše binarni tar.gz na stdout; log ide na stderr
        build_backup(out_path=None, out_stream=sys.stdout.buffer, quiet=args.quiet)
    else:
        build_backup(out_path=args.out, quiet=args.quiet)


if __name__ == "__main__":
    main()
