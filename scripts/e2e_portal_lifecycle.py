#!/usr/bin/env python3
"""E2E test koji simulira KOMPLETAN portal life-cycle novog klijenta.

Pokreće se sa PA da bi dijagnostikovao koji korak stvarno radi na živoj
infrastrukturi. Ne zahteva browser (koristi requests) — testira samo
backend API-je.

Životni ciklus koji simuliramo:
   1. Admin login u CRM
   2. Kreiraj testnog partnera (contact.email = test-<timestamp>@aspidus.test)
   3. Admin klikne "Set Portal Password" — postavi lozinku direktno
   4. Klijent otvori /portal/login → signin-password
   5. Klijent dobija auth_key + portal_token
   6. Klijent koristi portal API (GET /api/portal/data/<token>)
   7. Klijent menja lozinku (change-password)
   8. Klijent se ponovo uloguje sa novom lozinkom
   9. Admin briše test partnera (cleanup)

Za svaki korak: POVRATNA_VREDNOST + trajanje + status. Na kraju zbirni
pass/fail izveštaj.

Pokretanje na PA:
    cd /home/aspidus/mysite/CRM
    python3.13 scripts/e2e_portal_lifecycle.py

Env vars:
    ADMIN_USERNAME, ADMIN_PASSWORD  — CRM admin credentials
    PORTAL_BASE_URL                  — default https://aspidus.pythonanywhere.com
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("✗ 'requests' paket nije instaliran. pip install requests")
    sys.exit(1)


BASE = os.environ.get("PORTAL_BASE_URL", "https://aspidus.pythonanywhere.com").rstrip("/")
ADMIN_USER = os.environ.get("ADMIN_USERNAME")
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD")


class TestRunner:
    def __init__(self):
        self.session = requests.Session()
        self.session.verify = True
        self.csrf_token = None
        self.results = []
        self.test_partner_id = None
        self.test_email = f"e2e-test-{int(time.time())}@aspidus.test"
        self.test_password = "TestPass123!"
        self.new_password = "NewPass456!"
        self.portal_token = None
        self.auth_key = None

    def _step(self, name):
        return _Step(self, name)

    def _get_csrf(self):
        if self.csrf_token:
            return self.csrf_token
        r = self.session.get(f"{BASE}/api/csrf/token", timeout=10)
        r.raise_for_status()
        self.csrf_token = r.json().get("csrf_token")
        return self.csrf_token

    def _post(self, path, **kwargs):
        headers = kwargs.pop("headers", {})
        headers.setdefault("X-CSRF-Token", self._get_csrf() or "")
        headers.setdefault("Content-Type", "application/json")
        return self.session.post(f"{BASE}{path}", headers=headers, timeout=30, **kwargs)

    def _get(self, path, **kwargs):
        return self.session.get(f"{BASE}{path}", timeout=15, **kwargs)

    # ----- LIFE-CYCLE STEPS -----

    def step_admin_login(self):
        with self._step("1. Admin login u CRM"):
            r = self._post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
            assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
            self.csrf_token = None  # osvezi CSRF posle login-a
            self._get_csrf()

    def step_create_test_partner(self):
        with self._step("2. Kreiraj test partnera"):
            payload = {
                "companyName": f"E2E Test Company {int(time.time())}",
                "email": self.test_email,
                "contact": {"email": self.test_email, "person": "Test Contact"},
                "status": "active",
                "isPortalActive": True,
            }
            r = self._post("/api/data/partners", json={"data": payload})
            assert r.status_code in (200, 201), f"Create partner failed: {r.status_code} {r.text[:200]}"
            data = r.json() if r.text else {}
            self.test_partner_id = data.get("id") or (data.get("data") or {}).get("id")
            if not self.test_partner_id:
                # fallback — nadji poslednjeg partnera sa tim email-om
                r2 = self._get("/api/data/partners")
                partners = r2.json() if r2.ok else []
                for p in partners:
                    pdata = p.get("data") if isinstance(p, dict) else {}
                    if isinstance(pdata, str):
                        try: pdata = json.loads(pdata)
                        except Exception: pdata = {}
                    if pdata.get("email") == self.test_email or (pdata.get("contact") or {}).get("email") == self.test_email:
                        self.test_partner_id = p.get("id")
                        break
            assert self.test_partner_id, "Kreiran partner ali nemam ID"

    def step_set_portal_password(self):
        with self._step("3. Admin postavlja Portal Password direktno"):
            r = self._post(f"/api/portal/admin/set-partner-password/{self.test_partner_id}",
                           json={"password": self.test_password})
            assert r.status_code == 200, f"Set password failed: {r.status_code} {r.text[:400]}"
            body = r.json()
            assert body.get("status") == "success", f"Bad response: {body}"

    def step_client_signin(self):
        with self._step("4. Klijent login-uje na portal sa email + password"):
            # Odjavi admina prvo
            self.session.cookies.clear()
            self.csrf_token = None
            self._get_csrf()
            r = self._post("/api/portal/auth/supabase/signin-password", json={
                "email": self.test_email,
                "password": self.test_password,
                "location": "44.7866,20.4489",  # Belgrade for test
            })
            assert r.status_code == 200, f"Portal signin failed: {r.status_code} {r.text[:400]}"
            body = r.json()
            assert body.get("auth_key"), f"No auth_key: {body}"
            self.auth_key = body["auth_key"]
            self.portal_token = body["token"]

    def step_client_reads_data(self):
        with self._step("5. Klijent čita portal podatke"):
            r = self._get(f"/api/portal/data/{self.portal_token}",
                          headers={"X-Portal-Auth": self.auth_key})
            assert r.status_code == 200, f"Portal data read failed: {r.status_code} {r.text[:200]}"

    def step_client_change_password(self):
        with self._step("6. Klijent menja svoju lozinku iz portala"):
            r = self._post("/api/portal/user/change-password", json={
                "portal_token": self.portal_token,
                "current_password": self.test_password,
                "new_password": self.new_password,
            }, headers={"X-Portal-Auth": self.auth_key, "X-CSRF-Token": self._get_csrf() or ""})
            assert r.status_code == 200, f"Change password failed: {r.status_code} {r.text[:400]}"
            body = r.json()
            assert body.get("status") == "success", f"Bad response: {body}"

    def step_client_relogin(self):
        with self._step("7. Klijent se ponovo loguje sa NOVOM lozinkom"):
            self.session.cookies.clear()
            self.csrf_token = None
            self._get_csrf()
            r = self._post("/api/portal/auth/supabase/signin-password", json={
                "email": self.test_email,
                "password": self.new_password,
                "location": "44.7866,20.4489",
            })
            assert r.status_code == 200, f"Re-login failed: {r.status_code} {r.text[:400]}"

    def step_cleanup(self):
        with self._step("8. Cleanup — briši test partnera + Auth user"):
            # Re-login admin za brisanje
            self.session.cookies.clear()
            self.csrf_token = None
            self._get_csrf()
            r = self._post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
            if r.status_code == 200 and self.test_partner_id:
                self.csrf_token = None
                self._get_csrf()
                self.session.delete(f"{BASE}/api/data/partners/{self.test_partner_id}",
                                    headers={"X-CSRF-Token": self._get_csrf() or ""},
                                    timeout=15)

    # ----- RUN ALL -----

    def run(self):
        print(f"\n{'='*70}")
        print(f"  ASPIDUS PORTAL LIFECYCLE E2E TEST")
        print(f"  Target: {BASE}")
        print(f"  Test partner email: {self.test_email}")
        print(f"{'='*70}\n")

        steps = [
            self.step_admin_login,
            self.step_create_test_partner,
            self.step_set_portal_password,
            self.step_client_signin,
            self.step_client_reads_data,
            self.step_client_change_password,
            self.step_client_relogin,
            self.step_cleanup,
        ]

        for step in steps:
            try:
                step()
            except AssertionError as e:
                # Ne prekidaj — nastavi da vidimo koji sve koraci pucaju
                print(f"    → nastavljam da vidim šta još pukne...\n")
            except Exception as e:
                print(f"    ✗ Neočekivana greška: {type(e).__name__}: {e}\n")

        # SUMMARY
        print(f"\n{'='*70}")
        print(f"  REZIME")
        print(f"{'='*70}")
        passed = sum(1 for r in self.results if r["ok"])
        failed = sum(1 for r in self.results if not r["ok"])
        for r in self.results:
            icon = "✓" if r["ok"] else "✗"
            dur = f"{r['duration']:.2f}s"
            print(f"  {icon} {r['name']:60s} {dur:>8s}")
            if not r["ok"] and r.get("error"):
                print(f"     └─ {r['error'][:200]}")
        print(f"\n  TOTAL: {passed} passed, {failed} failed")
        if failed == 0:
            print(f"\n  ✅ SVI KORACI RADE — portal je spreman za produkciju.\n")
        else:
            print(f"\n  ⚠ IMA GREŠAKA — pogledaj koje i popravi pre nego što pustiš klijentima.\n")
        return 0 if failed == 0 else 1


class _Step:
    def __init__(self, runner, name):
        self.runner, self.name = runner, name
        self.t0 = 0.0

    def __enter__(self):
        self.t0 = time.time()
        print(f"→ {self.name}")
        return self

    def __exit__(self, exc_type, exc, tb):
        dur = time.time() - self.t0
        if exc is None:
            print(f"  ✓ OK ({dur:.2f}s)\n")
            self.runner.results.append({"name": self.name, "ok": True, "duration": dur})
        else:
            err = str(exc)
            print(f"  ✗ FAIL: {err[:400]} ({dur:.2f}s)")
            self.runner.results.append({"name": self.name, "ok": False, "duration": dur, "error": err})
            return True  # swallow to continue


def main():
    if not ADMIN_USER or not ADMIN_PASS:
        print("✗ Postavi ADMIN_USERNAME i ADMIN_PASSWORD env varijable (iz .env-a).")
        print("   Primer: source .env && python3.13 scripts/e2e_portal_lifecycle.py")
        return 1
    return TestRunner().run()


if __name__ == "__main__":
    sys.exit(main())
