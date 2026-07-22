#!/usr/bin/env python3
"""Bezbedan restore Fernet-šifrovanog backup-a nad postojećom (praznom
ili oštećenom) SQLite bazom.

Nastao je iz konkretne situacije: DATA_DIR je premešten u /home/aspidus/mysite
i aplikacija sada koristi novu (praznu) aspidus_crm.db u tom folderu, dok
su pravi podaci ostali u backup fajlovima šifrovanim vault key-em iz
starog radnog foldera.

Karakteristike:
  * Automatski nalazi POSLEDNJI (najsvežiji) .fernet backup za svaku bazu
    (crm/portal/audit) u svim poznatim backup direktorijumima.
  * Dešifruje sa AKTIVNIM vault.key (iz DATA_DIR ili env ENCRYPTION_KEY).
    Ako to ne uspe, pokušava sa svim drugim vault.key fajlovima nadjenim
    u home folderu (podržava scenario kad je DATA_DIR premešten).
  * Verifikuje integritet dekriptovane SQLite baze (PRAGMA integrity_check)
    i broji redove u ključnim tabelama pre nego što odluči da instalira.
  * NE briše postojeću bazu — kvarantinira je kao *.pre_restore.<ts>.
  * Dry-run mode (default) pokazuje šta bi radio bez izmena.

Primer:
    # Dijagnoza — ne dira ništa
    python3.13 scripts/restore_from_fernet_backup.py --dry-run

    # Pravi restore samo crm baze
    python3.13 scripts/restore_from_fernet_backup.py --confirm --only crm

    # Sve tri baze
    python3.13 scripts/restore_from_fernet_backup.py --confirm

    # Iz konkretnog fajla
    python3.13 scripts/restore_from_fernet_backup.py --confirm \\
        --file /home/aspidus/mysite/CRM/backups/aspidus_crm.db.20260722T123721Z.fernet
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path


DB_LABELS = {
    "crm":    "aspidus_crm.db",
    "portal": "aspidus_portal.db",
    "audit":  "aspidus_audit.db",
}


def _load_env():
    """Ucitaj .env iz najverovatnijih putanja."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    home = Path.home()
    for cand in (
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
        home / "mysite" / "CRM" / ".env",
        home / "mysite" / ".env",
    ):
        if cand.exists():
            load_dotenv(cand)
            print(f"✓ .env učitan iz {cand}")
            return


def _find_vault_keys():
    """Vrati sve nadjene vault.key fajlove (aktivni prvo, pa fallback-ovi).
    Format je 44-byte base64 Fernet ključ."""
    keys = []
    seen = set()

    def add(path: Path):
        try:
            if path.exists() and path.stat().st_size >= 40 and path.stat().st_size <= 128:
                data = path.read_bytes().strip()
                if data and data not in seen:
                    seen.add(data)
                    keys.append((str(path), data))
        except Exception:
            pass

    # Iz env-a (najveći prioritet)
    env_k = os.environ.get("ENCRYPTION_KEY", "").strip()
    if env_k:
        raw = env_k.encode() if isinstance(env_k, str) else env_k
        if raw not in seen:
            seen.add(raw)
            keys.append(("<env ENCRYPTION_KEY>", raw))

    home = Path.home()
    data_dir = Path(os.environ.get("DATA_DIR", "")).expanduser() if os.environ.get("DATA_DIR") else None
    candidates = []
    if data_dir:
        candidates.append(data_dir / "vault.key")
    candidates += [
        home / "mysite" / "vault.key",
        home / "mysite" / "CRM" / "vault.key",
        Path.cwd() / "vault.key",
        Path(__file__).resolve().parent.parent / "vault.key",
    ]
    for c in candidates:
        add(c)
    return keys


def _find_backup_dirs():
    """Vrati sve poznate backup direktorijume."""
    home = Path.home()
    data_dir = Path(os.environ.get("DATA_DIR", "")).expanduser() if os.environ.get("DATA_DIR") else None
    dirs = []
    if data_dir:
        dirs.append(data_dir / "backups")
    dirs += [
        home / "mysite" / "backups",
        home / "mysite" / "CRM" / "backups",
        Path.cwd() / "backups",
    ]
    out = []
    seen = set()
    for d in dirs:
        try:
            rp = d.resolve()
            if rp in seen or not d.exists():
                continue
            seen.add(rp)
            out.append(d)
        except Exception:
            pass
    return out


