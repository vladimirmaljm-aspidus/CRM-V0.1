#!/usr/bin/env python3
"""
V23.1 #1 — Supabase-only coverage audit.

Skenira izvorni kod trazeci potencijalna mesta gde app pise LOKALNO umesto u
Supabase (SQLite, disk fajlovi, in-memory samo). Cilj: na Render-u ne sme
ostati nista lokalno jer se ephemeral storage briše.

Ne menja kod — samo pravi izvestaj `out/supabase_coverage_report.md`.

Sekcije:
  1. SQLite writes (sqlite3.connect + INSERT/UPDATE/DELETE)
  2. Local file writes (open(..., 'w'/'a'/'wb'), os.makedirs, shutil.copy)
  3. In-memory caches koje bi trebalo persistovati u DB
  4. Preporucene korekcije (whitelist za kes-only tabele koje mogu ostati lokalno)
"""
from __future__ import annotations
import os
import re
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# Fajlovi/direktorijume koje preskacemo
SKIP_DIRS = {'.git', '__pycache__', 'node_modules', 'venv', '.venv', 'out',
             'backups', 'uploads', 'portal_uploads'}
SKIP_FILES = {'audit_supabase_coverage.py', 'db_export_full.py', 'db_import_full.py',
              'import_local_db_to_supabase.py', 'restore_from_fernet_backup.py',
              'reconcile_sqlite_supabase.py', 'migrate_data_to_supabase.py',
              'migrate_partners_to_supabase.py', 'db_recover.py',
              'db_migrate_to_postgres.py'}

# Kes-only tabele — dozvoljeno da ostanu SQLite (regeneraciju cine iz baze)
CACHE_TABLES = {'file_text', 'IP_INFO_CACHE', 'login_attempts', 'FirewallCache'}


def _iter_python_files():
    for root, dirs, files in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith('.py') and f not in SKIP_FILES:
                yield os.path.join(root, f)


def _scan_file(path):
    rel = os.path.relpath(path, _ROOT)
    findings = {'sqlite_writes': [], 'file_writes': [], 'in_memory': []}
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception:
        return rel, findings

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('#'):
            continue

        # SQLite INSERT/UPDATE/DELETE (heuristika)
        if re.search(r'\.execute\(.*(INSERT|UPDATE|DELETE)\s+', line, re.IGNORECASE):
            # Preskoci kes tabele
            if not any(t.lower() in line.lower() for t in CACHE_TABLES):
                findings['sqlite_writes'].append((i, stripped[:150]))

        # Local file writes
        if re.search(r"\bopen\s*\(\s*[^,]+,\s*['\"](w|wb|a|ab)", line):
            if 'backup' not in line.lower() and 'log' not in line.lower():
                findings['file_writes'].append((i, stripped[:150]))
        if re.search(r'\bshutil\.(copy|copytree|move)\(', line):
            findings['file_writes'].append((i, stripped[:150]))
        if re.search(r'\bos\.makedirs\(', line):
            findings['file_writes'].append((i, stripped[:150]))

        # In-memory dict on module level (heuristika)
        if re.match(r'^_?[A-Z_]+\s*=\s*(\{\}|\[\])', stripped):
            findings['in_memory'].append((i, stripped[:150]))

    return rel, findings


def main():
    out_dir = os.path.join(_ROOT, 'out')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'supabase_coverage_report.md')

    total_files = 0
    total_findings = 0
    per_file = []

    for path in _iter_python_files():
        rel, f = _scan_file(path)
        total_files += 1
        total = sum(len(v) for v in f.values())
        if total > 0:
            per_file.append((rel, f, total))
            total_findings += total

    per_file.sort(key=lambda x: -x[2])

    with open(out_path, 'w', encoding='utf-8') as w:
        w.write(f'# Supabase-only coverage audit\n\n')
        w.write(f'_Generated: {datetime.now().isoformat()}_\n\n')
        w.write(f'Scanned: **{total_files}** files. Found **{total_findings}** potential local-write hotspots.\n\n')
        w.write('## Rules\n\n')
        w.write('- Render brise sve lokalno na svakom redeploy-u. Sve mora u Supabase.\n')
        w.write('- Whitelist (dozvoljeno lokalno): kes tabele koje se regenerisu iz baze — '
                'file_text OCR cache, IP_INFO_CACHE, FirewallCache in-memory.\n')
        w.write('- Backup i log fajlovi su OK ako se otpremaju u Supabase Storage (utils.py `_backup_loop`).\n\n')

        for rel, f, total in per_file:
            w.write(f'## `{rel}` — {total} findings\n\n')
            if f['sqlite_writes']:
                w.write('### SQLite writes (proveri da li ide i u Supabase)\n\n')
                for ln, code in f['sqlite_writes']:
                    w.write(f'- **L{ln}**: `{code}`\n')
                w.write('\n')
            if f['file_writes']:
                w.write('### Local file writes (mora u Supabase Storage)\n\n')
                for ln, code in f['file_writes']:
                    w.write(f'- **L{ln}**: `{code}`\n')
                w.write('\n')
            if f['in_memory']:
                w.write('### In-memory state (razmotri persistenciju)\n\n')
                for ln, code in f['in_memory'][:10]:  # cap
                    w.write(f'- **L{ln}**: `{code}`\n')
                w.write('\n')

    print(f'Report written to {out_path}')
    print(f'{total_files} files scanned, {total_findings} findings.')


if __name__ == '__main__':
    main()
