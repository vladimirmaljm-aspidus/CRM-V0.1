#!/usr/bin/env python3
"""
audit_email_triggers.py — pronadji SVAKO mesto gde app salje email.

Skener prolazi kroz sve .py fajlove i identifikuje pozive raznih mail
funkcija. Rezultat:
  * gde je poziv (fajl:linija)
  * koja funkcija se zove (send_new_offer, send_password_reset, itd.)
  * u kojoj kategoriji upada (transactional / auth / notification / admin)
  * koji provider ide (utils_email queue → SMTP+Resend, ili Supabase Auth)

Cilj: uvek jedno mesto istine za "koji mejl kada ide klijentu / adminu".

Pokretanje:
    python3.13 scripts/audit_email_triggers.py
    python3.13 scripts/audit_email_triggers.py --json > docs/EMAIL_TRIGGERS.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Funkcije za koje znamo da šalju mejl (i njihove kategorije).
KNOWN_MAIL_FNS = {
    # utils_email — glavni transactional pipeline (SMTP+Resend, queue)
    "send_new_offer":            ("transactional", "utils_email → SMTP/Resend queue"),
    "send_kyc_approved":         ("transactional", "utils_email → SMTP/Resend queue"),
    "send_kyc_update_requested": ("transactional", "utils_email → SMTP/Resend queue"),
    "send_portal_welcome":       ("transactional", "utils_email → SMTP/Resend queue"),
    "send_branded_admin_message": ("admin",         "utils_email direct send"),
    "process_email_queue":       ("infra",         "utils_email background drain"),
    "queue_email":               ("transactional", "utils_email queue insert"),
    "_send_email":               ("infra",         "utils_email low-level SMTP"),
    "send_transactional":        ("transactional", "mail_providers → Resend/SendGrid"),

    # auth_supabase — direktno preko Supabase Auth API-ja
    "send_password_reset":       ("auth",          "Supabase Auth → email"),
    "send_magic_link":           ("auth",          "Supabase Auth → email (OTP)"),
    "reset_password_email":      ("auth",          "Supabase Auth reset"),
    "sign_in_with_otp":          ("auth",          "Supabase Auth magic link"),

    # comms.py — legacy admin-slanje custom mejla
    "send_email":                ("admin",         "routes/comms — ad-hoc SMTP"),
}

# Ne skeniramo ove foldere
SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", ".pytest_cache"}


def scan():
    hits = []
    fn_pattern = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in KNOWN_MAIL_FNS.keys()) + r")\s*\("
    )

    for py in ROOT.rglob("*.py"):
        rel = py.relative_to(ROOT)
        parts = set(rel.parts)
        if parts & SKIP_DIRS:
            continue
        # Preskoči sam sebe i audit skriptu
        if rel.name in ("audit_email_triggers.py",):
            continue

        try:
            content = py.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            # Preskoči komentare i def linije (definicija, ne poziv)
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if stripped.startswith("def "):
                continue
            m = fn_pattern.search(line)
            if not m:
                continue
            fn = m.group(1)
            category, provider = KNOWN_MAIL_FNS[fn]
            hits.append({
                "file": str(rel),
                "line": lineno,
                "function": fn,
                "category": category,
                "provider": provider,
                "snippet": stripped[:160],
            })

    return hits


def render_text(hits):
    by_cat = {}
    for h in hits:
        by_cat.setdefault(h["category"], []).append(h)

    out = []
    out.append("=" * 78)
    out.append("  EMAIL TRIGGER AUDIT — SVAKI MAIL CALL SITE U APLIKACIJI")
    out.append("=" * 78)
    order = ("auth", "transactional", "admin", "infra")
    for cat in order:
        if cat not in by_cat:
            continue
        out.append("")
        out.append(f"── {cat.upper()} ".ljust(78, "─"))
        for h in by_cat[cat]:
            out.append(f"  {h['file']}:{h['line']:>4d}  {h['function']}")
            out.append(f"       provider: {h['provider']}")
            out.append(f"       code:     {h['snippet']}")
            out.append("")
    out.append("=" * 78)
    out.append(f"  UKUPNO: {len(hits)} call site(s) u {len(set(h['file'] for h in hits))} fajl(ova)")
    out.append("=" * 78)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="JSON output umesto teksta")
    args = ap.parse_args()

    hits = scan()
    if args.json:
        print(json.dumps({"total": len(hits), "hits": hits}, indent=2))
    else:
        print(render_text(hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
