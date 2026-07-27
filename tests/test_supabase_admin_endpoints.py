#!/usr/bin/env python3
"""Testovi za Supabase admin API, migraciju, Error Log, Preferences endpoint-e.

Pokreni:
    pytest tests/test_supabase_admin_endpoints.py -v

ili direktno:
    python3 tests/test_supabase_admin_endpoints.py
"""
import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ.setdefault('TEST_MODE', '1')
os.environ.setdefault('ADMIN_USERNAME', 'testadmin')
os.environ.setdefault('ADMIN_PASSWORD', 'TestAdmin123!')


try:
    import flask  # noqa
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipIf(not HAS_FLASK, "Flask nije instaliran (samo runtime env)")
class SupabaseAdminEndpointsTests(unittest.TestCase):
    """Verifikuje da su sve Supabase admin API rute registrovane i da vraćaju
    smislene odgovore (bez potrebe za pravom Supabase konekcijom)."""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.app = app
        cls.client = app.test_client()

    def _login(self):
        return self.client.post('/api/auth/login', json={
            'username': os.environ['ADMIN_USERNAME'],
            'password': os.environ['ADMIN_PASSWORD'],
        })

    # ---- ADMIN PANEL ROUTES ----

    def test_supabase_admin_page_requires_auth(self):
        """/admin/supabase mora tražiti login."""
        r = self.client.get('/admin/supabase')
        self.assertIn(r.status_code, (302, 401, 403), f"got {r.status_code}")

    def test_admin_errors_page_requires_auth(self):
        r = self.client.get('/admin/errors')
        self.assertIn(r.status_code, (302, 401, 403))

    def test_supabase_status_requires_admin(self):
        r = self.client.get('/api/supabase/status')
        self.assertIn(r.status_code, (302, 401, 403))

    def test_admin_errors_api_requires_admin(self):
        r = self.client.get('/api/admin/errors')
        self.assertIn(r.status_code, (302, 401, 403))

    def test_session_info_requires_login(self):
        r = self.client.get('/api/session/info')
        self.assertIn(r.status_code, (302, 401, 403))

    def test_two_fa_status_requires_login(self):
        r = self.client.get('/api/2fa/status')
        self.assertIn(r.status_code, (302, 401, 403))

    # ---- ROUTES REGISTRATION ----

    def test_supabase_migrate_route_exists(self):
        """Ruta /api/supabase/migrate MORA postojati."""
        with self.app.test_request_context():
            urls = [r.rule for r in self.app.url_map.iter_rules()]
            self.assertIn('/api/supabase/migrate', urls)
            self.assertIn('/api/supabase/dry-run', urls)
            self.assertIn('/api/supabase/set-flag', urls)
            self.assertIn('/api/supabase/status', urls)
            self.assertIn('/api/admin/errors', urls)
            self.assertIn('/api/admin/errors/clear', urls)
            self.assertIn('/api/session/info', urls)
            self.assertIn('/api/2fa/status', urls)
            self.assertIn('/api/users/me', urls)
            self.assertIn('/api/users/change-password', urls)

    def test_supabase_admin_page_registered(self):
        with self.app.test_request_context():
            urls = [r.rule for r in self.app.url_map.iter_rules()]
            self.assertIn('/admin/supabase', urls)
            self.assertIn('/admin/errors', urls)

    # ---- PORTAL AUTH ENDPOINTS ----

    def test_portal_signin_password_route_exists(self):
        with self.app.test_request_context():
            urls = [r.rule for r in self.app.url_map.iter_rules()]
            self.assertIn('/api/portal/auth/supabase/signin-password', urls)
            self.assertIn('/api/portal/auth/supabase/set-password', urls)
            self.assertIn('/api/portal/auth/supabase/exchange', urls)
            self.assertIn('/api/portal/auth/supabase/send-magic-link', urls)
            self.assertIn('/api/portal/auth/supabase/send-reset', urls)
            self.assertIn('/api/portal/user/change-password', urls)

    def test_portal_admin_endpoints_exist(self):
        with self.app.test_request_context():
            urls = [r.rule for r in self.app.url_map.iter_rules()]
            self.assertIn('/api/portal/admin/send-portal-invite/<partner_id>', urls)
            self.assertIn('/api/portal/admin/set-partner-password/<partner_id>', urls)


