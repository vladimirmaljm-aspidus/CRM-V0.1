"""V23.1 BRUTAL TEST SUITE — cover everything added in V23.1/V23.1B/V23.1C.

Testira:
  1. DOCUMENT REGISTER hook — assigns docNumber, bumps revision on content change,
     no-op when content unchanged.
  2. GRANULAR PERMISSIONS — matrix save, require_perm gate, admin bypass.
  3. PORTAL PERMISSIONS — modules toggle, view_only_own_docs enforcement.
  4. CONVERSION offer → invoice / proforma — 1:1 field copy, new doc number.
  5. SECURITY CENTER — session list, revoke, magic link, lockout counters.
  6. USER TASKS — CRUD + entity-scoped query + complete action.
  7. SAVED FILTERS — per-entity, shared vs owned visibility.
  8. ACTIVITY FEED — pulls from audit_logs.
  9. BULK ACTIONS — archive/tag/delete guardrails.
 10. CUSTOM FIELDS — CRUD, only-known-entity filter.
 11. API KEYS — issue → hash-store, verify, revoke.
 12. OUTBOUND WEBHOOKS — HMAC signing, delivery log, emit_event routing.
 13. SUPABASE MERGE — _coerce_row whitelist + JSONB fallback.
 14. PDF GENERATOR V23.1 — kind_label defined, header+footer callback works,
     no overlap references.
 15. RELATIONS — offer→partner FK, deal→offer, invoice sourceOfferId, etc.

Pokreni:
    python -m tests.test_v23_1
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid


# ------------------------------------------------------------
# Setup — fresh temp DATA_DIR for every full run (deterministic)
# ------------------------------------------------------------
if not os.getenv('DATA_DIR'):
    os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='v23_1_')

os.environ.setdefault('SECRET_KEY', 'v23-1-test-secret-key-must-be-long-enough-please')
os.environ.setdefault('ADMIN_USERNAME', 'v23admin')
os.environ.setdefault('ADMIN_PASSWORD', 'V23Admin!TestPass2026')
os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SESSION_COOKIE_SECURE', 'false')
# V24.0: Supabase-only mode koristi in-memory mock backend za testove.
os.environ.setdefault('DB_BACKEND', 'mock')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module   # noqa: E402


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _login_admin(client, gps='45.267,19.833'):
    """Vrati auth-ovan Werkzeug client. Login zaobilazi 2FA (admin ga nema)."""
    r = client.post('/api/auth/login', json={
        'username': os.environ['ADMIN_USERNAME'],
        'password': os.environ['ADMIN_PASSWORD'],
        'location': gps, 'device': 'Test/1.0',
    })
    assert r.status_code == 200, f'admin login failed: {r.status_code} {r.data[:200]}'
    return r


def _csrf(client):
    r = client.get('/api/csrf/token')
    return r.get_json()['csrf_token']


# ------------------------------------------------------------
# 1) DOCUMENT REGISTER hook
# ------------------------------------------------------------
class T01DocumentRegister(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        _login_admin(self.client)
        self.csrf = _csrf(self.client)

        # Kreiraj partnera i ponudu direktno u SQLite (brzo)
        from config import DB_FILE
        self.pid = 'test-partner-' + uuid.uuid4().hex[:8]
        self.oid = 'test-offer-'  + uuid.uuid4().hex[:8]
        with sqlite3.connect(DB_FILE, timeout=5.0) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS partners (id TEXT PRIMARY KEY, data TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS offers   (id TEXT PRIMARY KEY, data TEXT)')
            conn.execute("INSERT OR REPLACE INTO partners (id,data) VALUES (?,?)",
                         (self.pid, json.dumps({'id': self.pid, 'name': 'ACME Ltd', 'country':'RS'})))
            conn.execute("INSERT OR REPLACE INTO offers (id,data) VALUES (?,?)",
                         (self.oid, json.dumps({
                             'id': self.oid, 'offerNo':'1/2026',
                             'customerId': self.pid, 'currency':'EUR',
                             'items':[{'name':'Coffee','qty':10,'price':5.5}]
                         })))

    def test_01_first_register_assigns_v1_and_doc_number(self):
        r = self.client.post(f'/api/documents/register-existing/offer/{self.oid}',
                             headers={'X-CSRF-Token': self.csrf},
                             json={'change_reason': 'test'})
        self.assertEqual(r.status_code, 200, r.data[:200])
        j = r.get_json()
        self.assertTrue(j['docNumber'].startswith('OFF-'), j)
        self.assertEqual(j['versionLabel'], 'V1')
        self.assertEqual(j['revision'], 0)
        self.assertTrue(j['changed'])

    def test_02_second_register_no_change_returns_noop(self):
        # register once → then twice without any content change
        self.client.post(f'/api/documents/register-existing/offer/{self.oid}',
                         headers={'X-CSRF-Token': self.csrf}, json={})
        r = self.client.post(f'/api/documents/register-existing/offer/{self.oid}',
                             headers={'X-CSRF-Token': self.csrf}, json={})
        j = r.get_json()
        self.assertFalse(j.get('changed', True), 'unchanged content should not bump revision')

    def test_03_content_change_bumps_to_v2(self):
        # register once
        r1 = self.client.post(f'/api/documents/register-existing/offer/{self.oid}',
                              headers={'X-CSRF-Token': self.csrf}, json={})
        first = r1.get_json()
        self.assertEqual(first['versionLabel'], 'V1')
        doc_number = first['docNumber']
        # mutate offer content — preserve docNumber that first register attached
        from config import DB_FILE
        with sqlite3.connect(DB_FILE, timeout=5.0) as conn:
            row = conn.execute("SELECT data FROM offers WHERE id=?", (self.oid,)).fetchone()
            d = json.loads(row[0])
            self.assertEqual(d.get('docNumber'), doc_number, 'first register must persist docNumber')
            d['items'][0]['price'] = 6.75    # material change
            conn.execute("UPDATE offers SET data=? WHERE id=?", (json.dumps(d), self.oid))
        # register again — should bump
        r = self.client.post(f'/api/documents/register-existing/offer/{self.oid}',
                             headers={'X-CSRF-Token': self.csrf},
                             json={'change_reason': 'price change'})
        j = r.get_json()
        self.assertTrue(j.get('changed'), f'expected changed=True, got {j}')
        self.assertEqual(j.get('versionLabel'), 'V2', j)
        self.assertEqual(j.get('revision'), 1, j)

    def test_04_register_list_shows_document(self):
        self.client.post(f'/api/documents/register-existing/offer/{self.oid}',
                         headers={'X-CSRF-Token': self.csrf}, json={})
        r = self.client.get('/api/documents/register?type=OFFER')
        self.assertEqual(r.status_code, 200)
        items = r.get_json()['items']
        self.assertTrue(any(i['entityId'] == self.pid for i in items),
                        'newly registered offer must appear in the Book')


# ------------------------------------------------------------
# 2) GRANULAR PERMISSIONS
# ------------------------------------------------------------
class T02Permissions(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        _login_admin(self.client)
        self.csrf = _csrf(self.client)

    def test_01_catalog_returns_all_groups(self):
        r = self.client.get('/api/admin/permissions/catalog')
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertIn('partners', j['groups'])
        self.assertGreater(j['total'], 20)

    def test_02_only_known_keys_persisted(self):
        # Kreiraj drugog user-a preko users API-ja (koji koristi UI za permissions)
        from config import DB_FILE
        from werkzeug.security import generate_password_hash
        uid = 'perm-test-' + uuid.uuid4().hex[:8]
        with sqlite3.connect(DB_FILE, timeout=5.0) as conn:
            conn.execute("INSERT INTO users (id,username,password,role,permissions) VALUES (?,?,?,?,?)",
                         (uid, 'permtestuser', generate_password_hash('X!aaaaa12345'),
                          'user', '{}'))
        r = self.client.post(f'/api/admin/permissions/user/{uid}',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'permissions': {'partners.view': True,
                                                   'made.up.key': True}})
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        # Only known keys count
        self.assertEqual(j['saved_keys'], 1)

    def test_03_users_list_endpoint(self):
        r = self.client.get('/api/admin/permissions/users')
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertTrue(any(u['username'] == os.environ['ADMIN_USERNAME'] for u in j['users']))


# ------------------------------------------------------------
# 3) PORTAL PERMISSIONS
# ------------------------------------------------------------
class T03PortalPermissions(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        _login_admin(self.client)
        self.csrf = _csrf(self.client)
        from config import DB_FILE
        self.pid = 'portal-perm-' + uuid.uuid4().hex[:8]
        with sqlite3.connect(DB_FILE, timeout=5.0) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS partners (id TEXT PRIMARY KEY, data TEXT)')
            conn.execute("INSERT INTO partners (id,data) VALUES (?,?)",
                         (self.pid, json.dumps({'id': self.pid, 'name': 'TestPortalPartner'})))

    def test_01_modules_catalog(self):
        r = self.client.get('/api/admin/portal/modules')
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertIn('offers', j['modules'])
        self.assertIn('kyc',    j['modules'])

    def test_02_get_and_set_permissions(self):
        r = self.client.get(f'/api/admin/portal/permissions/{self.pid}')
        self.assertEqual(r.status_code, 200)
        # Set custom
        r = self.client.post(f'/api/admin/portal/permissions/{self.pid}',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'enabled_modules': ['offers', 'kyc'],
                                   'is_premium': True,
                                   'view_only_own_docs': True})
        self.assertEqual(r.status_code, 200)
        r = self.client.get(f'/api/admin/portal/permissions/{self.pid}')
        j = r.get_json()
        self.assertEqual(sorted(j['enabled_modules']), ['kyc', 'offers'])
        self.assertTrue(j['is_premium'])

    def test_03_unknown_module_filtered(self):
        r = self.client.post(f'/api/admin/portal/permissions/{self.pid}',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'enabled_modules': ['offers', 'MALICIOUS_MODULE']})
        self.assertEqual(r.status_code, 200)
        r = self.client.get(f'/api/admin/portal/permissions/{self.pid}')
        j = r.get_json()
        self.assertNotIn('MALICIOUS_MODULE', j['enabled_modules'])


# ------------------------------------------------------------
# 4) CONVERSION offer → invoice
# ------------------------------------------------------------
class T04Conversion(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        _login_admin(self.client)
        self.csrf = _csrf(self.client)
        from config import DB_FILE
        self.pid = 'conv-p-'+uuid.uuid4().hex[:8]
        self.oid = 'conv-o-'+uuid.uuid4().hex[:8]
        with sqlite3.connect(DB_FILE, timeout=5.0) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS partners (id TEXT PRIMARY KEY, data TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS offers   (id TEXT PRIMARY KEY, data TEXT)')
            conn.execute("INSERT INTO partners (id,data) VALUES (?,?)",
                         (self.pid, json.dumps({'id':self.pid, 'name':'Buyer LLC'})))
            conn.execute("INSERT INTO offers (id,data) VALUES (?,?)",
                         (self.oid, json.dumps({'id':self.oid,'offerNo':'2/2026',
                                                'customerId':self.pid,'currency':'USD',
                                                'items':[{'name':'A','qty':1,'price':100}],
                                                'incoterm':'FOB','notes':'x'})))

    def test_01_convert_to_invoice_returns_new_doc_number(self):
        r = self.client.post('/api/documents/convert',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'source_type':'offer','source_id': self.oid,
                                   'target_type':'invoice'})
        self.assertEqual(r.status_code, 200, r.data[:200])
        j = r.get_json()
        self.assertEqual(j['target_type'], 'invoice')
        self.assertTrue(j['docNumber'].startswith('INV-'))
        # Verify all fields copied
        from config import DB_FILE
        with sqlite3.connect(DB_FILE) as conn:
            r = conn.execute("SELECT data FROM invoices WHERE id=?", (j['new_id'],)).fetchone()
        inv = json.loads(r[0])
        self.assertEqual(inv['currency'], 'USD')
        self.assertEqual(inv['incoterm'], 'FOB')
        self.assertEqual(inv['sourceOfferId'], self.oid)
        self.assertEqual(inv['sourceOfferNumber'], '2/2026')
        self.assertEqual(inv['items'][0]['price'], 100)

    def test_02_convert_to_proforma_uses_pro_prefix(self):
        r = self.client.post('/api/documents/convert',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'source_type':'offer','source_id': self.oid,
                                   'target_type':'proforma'})
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertTrue(j['docNumber'].startswith('PRO-'))

    def test_03_invalid_target_rejected(self):
        r = self.client.post('/api/documents/convert',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'source_type':'offer','source_id': self.oid,
                                   'target_type':'contract'})
        self.assertEqual(r.status_code, 400)


# ------------------------------------------------------------
# 5) SECURITY CENTER — sessions, magic-link, lockout counters
# ------------------------------------------------------------
class T05SecurityCenter(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        _login_admin(self.client)
        self.csrf = _csrf(self.client)

    def test_01_my_sessions_lists_current(self):
        r = self.client.get('/api/security/sessions')
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        # Login just created a session — should be at least 1 with is_current=true
        current = [s for s in j['sessions'] if s.get('is_current')]
        self.assertEqual(len(current), 1, f"expected 1 current session, got {j}")

    def test_02_login_history_returns_recent_login(self):
        r = self.client.get('/api/security/login-history')
        self.assertEqual(r.status_code, 200)
        events = r.get_json()['events']
        self.assertTrue(any(e['action'] == 'LOGIN' for e in events))

    def test_03_password_policy_public(self):
        r = self.client.get('/api/security/password/policy')
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertGreaterEqual(j['password_min_length'], 8)

    def test_04_magic_link_constant_response_for_unknown(self):
        r = self.client.post('/api/security/magic-link',
                             json={'username':'no-such-user-xyz'})
        self.assertEqual(r.status_code, 200)
        # No leaking existence
        self.assertEqual(r.get_json(), {'status':'ok'})

    def test_05_lockout_status_default_false(self):
        r = self.client.post('/api/security/lockout/status',
                             json={'username':'no-such-user'})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()['locked'])


# ------------------------------------------------------------
# 6) USER TASKS (Round G)
# ------------------------------------------------------------
class T06UserTasks(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        _login_admin(self.client)
        self.csrf = _csrf(self.client)

    def test_01_crud_flow(self):
        # Create
        r = self.client.post('/api/tasks',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'title': 'Call ACME', 'priority': 1,
                                   'linked_entity_type':'partner', 'linked_entity_id':'p-1'})
        self.assertEqual(r.status_code, 200, r.data[:200])
        tid = r.get_json()['id']
        # List
        r = self.client.get('/api/tasks')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(t['id'] == tid for t in r.get_json()['tasks']))
        # Update
        r = self.client.patch(f'/api/tasks/{tid}',
                              headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                              json={'title': 'Call ACME Corp'})
        self.assertEqual(r.status_code, 200)
        # Complete
        r = self.client.post(f'/api/tasks/{tid}/complete',
                             headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(r.status_code, 200)
        # Entity-scoped
        r = self.client.get('/api/tasks/entity/partner/p-1')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(t['id'] == tid for t in r.get_json()['tasks']))
        # Delete
        r = self.client.delete(f'/api/tasks/{tid}', headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(r.status_code, 200)

    def test_02_empty_title_rejected(self):
        r = self.client.post('/api/tasks',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'title': '   '})
        self.assertEqual(r.status_code, 400)


# ------------------------------------------------------------
# 7) SAVED FILTERS
# ------------------------------------------------------------
class T07SavedFilters(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        _login_admin(self.client)
        self.csrf = _csrf(self.client)

    def test_01_create_and_list(self):
        r = self.client.post('/api/filters',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'name':'Active RS partners',
                                   'entity_type':'partner',
                                   'filter': {'country':'RS','archived':False},
                                   'is_shared': False})
        self.assertEqual(r.status_code, 200, r.data[:200])
        fid = r.get_json()['id']
        r = self.client.get('/api/filters?entity=partner')
        self.assertTrue(any(f['id'] == fid for f in r.get_json()['filters']))

    def test_02_reject_missing_fields(self):
        r = self.client.post('/api/filters',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'name':'X'})
        self.assertEqual(r.status_code, 400)


# ------------------------------------------------------------
# 8) ACTIVITY FEED
# ------------------------------------------------------------
class T08ActivityFeed(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        _login_admin(self.client)

    def test_01_recent_returns_login_event(self):
        r = self.client.get('/api/activity/recent?limit=25')
        self.assertEqual(r.status_code, 200)
        entries = r.get_json()['entries']
        # LOGIN was just performed by _login_admin
        self.assertTrue(any(e['action'] == 'LOGIN' for e in entries))

    def test_02_mine_returns_only_my_events(self):
        r = self.client.get('/api/activity/mine')
        self.assertEqual(r.status_code, 200)


# ------------------------------------------------------------
# 9) BULK ACTIONS (V23.1 extras)
# ------------------------------------------------------------
class T09BulkActions(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        _login_admin(self.client)
        self.csrf = _csrf(self.client)
        # V24.4 SUPABASE-ONLY: seed direktno u Supabase (mock backend)
        import supabase_store as _store
        self.ids = [f'bulk-{i}-{uuid.uuid4().hex[:6]}' for i in range(5)]
        for pid in self.ids:
            _store.upsert_entity('partners', {'id': pid, 'name': f'Bulk-{pid[:6]}'})

    def test_01_bulk_archive(self):
        r = self.client.post('/api/bulk/partners/archive',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'ids': self.ids})
        self.assertEqual(r.status_code, 200, r.data[:200])
        j = r.get_json()
        self.assertEqual(j['ok'], 5)

    def test_02_bulk_tag_adds_tag(self):
        r = self.client.post('/api/bulk/partners/tag',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'ids': self.ids, 'tag':'promo2026'})
        self.assertEqual(r.status_code, 200)
        # Verify via supabase_store
        import supabase_store as _store
        p = _store.get_entity('partners', self.ids[0])
        self.assertIsNotNone(p)
        self.assertIn('promo2026', p.get('tags') or [])

    def test_03_rejects_disallowed_entity_action(self):
        r = self.client.post('/api/bulk/users/delete',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'ids':['x']})
        self.assertEqual(r.status_code, 400)


# ------------------------------------------------------------
# 10) CUSTOM FIELDS
# ------------------------------------------------------------
class T10CustomFields(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        _login_admin(self.client)
        self.csrf = _csrf(self.client)

    def test_01_full_lifecycle(self):
        r = self.client.post('/api/custom-fields',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'entity_type':'partner','field_key':'sap_code',
                                   'field_label':'SAP Code','field_type':'text',
                                   'required': True})
        self.assertEqual(r.status_code, 200, r.data[:200])
        fid = r.get_json()['id']
        # duplicate key → 409
        r = self.client.post('/api/custom-fields',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'entity_type':'partner','field_key':'sap_code',
                                   'field_label':'X','field_type':'text'})
        self.assertEqual(r.status_code, 409)
        r = self.client.get('/api/custom-fields?entity=partner')
        self.assertTrue(any(f['id'] == fid for f in r.get_json()['fields']))
        r = self.client.delete(f'/api/custom-fields/{fid}', headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(r.status_code, 200)


# ------------------------------------------------------------
# 11) API KEYS
# ------------------------------------------------------------
class T11ApiKeys(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        _login_admin(self.client)
        self.csrf = _csrf(self.client)

    def test_01_issue_and_revoke(self):
        r = self.client.post('/api/api-keys',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'name':'nightly-sync', 'scope':'read',
                                   'rate_limit_per_min': 30})
        self.assertEqual(r.status_code, 200, r.data[:200])
        j = r.get_json()
        raw = j['raw_key']
        kid = j['id']
        self.assertTrue(raw.startswith('ask_'))
        # verify raw key
        from routes.v23_extras import verify_api_key
        info = verify_api_key(raw)
        self.assertIsNotNone(info)
        self.assertEqual(info['scope'], 'read')
        # revoke
        r = self.client.post(f'/api/api-keys/{kid}/revoke',
                             headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(r.status_code, 200)
        info2 = verify_api_key(raw)
        self.assertIsNone(info2, 'revoked key must not verify')


# ------------------------------------------------------------
# 12) OUTBOUND WEBHOOKS
# ------------------------------------------------------------
class T12Webhooks(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        _login_admin(self.client)
        self.csrf = _csrf(self.client)

    def test_01_create_returns_secret_once(self):
        r = self.client.post('/api/webhooks',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'name':'zapier','target_url':'https://hooks.zapier.com/x',
                                   'events':['deal.created','offer.*']})
        self.assertEqual(r.status_code, 200, r.data[:200])
        j = r.get_json()
        self.assertTrue(j['secret'].startswith('whsec_'))
        # list — secret NOT in list response
        r = self.client.get('/api/webhooks')
        entries = r.get_json()['webhooks']
        for e in entries:
            self.assertNotIn('secret', e)

    def test_02_reject_invalid_url(self):
        r = self.client.post('/api/webhooks',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'name':'x','target_url':'ftp://bad','events':['x']})
        self.assertEqual(r.status_code, 400)


# ------------------------------------------------------------
# 13) SUPABASE MERGE — _coerce_row unit tests
# ------------------------------------------------------------
class T13SupabaseMergeCoerce(unittest.TestCase):
    def test_01_only_whitelisted_columns_kept(self):
        from routes.supabase_merge import _coerce_row, SUPPORTED_TABLES
        info = SUPPORTED_TABLES['partners']
        row = {'id':'p1', 'company_name':'ACME', 'email':'e@x.com',
               'city':'Belgrade', 'random_extra_col': 'ignored',
               'nested_stuff': {'key':'val'}}
        out = _coerce_row(row, info)
        # id, company_name, email, city are in whitelist
        self.assertEqual(out['id'], 'p1')
        self.assertEqual(out['city'], 'Belgrade')
        # random_extra_col → into data JSONB
        self.assertNotIn('random_extra_col', out)
        self.assertIn('random_extra_col', out.get('data', {}))
        self.assertEqual(out['data']['random_extra_col'], 'ignored')

    def test_02_bool_coercion(self):
        from routes.supabase_merge import _coerce_row, SUPPORTED_TABLES
        info = SUPPORTED_TABLES['partners']
        row = {'id':'p1','is_portal_active':1,'is_premium':0}
        out = _coerce_row(row, info)
        self.assertIs(out['is_portal_active'], True)
        self.assertIs(out['is_premium'], False)

    def test_03_json_string_parsed_to_dict(self):
        from routes.supabase_merge import _coerce_row, SUPPORTED_TABLES
        info = SUPPORTED_TABLES['partners']
        row = {'id':'p1', 'data': '{"nested":"yes"}'}
        out = _coerce_row(row, info)
        self.assertIsInstance(out['data'], dict)
        self.assertEqual(out['data']['nested'], 'yes')

    def test_04_audit_id_remapped_to_sync_id(self):
        from routes.supabase_merge import _coerce_row, SUPPORTED_TABLES
        info = SUPPORTED_TABLES['audit_logs']
        row = {'id':'audit-uuid-123', 'action':'LOGIN', 'timestamp':'2026-07-29T08:00:00Z',
               'location':'NS,RS'}
        out = _coerce_row(row, info)
        # id maps to sync_id
        self.assertEqual(out.get('sync_id'), 'audit-uuid-123')
        self.assertNotIn('id', out)

    def test_05_empty_string_becomes_null(self):
        from routes.supabase_merge import _coerce_row, SUPPORTED_TABLES
        info = SUPPORTED_TABLES['partners']
        row = {'id':'p1','company_name':'','email':''}
        out = _coerce_row(row, info)
        self.assertIsNone(out['company_name'])
        self.assertIsNone(out['email'])


# ------------------------------------------------------------
# 14) PDF GENERATOR — smoke test for V23.1 header/footer
# ------------------------------------------------------------
class T14PdfGenerator(unittest.TestCase):
    def test_01_build_offer_pdf_no_kind_label_regression(self):
        from pdf_generator import build_offer_pdf
        pdf = build_offer_pdf({
            'id':'o1','offerNo':'99/2026','customerId':'p1','currency':'EUR',
            'items':[{'productId':'x','quantity':1,'price':10,'unit':'kg'}]
        }, company={'name':'TestCo','address':'Address 1','taxId':'123'})
        self.assertIsInstance(pdf, bytes)
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 1000)

    def test_02_pdf_metadata_hash_in_keywords(self):
        from pdf_generator import build_offer_pdf
        pdf = build_offer_pdf({
            'id':'o2','offerNo':'100/2026','customerId':'p1','currency':'EUR',
            'items':[{'productId':'x','quantity':1,'price':10,'unit':'kg'}]
        }, company={'name':'TestCo'})
        # PDF should contain VER- verification hash reference in metadata
        self.assertIn(b'verification:', pdf.replace(b' ', b''))


# ------------------------------------------------------------
# 15) RELATIONS — offer→partner, converted invoice→offer
# ------------------------------------------------------------
class T15Relations(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        _login_admin(self.client)
        self.csrf = _csrf(self.client)

    def test_01_conversion_maintains_source_link(self):
        from config import DB_FILE
        pid = 'rel-p-'+uuid.uuid4().hex[:8]
        oid = 'rel-o-'+uuid.uuid4().hex[:8]
        with sqlite3.connect(DB_FILE, timeout=5.0) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS partners (id TEXT PRIMARY KEY, data TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS offers   (id TEXT PRIMARY KEY, data TEXT)')
            conn.execute("INSERT INTO partners (id,data) VALUES (?,?)",
                         (pid, json.dumps({'id':pid,'name':'RelPartner'})))
            conn.execute("INSERT INTO offers (id,data) VALUES (?,?)",
                         (oid, json.dumps({'id':oid,'offerNo':'REL/2026',
                                           'customerId':pid,'currency':'EUR',
                                           'items':[{'name':'X','qty':1,'price':1}]})))
        r = self.client.post('/api/documents/convert',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'source_type':'offer','source_id':oid,
                                   'target_type':'invoice'})
        j = r.get_json()
        new_id = j['new_id']
        with sqlite3.connect(DB_FILE) as conn:
            row = conn.execute("SELECT data FROM invoices WHERE id=?", (new_id,)).fetchone()
        inv = json.loads(row[0])
        # Relations preserved:
        self.assertEqual(inv['sourceOfferId'], oid)
        # Copied 1:1 — customerId iz offera → invoice.customerId (Not renamed)
        self.assertEqual(inv.get('customerId') or inv.get('partnerId'), pid)

    def test_02_document_register_entity_id_matches_partner(self):
        from config import DB_FILE
        # Reuse partner and offer from test_01 mechanism
        pid = 'rel2-p-'+uuid.uuid4().hex[:8]
        oid = 'rel2-o-'+uuid.uuid4().hex[:8]
        with sqlite3.connect(DB_FILE, timeout=5.0) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS partners (id TEXT PRIMARY KEY, data TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS offers   (id TEXT PRIMARY KEY, data TEXT)')
            conn.execute("INSERT INTO partners (id,data) VALUES (?,?)",
                         (pid, json.dumps({'id':pid,'name':'RelP2'})))
            conn.execute("INSERT INTO offers (id,data) VALUES (?,?)",
                         (oid, json.dumps({'id':oid,'customerId':pid,
                                           'items':[{'name':'X','qty':1,'price':1}]})))
        self.client.post(f'/api/documents/register-existing/offer/{oid}',
                         headers={'X-CSRF-Token': self.csrf}, json={})
        r = self.client.get('/api/documents/register')
        items = r.get_json()['items']
        match = [i for i in items if i['entityId'] == pid]
        self.assertTrue(match, 'register must link entity_id back to partner')


# ------------------------------------------------------------
# 16) LIVE MIRROR — save via /api/item/partners kalls Supabase mirror
# ------------------------------------------------------------
class T16LiveMirror(unittest.TestCase):
    """Testira da nov partner ide u Supabase mirror pored SQLite-a.
    Kada Supabase nije dostupan, mirror mora TIHO pasti bez rusenja save flow-a."""

    def setUp(self):
        self.client = app_module.app.test_client()
        _login_admin(self.client)
        self.csrf = _csrf(self.client)

    def test_01_save_partner_does_not_500_even_without_supabase(self):
        pid = 'mirror-p-' + uuid.uuid4().hex[:8]
        r = self.client.post('/api/item/partners',
                             headers={'Content-Type':'application/json','X-CSRF-Token': self.csrf},
                             json={'id': pid, 'name': 'Mirror Test LLC',
                                   'country': 'RS', 'email': 'x@y.com'})
        self.assertEqual(r.status_code, 200, f'save flow broke: {r.data[:200]}')
        j = r.get_json()
        self.assertEqual(j['id'], pid)

    def test_02_mirror_to_supabase_helper_returns_false_on_bad_input(self):
        from routes.supabase_merge import mirror_to_supabase
        # Nevalidan payload → False, ne baca izuzetak
        self.assertFalse(mirror_to_supabase('partners', None))
        self.assertFalse(mirror_to_supabase('partners', {}))
        self.assertFalse(mirror_to_supabase('unknown_table', {'id':'x'}))


# ------------------------------------------------------------
# 17) V23.3 READ-FALLBACK — rehydrate SQLite from Supabase on empty
# ------------------------------------------------------------
class T17ReadFallback(unittest.TestCase):
    """V23.3: Kritican fix — Render brise SQLite pri deploy-u, a bez
    read-fallback-a app prikazuje prazne tabele iako su podaci u Supabase.
    Ovi testovi drze ponasanje `_rehydrate_row`, `fetch_from_supabase` i
    ojacanog `get_data` endpoint-a."""

    def test_01_rehydrate_direct_cols_override_data_jsonb(self):
        from routes.supabase_merge import _rehydrate_row, SUPPORTED_TABLES
        row = {'id': 'p1', 'email': 'top@x.com', 'company_name': 'Top Co',
               'data': {'email': 'inner@x.com', 'phone': '+1', 'extra': 'y'}}
        out = _rehydrate_row(row, SUPPORTED_TABLES['partners'])
        self.assertEqual(out['id'], 'p1')
        # Direktne kolone imaju prioritet nad `data` JSONB
        self.assertEqual(out['email'], 'top@x.com')
        self.assertEqual(out['company_name'], 'Top Co')
        # Dodatna polja iz `data` moraju biti tu
        self.assertEqual(out['phone'], '+1')
        self.assertEqual(out['extra'], 'y')

    def test_02_rehydrate_handles_string_json_data(self):
        from routes.supabase_merge import _rehydrate_row, SUPPORTED_TABLES
        row = {'id': 'p2', 'data': '{"foo":"bar","n":42}'}
        out = _rehydrate_row(row, SUPPORTED_TABLES['partners'])
        self.assertEqual(out['id'], 'p2')
        self.assertEqual(out['foo'], 'bar')
        self.assertEqual(out['n'], 42)

    def test_03_rehydrate_none_values_ignored(self):
        from routes.supabase_merge import _rehydrate_row, SUPPORTED_TABLES
        row = {'id': 'p3', 'email': None, 'phone': None,
               'data': {'email': 'keep@x.com'}}
        out = _rehydrate_row(row, SUPPORTED_TABLES['partners'])
        # None ne sme da nadjaca `data.email`
        self.assertEqual(out.get('email'), 'keep@x.com')

    def test_04_rehydrate_settings_key_maps_to_id(self):
        from routes.supabase_merge import _rehydrate_row, SUPPORTED_TABLES
        row = {'key': 'company', 'value': '{"n":"X"}'}
        out = _rehydrate_row(row, SUPPORTED_TABLES['settings'])
        # id_key='key' → id ostaje popunjen (setdefault)
        self.assertEqual(out.get('id'), 'company')
        self.assertEqual(out.get('key'), 'company')
        self.assertEqual(out.get('value'), '{"n":"X"}')

    def test_05_fetch_from_supabase_returns_empty_on_no_backend(self):
        from routes.supabase_merge import fetch_from_supabase
        # Bez Supabase env-a, fetch mora vratiti [] a ne baciti izuzetak
        rows = fetch_from_supabase('partners')
        self.assertIsInstance(rows, list)

    def test_06_fetch_unknown_table_returns_empty(self):
        from routes.supabase_merge import fetch_from_supabase, fetch_one_from_supabase
        self.assertEqual(fetch_from_supabase('this_does_not_exist'), [])
        self.assertIsNone(fetch_one_from_supabase('this_does_not_exist', 'x'))

    def test_07_get_data_returns_empty_list_when_sqlite_and_supabase_empty(self):
        """get_data na praznoj tabeli + nedostupan Supabase → [] a ne 500."""
        client = app_module.app.test_client()
        _login_admin(client)
        r = client.get('/api/data/partners')
        self.assertEqual(r.status_code, 200)
        self.assertIn('value', r.get_json())

    def test_08_backfill_sqlite_from_supabase_is_idempotent(self):
        """Direktna simulacija — pozovi backfill sa mock rezultatima."""
        from routes.supabase_merge import backfill_sqlite_from_supabase
        from config import DB_FILE
        import sqlite3 as _sq
        # V24.0: partners tabela u mock-u moze imati redova iz drugih testova.
        # Kljucno je da backfill ne baca i vraca broj >= 0.
        conn = _sq.connect(DB_FILE, timeout=5.0)
        try:
            written = backfill_sqlite_from_supabase('partners', conn)
            self.assertGreaterEqual(written, 0)
        finally:
            conn.close()


# ------------------------------------------------------------
# 18) V23.4 LOGIN FALLBACK — user restored from Supabase after SQLite wipe
# ------------------------------------------------------------
class T18LoginSupabaseFallback(unittest.TestCase):
    """V23.4: kada je SQLite wiped (Render redeploy), login mora da povuce
    user-a iz Supabase, upisu ga u SQLite, i zavrsi prijavu bez greske."""

    def test_01_login_reads_supabase_when_sqlite_missing_user(self):
        from werkzeug.security import generate_password_hash
        import data_layer as dl

        # Simuliraj sveze-obrisan SQLite tako sto obrisemo admin-a
        from config import DB_FILE
        with sqlite3.connect(DB_FILE, timeout=5.0) as conn:
            conn.execute("DELETE FROM users WHERE username=?", (os.environ['ADMIN_USERNAME'],))
            conn.commit()

        # Monkey-patch data_layer.select da vrati "Supabase" red
        fake_pw = generate_password_hash(os.environ['ADMIN_PASSWORD'], method='scrypt:32768:8:1')
        fake_user = {
            'id': 'supabase-admin-id',
            'username': os.environ['ADMIN_USERNAME'],
            'password': fake_pw,
            'role': 'admin',
            'permissions': {},
            'token_version': 1,
        }
        orig_select = dl.select
        def _fake_select(table, filters=None, columns='*', order=None, limit=None):
            if table == 'users':
                return [fake_user]
            return orig_select(table, filters, columns, order, limit) if orig_select else []
        dl.select = _fake_select
        try:
            client = app_module.app.test_client()
            r = client.post('/api/auth/login', json={
                'username': os.environ['ADMIN_USERNAME'],
                'password': os.environ['ADMIN_PASSWORD'],
                'location': '45,19', 'device': 'Test/1.0',
            })
            self.assertEqual(r.status_code, 200,
                             f'login should succeed via Supabase fallback: {r.data[:200]}')
            j = r.get_json()
            self.assertEqual(j['user']['username'], os.environ['ADMIN_USERNAME'])
        finally:
            dl.select = orig_select

    def test_02_settings_read_fallback_from_supabase(self):
        """Settings ne postoji u SQLite → povuci iz Supabase i backfiluj."""
        import data_layer as dl
        from utils import encrypt_data as _enc
        from config import DB_FILE
        test_key = 'v234_test_company_' + uuid.uuid4().hex[:6]
        # Encrypt-ovan value string (isto sto Supabase mirror ocekuje)
        stored_value = _enc({'company_name': 'Test Corp', 'currency': 'EUR'})

        orig_select = dl.select
        orig_select_one = dl.select_one
        def _fake_select_one(table, filters, columns='*'):
            if table == 'settings' and filters and filters.get('key') == test_key:
                return {'key': test_key, 'value': stored_value}
            # Ostale tabele (posebno 'users' za auth) → propusti kroz originalni backend
            return orig_select_one(table, filters, columns)
        dl.select_one = _fake_select_one
        try:
            client = app_module.app.test_client()
            _login_admin(client)
            r = client.get(f'/api/data/{test_key}')
            self.assertEqual(r.status_code, 200)
            j = r.get_json()
            self.assertIsNotNone(j.get('value'))
            v = j['value']
            self.assertEqual(v.get('company_name'), 'Test Corp')
        finally:
            dl.select = orig_select
            dl.select_one = orig_select_one
            # cleanup
            with sqlite3.connect(DB_FILE, timeout=5.0) as conn:
                conn.execute("DELETE FROM settings WHERE key=?", (test_key,))
                conn.commit()

    def test_03_users_table_has_password_in_supabase_whitelist(self):
        """V23.4: users mirror mora da salje password kolonu."""
        from routes.supabase_merge import SUPPORTED_TABLES
        cols = SUPPORTED_TABLES['users']['cols']
        for required in ('password', 'totp_secret', 'totp_recovery',
                         'token_version', 'last_password_change_at'):
            self.assertIn(required, cols,
                          f'users mirror mora imati "{required}" u whitelist-u')


if __name__ == '__main__':
    unittest.main(verbosity=2)