def _list_backups_for(label_prefix: str):
    """Vrati sortirane (najsveži prvo) putanje ka .fernet fajlovima za dati DB label."""
    all_files = []
    for d in _find_backup_dirs():
        try:
            for f in d.iterdir():
                name = f.name
                if name.startswith(label_prefix + ".") and name.endswith(".fernet"):
                    all_files.append(f)
        except Exception:
            continue
    # Sortiraj po mtime, najveći (najsvežiji) prvi
    all_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return all_files


def _decrypt_bytes(cipher_bytes: bytes):
    """Pokušaj dekriptovati bytes sa svakim nadjenim vault key-em.
    Vraća (plaintext_bytes, key_source_str) ili (None, None)."""
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError:
        print("✗ cryptography paket nije instaliran. pip install cryptography")
        return None, None
    for source, key in _find_vault_keys():
        try:
            f = Fernet(key)
            pt = f.decrypt(cipher_bytes)
            return pt, source
        except InvalidToken:
            continue
        except Exception as e:
            print(f"  (ne-Fernet greška za key {source}: {e})")
            continue
    return None, None


def _verify_sqlite(path: Path) -> dict:
    """PRAGMA integrity_check + brojanje ključnih tabela. Vraća {ok, tables, error}."""
    try:
        conn = sqlite3.connect(str(path), timeout=15.0)
        c = conn.cursor()
        c.execute("PRAGMA integrity_check")
        integ = c.fetchone()
        if not integ or integ[0] != "ok":
            conn.close()
            return {"ok": False, "error": f"integrity_check: {integ}"}
        c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {}
        for (name,) in c.fetchall():
            try:
                cc = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()
                tables[name] = int(cc[0]) if cc else 0
            except Exception:
                tables[name] = -1
        conn.close()
        return {"ok": True, "tables": tables}
    except Exception as e:
        return {"ok": False, "error": f"{e.__class__.__name__}: {e}"}


def _target_path(label: str) -> Path:
    """Aktivna putanja u koju treba restore. Uzimamo iz .env DATA_DIR-a."""
    data_dir = os.environ.get("DATA_DIR")
    if not data_dir:
        # Fallback: cwd/../
        data_dir = str(Path(__file__).resolve().parent.parent)
    return Path(data_dir) / DB_LABELS[label]