@unittest.skipIf(not HAS_FLASK, "Flask nije instaliran (samo runtime env)")
class ErrorHandlerTests(unittest.TestCase):
    """Verifikuje da 500 handler ubacuje u error buffer."""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.app = app

    def test_record_error_appends_to_buffer(self):
        from routes.supabase_admin import record_error, _ERROR_BUFFER
        n_before = len(_ERROR_BUFFER)
        try:
            raise ValueError("test error XYZ")
        except ValueError as e:
            record_error("test_context", e, request_id="test123")
        self.assertGreater(len(_ERROR_BUFFER), n_before)
        latest = _ERROR_BUFFER[-1]
        self.assertEqual(latest['request_id'], "test123")
        self.assertIn("test error XYZ", latest['message'])
        self.assertIn("ValueError", latest['traceback'])


class DesignAssetsTests(unittest.TestCase):
    """Verifikuje da su svi novi frontend fajlovi tu."""

    def test_modern_css_exists(self):
        p = _ROOT / 'static' / 'css' / 'modern.css'
        self.assertTrue(p.exists(), "modern.css mora postojati")
        content = p.read_text()
        self.assertIn('--brand-gradient', content)
        self.assertIn('kpi-card', content)
        self.assertIn('sidebar-section', content)

    def test_preferences_js_exists(self):
        p = _ROOT / 'static' / 'js' / 'core' / 'preferences.js'
        self.assertTrue(p.exists())
        content = p.read_text()
        self.assertIn('openPreferences', content)
        self.assertIn('saveProfile', content)
        self.assertIn('saveAppearance', content)
        self.assertIn('saveNotifications', content)
        self.assertIn('refreshSession', content)

    def test_supabase_admin_template_exists(self):
        p = _ROOT / 'templates' / 'supabase_admin.html'
        self.assertTrue(p.exists())
        c = p.read_text()
        self.assertIn('Migration Actions', c)
        self.assertIn('Dry-Run', c)
        self.assertIn('X-CSRF-Token', c, "Template mora slati CSRF token")

    def test_admin_errors_template_exists(self):
        p = _ROOT / 'templates' / 'admin_errors.html'
        self.assertTrue(p.exists())
        c = p.read_text()
        self.assertIn('Error Log', c)

    def test_index_html_includes_new_assets(self):
        p = _ROOT / 'templates' / 'index.html'
        c = p.read_text()
        self.assertIn('modern.css', c)
        self.assertIn('preferences.js', c)
        self.assertIn('supabase-admin-link', c)
        self.assertIn('admin-errors-link', c)
        self.assertIn('profile-btn', c)


class ScriptsSanityTests(unittest.TestCase):
    """Basic Python sintaksa svih naših migracionih skript-ova."""

    def test_migrate_data_script_parses(self):
        import ast
        p = _ROOT / 'scripts' / 'migrate_data_to_supabase.py'
        ast.parse(p.read_text())

    def test_migrate_partners_script_parses(self):
        import ast
        p = _ROOT / 'scripts' / 'migrate_partners_to_supabase.py'
        ast.parse(p.read_text())

    def test_restore_backup_script_parses(self):
        import ast
        p = _ROOT / 'scripts' / 'restore_from_fernet_backup.py'
        ast.parse(p.read_text())

    def test_verify_supabase_script_parses(self):
        import ast
        p = _ROOT / 'scripts' / 'verify_supabase_connection.py'
        ast.parse(p.read_text())

    def test_auth_supabase_module_parses(self):
        import ast
        p = _ROOT / 'auth_supabase.py'
        ast.parse(p.read_text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
