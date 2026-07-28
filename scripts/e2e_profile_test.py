#!/usr/bin/env python3
"""
e2e_profile_test.py — end-to-end verifikacija svih Profile & Preferences
opcija koje smo dodali. Proverava:

  1. /api/auth/login          — admin login
  2. /api/auth/me             — vraca full_name/email/phone/notif_prefs
  3. /api/users/me GET        — full profile
  4. /api/users/me PATCH      — update full_name
  5. /api/users/me PATCH      — update notif_prefs
  6. /api/users/me PATCH      — reject invalid email
  7. /api/users/change-password — reject wrong current password
  8. /api/session/info        — vraca ttl_seconds
  9. /api/2fa/status          — vraca boolean
 10. /admin/health JSON       — sve nove probe stigle

Pokretanje:
    ADMIN_USERNAME=admin ADMIN_PASSWORD=... \\
    python3.13 scripts/e2e_profile_test.py
"""
from __future__ import annotations

import json
import os
import sys

try:
    import requests
except ImportError:
    print("✗ requests missing. pip install requests")
    sys.exit(1)


BASE = os.environ.get("PORTAL_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
USER = os.environ.get("ADMIN_USERNAME")
PWD  = os.environ.get("ADMIN_PASSWORD")


class Test:
    def __init__(self):
        self.s = requests.Session()
        self.csrf = None
        self.results = []
        self.original_full_name = None

    def _csrf(self):
        if self.csrf: return self.csrf
        r = self.s.get(f"{BASE}/api/csrf/token", timeout=10)
        if r.ok:
            self.csrf = r.json().get('csrf_token')
        return self.csrf or ''

    def _hdr(self):
        return {'Content-Type': 'application/json', 'X-CSRF-Token': self._csrf()}

    def _step(self, name, fn):
        try:
            fn()
            self.results.append({'name': name, 'ok': True})
            print(f"  ✓ {name}")
        except AssertionError as e:
            self.results.append({'name': name, 'ok': False, 'error': str(e)})
            print(f"  ✗ {name}")
            print(f"      {e}")
        except Exception as e:
            self.results.append({'name': name, 'ok': False, 'error': f'{type(e).__name__}: {e}'})
            print(f"  ✗ {name} → {type(e).__name__}: {e}")

    def run(self):
        print(f"\n{'='*70}\n  Profile & Preferences E2E test — {BASE}\n{'='*70}\n")
        self._step("1. Admin login", self.step_login)
        self._step("2. /api/auth/me returns extended profile", self.step_auth_me)
        self._step("3. GET /api/users/me", self.step_get_me)
        self._step("4. PATCH /api/users/me — full_name", self.step_patch_name)
        self._step("5. PATCH /api/users/me — notif_prefs", self.step_patch_notif)
        self._step("6. PATCH /api/users/me — reject invalid email", self.step_reject_email)
        self._step("7. Change password — reject wrong current", self.step_reject_pwd)
        self._step("8. /api/session/info", self.step_session_info)
        self._step("9. /api/2fa/status", self.step_2fa_status)
        self._step("10. /api/admin/health JSON has new probes", self.step_health)
        # Restore name da ne ostavimo test tragove
        if self.original_full_name is not None:
            try:
                self.s.patch(f"{BASE}/api/users/me", headers=self._hdr(),
                             json={'full_name': self.original_full_name}, timeout=10)
            except Exception:
                pass

        print(f"\n{'='*70}\n  RESULTS\n{'='*70}")
        passed = sum(1 for r in self.results if r['ok'])
        failed = sum(1 for r in self.results if not r['ok'])
        print(f"  {passed} passed, {failed} failed / {len(self.results)} total")
        return 0 if failed == 0 else 1

    # Individual steps

    def step_login(self):
        r = self.s.post(f"{BASE}/api/auth/login", headers=self._hdr(),
                        json={'username': USER, 'password': PWD}, timeout=15)
        assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
        self.csrf = None  # nova csrf posle logina
        self._csrf()

    def step_auth_me(self):
        r = self.s.get(f"{BASE}/api/auth/me", timeout=10)
        assert r.status_code == 200, f"/api/auth/me → {r.status_code}"
        u = r.json().get('user', {})
        for key in ('id', 'username', 'role', 'permissions', 'full_name', 'email', 'phone', 'notif_prefs'):
            assert key in u, f"missing key '{key}' in /api/auth/me response"
        self.original_full_name = u.get('full_name') or ''

    def step_get_me(self):
        r = self.s.get(f"{BASE}/api/users/me", timeout=10)
        assert r.status_code == 200, f"GET /api/users/me → {r.status_code}"
        j = r.json()
        for key in ('username', 'role', 'full_name', 'email', 'phone', 'notif_prefs'):
            assert key in j, f"missing key '{key}' in /api/users/me"

    def step_patch_name(self):
        test_name = "E2E Test Name"
        r = self.s.patch(f"{BASE}/api/users/me", headers=self._hdr(),
                         json={'full_name': test_name}, timeout=10)
        assert r.status_code == 200, f"PATCH failed: {r.status_code} {r.text[:200]}"
        assert r.json().get('status') == 'ok'
        # Read back
        r2 = self.s.get(f"{BASE}/api/users/me", timeout=10)
        assert r2.json().get('full_name') == test_name, f"full_name not persisted: got '{r2.json().get('full_name')}'"

    def step_patch_notif(self):
        prefs = {'portal': False, 'deals': True, 'email': False, 'sound': True}
        r = self.s.patch(f"{BASE}/api/users/me", headers=self._hdr(),
                         json={'notif_prefs': prefs}, timeout=10)
        assert r.status_code == 200, f"PATCH notif_prefs failed: {r.status_code}"
        r2 = self.s.get(f"{BASE}/api/users/me", timeout=10)
        got = r2.json().get('notif_prefs') or {}
        assert got.get('portal') is False, f"notif_prefs.portal not saved: {got}"
        assert got.get('sound') is True, f"notif_prefs.sound not saved: {got}"

    def step_reject_email(self):
        r = self.s.patch(f"{BASE}/api/users/me", headers=self._hdr(),
                         json={'email': 'not-an-email'}, timeout=10)
        assert r.status_code == 400, f"expected 400 for invalid email, got {r.status_code}"

    def step_reject_pwd(self):
        r = self.s.post(f"{BASE}/api/users/change-password", headers=self._hdr(),
                        json={'current': 'WRONG_password_XYZ', 'next': 'Aspidus9!New'},
                        timeout=15)
        assert r.status_code == 401, f"expected 401 for wrong password, got {r.status_code}"

    def step_session_info(self):
        r = self.s.get(f"{BASE}/api/session/info", timeout=10)
        assert r.status_code == 200
        j = r.json()
        assert 'ttl_seconds' in j
        assert 'login_time' in j

    def step_2fa_status(self):
        r = self.s.get(f"{BASE}/api/2fa/status", timeout=10)
        assert r.status_code == 200
        assert isinstance(r.json().get('enabled'), bool)

    def step_health(self):
        r = self.s.get(f"{BASE}/api/admin/health", timeout=15)
        assert r.status_code == 200, f"/api/admin/health → {r.status_code}"
        checks = (r.json() or {}).get('checks', {})
        for expected in ('sqlite', 'ocr', 'mail_queue', 'webhook', 'data_layer'):
            assert expected in checks, f"health probe '{expected}' missing"


def main():
    if not USER or not PWD:
        print("✗ Postavi ADMIN_USERNAME i ADMIN_PASSWORD env varijable.")
        return 1
    return Test().run()


if __name__ == "__main__":
    sys.exit(main())