def restore_one(label: str, backup_file: Path | None, dry_run: bool) -> bool:
    prefix = DB_LABELS[label]
    if backup_file is None:
        candidates = _list_backups_for(prefix)
        if not candidates:
            print(f"\n[{label}] ✗ Nema .fernet backup-a — preskačem.")
            return False
        backup_file = candidates[0]
        others = candidates[1:5]
    else:
        others = []

    print(f"\n── {label.upper()} ──")
    print(f"  Backup:   {backup_file}")
    print(f"  Veličina: {backup_file.stat().st_size:,} bytes")
    print(f"  Datum:    {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(backup_file.stat().st_mtime))}")
    if others:
        print(f"  Ostali kandidati (novije→starije):")
        for o in others:
            print(f"    · {o.name}  ({o.stat().st_size:,} B)")

    # Dekriptuj
    cipher = backup_file.read_bytes()
    plain, source = _decrypt_bytes(cipher)
    if plain is None:
        print(f"  ✗ Dekripcija PROPALA sa svim nadjenim vault ključevima.")
        print(f"    Proveri vault.key — možda je stari ključ na drugoj putanji.")
        return False
    print(f"  ✓ Dekriptovano sa key-em: {source}  ({len(plain):,} bytes plaintext)")

    # Snimi u tmp i verifikuj
    tmp = backup_file.parent / f".restore_tmp_{prefix}"
    tmp.write_bytes(plain)
    v = _verify_sqlite(tmp)
    if not v["ok"]:
        print(f"  ✗ Verifikacija propala: {v['error']}")
        try:
            tmp.unlink()
        except Exception:
            pass
        return False

    partners_hint = ""
    for key_table in ("partners", "products", "deals", "portal_activity_log", "audit_logs"):
        if key_table in v["tables"]:
            partners_hint = f" [{key_table}={v['tables'][key_table]}]" + partners_hint
    print(f"  ✓ SQLite integrity OK  •  {len(v['tables'])} tabela{partners_hint}")

    # Ciljna putanja
    target = _target_path(label)
    print(f"  Ciljna putanja: {target}")
    if not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)

    if dry_run:
        print(f"  → dry-run: ne pomeram u produkciju")
        try:
            tmp.unlink()
        except Exception:
            pass
        return True

    # Kvarantini postojeću bazu
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    if target.exists():
        quarantine = target.with_suffix(target.suffix + f".pre_restore.{ts}")
        try:
            target.rename(quarantine)
            print(f"  ↩ Postojeća baza kvarantinirana → {quarantine}")
        except Exception as e:
            print(f"  ✗ Ne mogu da premestim postojeću bazu: {e}")
            try:
                tmp.unlink()
            except Exception:
                pass
            return False
    # Uklanjanje WAL/SHM fajlova da SQLite ne pokupi stari WAL
    for suf in ("-wal", "-shm", "-journal"):
        p = target.with_name(target.name + suf)
        if p.exists():
            try:
                p.rename(p.with_suffix(p.suffix + f".pre_restore.{ts}"))
            except Exception:
                pass

    # Instaliraj novu bazu
    try:
        tmp.replace(target)
    except Exception as e:
        print(f"  ✗ Move u produkciju propao: {e}")
        return False
    try:
        os.chmod(target, 0o600)
    except Exception:
        pass
    print(f"  ✅ RESTORE ZAVRŠEN → {target}")
    return True


def main():
    ap = argparse.ArgumentParser(description="Restore Fernet-šifrovanih SQLite backup-a")
    ap.add_argument("--dry-run", action="store_true", help="Samo pokaži šta bi radilo")
    ap.add_argument("--confirm", action="store_true", help="Zaista prepiši aktivne baze")
    ap.add_argument("--only", choices=list(DB_LABELS.keys()), help="Restore samo jedne baze (crm/portal/audit)")
    ap.add_argument("--file", type=str, help="Specifičan .fernet fajl (samo sa --only)")
    args = ap.parse_args()

    if not args.dry_run and not args.confirm:
        print("Bez --dry-run ili --confirm. Podrazumeva se --dry-run zbog bezbednosti.\n")
        args.dry_run = True

    _load_env()

    print("\n── vault.key kandidati ──")
    keys = _find_vault_keys()
    if not keys:
        print("  ✗ Nema nijedan vault.key niti env ENCRYPTION_KEY.")
        return 1
    for src, k in keys:
        print(f"  • {src}  (key hash: {k[:8].decode('utf-8', 'replace')}…)")

    print("\n── backup direktorijumi ──")
    for d in _find_backup_dirs():
        try:
            n = sum(1 for _ in d.iterdir())
            print(f"  • {d}  ({n} fajlova)")
        except Exception:
            pass

    labels = [args.only] if args.only else list(DB_LABELS.keys())

    if args.file:
        if not args.only:
            print("\n✗ --file zahteva --only.")
            return 1
        f = Path(args.file)
        if not f.exists():
            print(f"\n✗ Fajl ne postoji: {f}")
            return 1
        ok = restore_one(args.only, f, args.dry_run)
    else:
        results = []
        for lbl in labels:
            results.append(restore_one(lbl, None, args.dry_run))
        ok = all(results)

    if args.dry_run:
        print("\nDry-run gotov. Pokreni ponovo sa --confirm da instaliraš.")
    elif ok:
        print("\n✅ Restore gotov. **Reload web app** kroz PA Web tab da aplikacija pokupi svežu bazu.")
    else:
        print("\n✗ Bilo je grešaka — proveri gore.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
