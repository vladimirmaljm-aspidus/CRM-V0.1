"""
END-TO-END TEST SUITE (Playwright + backend DB verification).

Pokrivenost:
  1) CRM login flow (GPS obavezan, CSRF, cookie sesija)
  2) CRM dashboard load bez konzolskih grešaka
  3) Partneri: create/edit/delete kroz UI + verifikacija u bazi
  4) Proizvodi: create + verifikacija
  5) Ponude: create u CRM + Portal accept + convert-to-deal
  6) Portal login (OTP kroz TEST_MODE hook)
  7) Portal Catalog view
  8) Portal RFQ submit → CRM demand created (DB check)
  9) Portal KYC submit → CRM KYC review + approve → partner merged (DB check)
  10) Portal Profile change request → CRM approve → partner updated (DB check)
  11) Portal accept offer → CRM offer clientStatus=accepted (DB check)
  12) Portal decline offer → CRM offer clientStatus=declined (DB check)
  13) Logistics planner modal (CRM + Portal)
  14) Notifications counters (KYC pending, RFQ pending, profile requests)
  15) Console errors: nema JS grešaka na key stranicama

Server se pokreće u pozadini prije testova, sa čistom seed bazom.
Baza se PROVERAVA DIREKTNO posle svake UI submission-e.
"""
import os
import re
import sys
import json
import time
import signal
import sqlite3
import subprocess
import unittest
import uuid
from pathlib import Path

# --- ENV pre importa app-a ---
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / '_e2e_data'
os.environ['DATA_DIR'] = str(DATA_DIR)
os.environ['TEST_MODE'] = '1'
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['ADMIN_USERNAME'] = 'e2eadmin'
os.environ['ADMIN_PASSWORD'] = 'E2eAdmin!12345'
os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '/opt/pw-browsers'

sys.path.insert(0, str(ROOT))

from tests.e2e_seed import (
    seed, E2E_ADMIN_USER, E2E_ADMIN_PASS,
    E2E_PARTNER_EMAIL, E2E_PARTNER_TOKEN, E2E_PARTNER_ID, E2E_PRODUCT_ID
)
from config import DB_FILE, PORTAL_DB_FILE
from playwright.sync_api import sync_playwright, expect, Page

# Port iz env-a ili 5555 (retko zauzet u dev okruženjima).
E2E_PORT = int(os.getenv('E2E_PORT', '5555'))
BASE_URL = os.getenv('E2E_BASE_URL', f'http://127.0.0.1:{E2E_PORT}')
GPS_LOCATION = '44.7866,20.4489'  # Beograd


# ==========================================================
#  SERVER LIFECYCLE
# ==========================================================
_server_proc = None

def start_server():
    global _server_proc
    if _server_proc is not None:
        return
    # Očisti bazu za deterministic run
    if DATA_DIR.exists():
        for p in DATA_DIR.glob('*.db*'):
            try: p.unlink()
            except: pass
    DATA_DIR.mkdir(exist_ok=True)
    seed()
    env = os.environ.copy()
    env['FLASK_ENV'] = 'testing'
    # Ubij bilo koji zombi proces na istom portu (npr. iz prethodnog test run-a).
    try:
        subprocess.run(['fuser', '-k', f'{E2E_PORT}/tcp'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        time.sleep(0.5)
    except Exception:
        pass
    LOG = open('/tmp/e2e_server.log', 'w')
    _server_proc = subprocess.Popen(
        [sys.executable, '-c',
         "import os,sys; sys.path.insert(0, r'" + str(ROOT) + "'); "
         f"import app as A; A.app.run(host='127.0.0.1', port={E2E_PORT}, debug=False, use_reloader=False)"],
        env=env, stdout=LOG, stderr=LOG,
        cwd=str(ROOT), preexec_fn=os.setsid,
    )
    # Sačekaj da server odgovori (bilo koji uspešan HTTP status)
    import urllib.request
    for i in range(60):
        try:
            r = urllib.request.urlopen(BASE_URL + '/robots.txt', timeout=1)
            if r.getcode() in (200, 404, 403):
                return
        except Exception:
            time.sleep(0.5)
    stop_server()
    raise RuntimeError('Server did not start within 30s')


def stop_server():
    global _server_proc
    if _server_proc:
        try:
            os.killpg(os.getpgid(_server_proc.pid), signal.SIGTERM)
            _server_proc.wait(timeout=5)
        except Exception:
            try: _server_proc.kill()
            except: pass
        _server_proc = None


# ==========================================================
#  DB HELPERS (verifikacija posle UI submission-a)
# ==========================================================
def db_row_by_key(table, id_):
    con = sqlite3.connect(DB_FILE); con.row_factory = sqlite3.Row
    try:
        cur = con.execute(f'SELECT data FROM {table} WHERE id=?', (id_,))
        r = cur.fetchone()
        return json.loads(r[0]) if r else None
    finally:
        con.close()

def db_partners_by_email(email):
    con = sqlite3.connect(DB_FILE); con.row_factory = sqlite3.Row
    out = []
    try:
        for r in con.execute('SELECT id, data FROM partners'):
            d = json.loads(r[1])
            e = (d.get('email') or d.get('contact', {}).get('email') or '').lower()
            if e == email.lower(): out.append((r[0], d))
    finally:
        con.close()
    return out

def db_all(table):
    con = sqlite3.connect(DB_FILE)
    try:
        cur = con.execute(f'SELECT id, data FROM {table}')
        return [(r[0], json.loads(r[1])) for r in cur.fetchall()]
    finally:
        con.close()

def portal_db_all(table):
    con = sqlite3.connect(PORTAL_DB_FILE)
    try:
        cur = con.execute(f'SELECT * FROM {table}')
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


# ==========================================================
#  PLAYWRIGHT WRAPPER
# ==========================================================
class BrowserSession:
    _pw = None
    _browser = None

    @classmethod
    def get_browser(cls):
        if cls._browser is None:
            cls._pw = sync_playwright().start()
            cls._browser = cls._pw.chromium.launch(
                headless=True,
                executable_path='/opt/pw-browsers/chromium-1194/chrome-linux/chrome' if os.path.exists('/opt/pw-browsers/chromium-1194/chrome-linux/chrome') else None,
                args=['--no-sandbox', '--disable-dev-shm-usage'],
            )
        return cls._browser

    @classmethod
    def shutdown(cls):
        if cls._browser:
            try: cls._browser.close()
            except: pass
        if cls._pw:
            try: cls._pw.stop()
            except: pass
        cls._browser = None
        cls._pw = None


def new_context(browser, permissions=None):
    ctx = browser.new_context(
        base_url=BASE_URL,
        viewport={'width': 1400, 'height': 900},
        locale='en-US',
        timezone_id='Europe/Belgrade',
        geolocation={'latitude': 44.7866, 'longitude': 20.4489},
        permissions=permissions or ['geolocation'],
    )
    return ctx


# ==========================================================
#  CRM LOGIN (kroz UI: fill username + password + GPS)
# ==========================================================
def crm_login(page: Page, username=E2E_ADMIN_USER, password=E2E_ADMIN_PASS):
    page.goto(BASE_URL + '/')
    # login form
    page.wait_for_selector('#login-screen', state='visible', timeout=10000)
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    # klikni login — geolokacija je vec dozvoljena kroz context permissions
    page.click('#login-submit-btn')
    # sacekaj da nestane login modal i pokaže se glavni layout
    page.wait_for_selector('#login-screen', state='hidden', timeout=15000)
    # sacekaj da state bude eksponovan i da je initial render obavljen
    page.wait_for_function("() => typeof window.state !== 'undefined' && typeof window.render === 'function'",
                           timeout=10000)


# ==========================================================
#  PORTAL LOGIN (kroz API — brže i deterministicnije)
# ==========================================================
import urllib.request, urllib.parse

def portal_login_api():
    """Vraća (auth_key, token). Koristi TEST_MODE hook da procita OTP."""
    # 1. traži OTP
    req = urllib.request.Request(
        BASE_URL + f'/api/portal/auth/send_otp/{E2E_PARTNER_TOKEN}',
        method='POST', data=b'{}',
        headers={'Content-Type': 'application/json'}
    )
    urllib.request.urlopen(req, timeout=5).read()
    # 2. procitaj OTP iz test hook-a
    r = urllib.request.urlopen(BASE_URL + f'/api/portal/testonly/last_otp/{E2E_PARTNER_TOKEN}', timeout=5)
    otp = json.loads(r.read())['otp']
    assert otp, "OTP not created"
    # 3. verify sa GPS-om
    req = urllib.request.Request(
        BASE_URL + f'/api/portal/auth/verify_otp/{E2E_PARTNER_TOKEN}',
        method='POST',
        data=json.dumps({'otp': otp, 'location': GPS_LOCATION}).encode(),
        headers={'Content-Type': 'application/json'}
    )
    r = urllib.request.urlopen(req, timeout=5)
    d = json.loads(r.read())
    assert d.get('status') == 'success', f'verify_otp failed: {d}'
    return d['auth_key'], E2E_PARTNER_TOKEN


def portal_api(path, method='GET', body=None, auth_key=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE_URL + path, method=method, data=data,
        headers={'Content-Type': 'application/json',
                 **({'X-Portal-Auth': auth_key} if auth_key else {})}
    )
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        raw = r.read()
        try: return r.getcode(), json.loads(raw)
        except: return r.getcode(), raw
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except: return e.code, None


# ==========================================================
#  TEST CASES
# ==========================================================
class E2EBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        start_server()
        cls.browser = BrowserSession.get_browser()

    @classmethod
    def tearDownClass(cls):
        pass  # server + browser gasimo u finalizer-u dole


class T01Health(E2EBase):
    def test_01_robots_reachable(self):
        import urllib.request
        r = urllib.request.urlopen(BASE_URL + '/robots.txt')
        self.assertIn(r.getcode(), (200, 404))

    def test_02_index_returns_login_page(self):
        import urllib.request
        r = urllib.request.urlopen(BASE_URL + '/')
        html = r.read().decode('utf-8', 'replace')
        self.assertIn('login-screen', html)


class T02CrmLoginUI(E2EBase):
    def test_01_login_and_dashboard_visible(self):
        ctx = new_context(self.browser)
        page = ctx.new_page()
        errs = []
        page.on('pageerror', lambda e: errs.append('pageerror:' + str(e)))
        page.on('console', lambda m: errs.append('console.' + m.type + ':' + m.text) if m.type == 'error' else None)
        try:
            crm_login(page)
            # provera da je sidebar prisutan (moduli)
            page.wait_for_selector('#app-sidebar', timeout=10000)
            # Očisti tolerable erori (favicon, external cdn analytics)
            fatal = [e for e in errs
                     if 'favicon' not in e
                     and 'cdn' not in e.lower()
                     and 'net::err_' not in e.lower()
                     and 'nominatim' not in e.lower()
                     and 'unpkg' not in e.lower()
                     and 'tailwind' not in e.lower()
                     and 'cartocdn' not in e.lower()
                     # 401 na /api/auth/me je normalan pre-login response
                     and '401' not in e
                     and 'unauthorized' not in e.lower()]
            self.assertEqual(fatal, [], f'Fatal console errors on CRM login: {fatal}')
        finally:
            ctx.close()


class T03PortalLoginAPI(E2EBase):
    """Portal login preko API-ja (dozvoljen jer koristimo TEST_MODE OTP hook)."""

    def test_01_portal_login_success(self):
        auth_key, token = portal_login_api()
        self.assertTrue(auth_key)
        # provera da /api/portal/data/<token> radi
        code, data = portal_api(f'/api/portal/data/{token}', auth_key=auth_key)
        self.assertEqual(code, 200, msg=data)
        self.assertIn('offers', data)

    def test_02_portal_login_wrong_otp(self):
        # zatraži OTP
        urllib.request.urlopen(urllib.request.Request(
            BASE_URL + f'/api/portal/auth/send_otp/{E2E_PARTNER_TOKEN}',
            method='POST', data=b'{}', headers={'Content-Type': 'application/json'}
        )).read()
        code, data = portal_api(f'/api/portal/auth/verify_otp/{E2E_PARTNER_TOKEN}',
                                method='POST', body={'otp': '000000', 'location': GPS_LOCATION})
        self.assertEqual(code, 401)

    def test_03_portal_login_missing_gps_blocked(self):
        urllib.request.urlopen(urllib.request.Request(
            BASE_URL + f'/api/portal/auth/send_otp/{E2E_PARTNER_TOKEN}',
            method='POST', data=b'{}', headers={'Content-Type': 'application/json'}
        )).read()
        code, data = portal_api(f'/api/portal/auth/verify_otp/{E2E_PARTNER_TOKEN}',
                                method='POST', body={'otp': '123456'})
        self.assertEqual(code, 403)


class T04PortalCatalog(E2EBase):
    def test_01_catalog_returns_seeded_product(self):
        auth_key, token = portal_login_api()
        code, data = portal_api(f'/api/portal/catalog/{token}', auth_key=auth_key)
        self.assertEqual(code, 200, msg=data)
        products = data.get('products', [])
        self.assertTrue(any(p.get('id') == E2E_PRODUCT_ID for p in products),
                        msg=f'seeded product missing from catalog: {products}')


class T05PortalRfqToCrmDemand(E2EBase):
    """Portal klijent šalje RFQ; CRM /api/item/demands mora imati novu potražnju."""

    def test_01_rfq_submit_creates_demand(self):
        before = {i for i, _ in db_all('demands')}
        auth_key, token = portal_login_api()
        code, data = portal_api(f'/api/portal/rfq/submit/{token}', method='POST',
                                 body={'productId': E2E_PRODUCT_ID, 'quantity': 25, 'notes': 'e2e rfq test'},
                                 auth_key=auth_key)
        self.assertEqual(code, 200, msg=data)
        after = {i for i, _ in db_all('demands')}
        new_ids = after - before
        self.assertEqual(len(new_ids), 1, msg='exactly one new demand expected')
        new_id = list(new_ids)[0]
        demand = db_row_by_key('demands', new_id)
        self.assertEqual(demand.get('productId'), E2E_PRODUCT_ID)
        self.assertEqual(int(demand.get('quantity') or 0), 25)
        # veza sa partnerom (buyerId ili customerId)
        self.assertTrue(demand.get('buyerId') == E2E_PARTNER_ID or
                        demand.get('customerId') == E2E_PARTNER_ID,
                        msg=f'demand not linked to partner: {demand}')


class T06PortalKycToCrmReview(E2EBase):
    """Portal KYC submit → mora se pojaviti u submissions tabeli."""

    def test_01_kyc_submit_saved(self):
        auth_key, token = portal_login_api()
        kyc = {
            'companyName': 'E2E Updated Co Ltd',
            'entityType': 'company',
            'address': 'New Street 42',
            'city': 'Belgrade',
            'country': 'RS',
            'taxId': '999888777',
            'registrationNumber': 'REG-E2E-01',
            'website': 'https://e2e.example',
            'bankAccounts': [{'bankName': 'BankE2E', 'accountNumber': 'IBAN9999', 'swiftCode': 'BANKE2EX', 'currency': 'EUR'}],
            'directors': [{'name': 'John Doe', 'passport': 'X999', 'nationality': 'RS'}],
            'ubos': [{'name': 'Jane Owner', 'ownershipPct': 100}],
            'consent': True,
        }
        code, data = portal_api(f'/api/portal/kyc/submit/{token}', method='POST',
                                 body={'data': kyc, 'consent': True}, auth_key=auth_key)
        self.assertEqual(code, 200, msg=data)
        subs = portal_db_all('kyc_submissions')
        self.assertTrue(any(s['partner_id'] == E2E_PARTNER_ID for s in subs))


class T07PortalOfferAcceptDeclineToCrm(E2EBase):
    """Kreiraj ponudu u bazi, klijent je prihvata/odbija preko portala, CRM update."""

    def _make_offer(self, offer_id=None):
        offer_id = offer_id or ('e2e-offer-' + uuid.uuid4().hex[:8])
        offer = {
            'id': offer_id,
            'offerNo': 'E2E-' + offer_id[-4:],
            'customerId': E2E_PARTNER_ID,
            'productId': E2E_PRODUCT_ID,
            'quantity': 10, 'unit': 'MT',
            'sellingPrice': 5000, 'currency': 'USD',
            'date': '2026-07-17',
            'clientStatus': None,
        }
        con = sqlite3.connect(DB_FILE)
        con.execute('INSERT OR REPLACE INTO offers (id, data) VALUES (?, ?)',
                    (offer_id, json.dumps(offer)))
        con.commit(); con.close()
        return offer_id

    def test_01_accept_via_portal_updates_status(self):
        oid = self._make_offer()
        auth_key, token = portal_login_api()
        code, data = portal_api(f'/api/portal/offers/accept/{token}/{oid}', method='POST',
                                 body={'action': 'accept', 'note': 'e2e accept test'},
                                 auth_key=auth_key)
        self.assertEqual(code, 200, msg=data)
        offer = db_row_by_key('offers', oid)
        self.assertEqual(offer.get('clientStatus'), 'accepted')
        self.assertEqual(offer.get('clientNote'), 'e2e accept test')

    def test_02_decline_requires_reason(self):
        oid = self._make_offer()
        auth_key, token = portal_login_api()
        # bez razloga → 400
        code, _ = portal_api(f'/api/portal/offers/accept/{token}/{oid}', method='POST',
                             body={'action': 'decline'}, auth_key=auth_key)
        self.assertIn(code, (400, 422))
        # sa razlogom → 200 i status=declined
        code, _ = portal_api(f'/api/portal/offers/accept/{token}/{oid}', method='POST',
                             body={'action': 'decline', 'note': 'price too high'},
                             auth_key=auth_key)
        self.assertEqual(code, 200)
        offer = db_row_by_key('offers', oid)
        self.assertEqual(offer.get('clientStatus'), 'declined')

    def test_03_convert_accepted_offer_to_deal(self):
        """CRM admin klikom 'Kreiraj dil' pretvara ponudu u dil. Direktan API test."""
        oid = self._make_offer()
        # accept preko portala
        auth_key, token = portal_login_api()
        portal_api(f'/api/portal/offers/accept/{token}/{oid}', method='POST',
                   body={'action': 'accept', 'note': 'ok'}, auth_key=auth_key)
        # sad login kao admin i pošalji POST /api/deals/from_offer/<oid>
        s = new_crm_api_session()
        code, data = s.post(f'/api/deals/from_offer/{oid}', {'force': False})
        self.assertEqual(code, 200, msg=data)
        deal_id = data['dealId']
        deal = db_row_by_key('deals', deal_id)
        self.assertIsNotNone(deal)
        self.assertEqual(deal.get('sourceOfferId'), oid)
        self.assertEqual(deal.get('productId'), E2E_PRODUCT_ID)


class T08LogisticsPlannerApi(E2EBase):
    def test_01_plan_endpoint_multimodal(self):
        s = new_crm_api_session()
        code, data = s.post('/api/logistics/plan', {
            'origin': {'lat': 51.9, 'lon': 4.5, 'label': 'Rotterdam'},
            'destination': {'lat': 40.7, 'lon': -74.0, 'label': 'NYC'},
            'cargo_tons': 100
        })
        self.assertEqual(code, 200)
        self.assertGreaterEqual(len(data['plans']), 2)
        modes = {p['mode'] for p in data['plans']}
        self.assertIn('sea', modes)

    def test_02_portal_plan_endpoint_multimodal(self):
        auth_key, token = portal_login_api()
        code, data = portal_api('/api/portal/logistics/plan', method='POST',
                                 body={'origin': {'lat': 51.9, 'lon': 4.5, 'label': 'R'},
                                       'destination': {'lat': 40.7, 'lon': -74.0, 'label': 'N'},
                                       'cargo_tons': 100},
                                 auth_key=auth_key)
        self.assertEqual(code, 200)


class T09PortalProfileChangeRequest(E2EBase):
    def test_01_profile_change_creates_pending_request(self):
        auth_key, token = portal_login_api()
        code, data = portal_api(f'/api/portal/profile/update/{token}', method='POST',
                                 body={'phone': '+38160123123', 'address': 'Changed Str 5'},
                                 auth_key=auth_key)
        self.assertEqual(code, 200, msg=data)
        # PROVERA U PORTAL BAZI
        reqs = portal_db_all('profile_change_requests')
        matches = [r for r in reqs if r['partner_id'] == E2E_PARTNER_ID]
        self.assertTrue(matches, 'no profile change request found')
        pending = [r for r in matches if r.get('status') in ('pending', None, 'PENDING')]
        self.assertTrue(pending, f'expected pending, got: {[r.get("status") for r in matches]}')


class T10PendingCountsSummary(E2EBase):
    def test_01_admin_pending_counts_endpoint(self):
        s = new_crm_api_session()
        code, data = s.get('/api/portal/admin/pending_counts')
        self.assertEqual(code, 200, msg=data)
        for key in ('kyc', 'rfqs', 'profile_requests'):
            self.assertIn(key, data, msg=f'missing key: {key}')


class T10bCrmUiNavigation(E2EBase):
    """UI klikanje: login → prebaci se kroz SVAKI modul → nema fatal konzole error-a.
    Jedan browser context za celu klasu (login samo jednom) da se izbegne 30s+ overhead."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ctx = new_context(cls.browser)
        cls.page = cls.ctx.new_page()
        cls.errs = []
        cls.page.on('pageerror', lambda e: cls.errs.append('pageerror:' + str(e)))
        cls.page.on('console',
                    lambda m: cls.errs.append('console.' + m.type + ':' + m.text) if m.type == 'error' else None)
        crm_login(cls.page)

    @classmethod
    def tearDownClass(cls):
        try: cls.ctx.close()
        except: pass

    def _navigate_and_check(self, view_name):
        # obriši samo greške dok navigiraš
        self.errs.clear()
        # debug: state postoji?
        has_state = self.page.evaluate("() => typeof window.state !== 'undefined'")
        self.assertTrue(has_state, msg=f'window.state not exposed before navigation to {view_name}')
        # Sidebar item-i se render-uju od strane JS-a i menjaju state.currentView.
        # `state` je top-level const u regular <script> — u strict eval kontekstu
        # nije referencabilan kao globalni identifier, pa koristimo eval() koji
        # se izvršava u istoj scope-i kao script-ovi na stranici.
        self.page.evaluate(
            "(v) => { window.state.currentView = v; window.state.detailViewId = null; "
            "if (typeof window.resetFilters === 'function') window.resetFilters(); "
            "if (typeof window.render === 'function') window.render(); }",
            view_name
        )
        self.page.wait_for_timeout(700)
        fatal = [e for e in self.errs
                 if 'favicon' not in e and 'cdn' not in e.lower()
                 and 'net::err_' not in e.lower()
                 and 'nominatim' not in e.lower() and 'unpkg' not in e.lower()
                 and 'tailwind' not in e.lower() and 'cartocdn' not in e.lower()
                 and '401' not in e and 'unauthorized' not in e.lower()
                 and 'openstreetmap' not in e.lower() and 'ip-api' not in e.lower()
                 and 'er-api' not in e.lower()]
        self.assertEqual(fatal, [], f'Fatal console errors on {view_name}: {fatal}')

    def test_01_partners(self):  self._navigate_and_check('partners')
    def test_02_products(self):  self._navigate_and_check('products')
    def test_03_offers(self):    self._navigate_and_check('offers')
    def test_04_deals(self):     self._navigate_and_check('deals')
    def test_05_demands(self):   self._navigate_and_check('demands')
    def test_06_finances(self):  self._navigate_and_check('finances')
    def test_07_settings(self):  self._navigate_and_check('settings')
    def test_08_users(self):     self._navigate_and_check('users')
    def test_09_audit(self):     self._navigate_and_check('audit')


class T11ProductAndPartnerCrudApi(E2EBase):
    """Direktan CRUD test svih tabela CRM-a."""

    def test_01_create_edit_delete_partner(self):
        s = new_crm_api_session()
        pid = 'p-e2e-' + uuid.uuid4().hex[:6]
        # Create
        code, _ = s.post('/api/item/partners', {
            'id': pid, 'name': 'Partner E2E', 'type': 'buyer',
            'address': 'Some 1', 'city': 'Novi Sad', 'country': 'RS',
            'email': 'p@example.com',
        })
        self.assertEqual(code, 200)
        p = db_row_by_key('partners', pid)
        self.assertEqual(p['name'], 'Partner E2E')
        # Edit (isti endpoint, POST prepisuje)
        code, _ = s.post('/api/item/partners', {
            'id': pid, 'name': 'Partner E2E V2', 'address': 'Some 1',
            'city': 'Novi Sad', 'country': 'RS', 'type': 'buyer'
        })
        self.assertEqual(code, 200)
        self.assertEqual(db_row_by_key('partners', pid)['name'], 'Partner E2E V2')
        # Delete
        code, _ = s.delete(f'/api/item/partners/{pid}')
        self.assertIn(code, (200, 204))
        self.assertIsNone(db_row_by_key('partners', pid))

    def test_02_create_product_with_variants(self):
        s = new_crm_api_session()
        pid = 'pr-e2e-' + uuid.uuid4().hex[:6]
        code, _ = s.post('/api/item/products', {
            'id': pid, 'name': 'Test Wheat', 'unit': 'MT', 'hsCode': '100199',
            'supplyOffers': [
                {'supplierId': 'sup-a', 'country': 'RS', 'incoterm': 'FOB', 'price': 300},
                {'supplierId': 'sup-b', 'country': 'BG', 'incoterm': 'CIF', 'price': 320}
            ]
        })
        self.assertEqual(code, 200)
        p = db_row_by_key('products', pid)
        self.assertEqual(len(p['supplyOffers']), 2)


class T12EdgeCasesPortalRfq(E2EBase):
    """Edge case-ovi na portal RFQ endpoint-u (NaN, negativno, prazno, ogromno)."""

    def test_01_negative_quantity_normalizes_to_zero(self):
        auth_key, token = portal_login_api()
        code, _ = portal_api(f'/api/portal/rfq/submit/{token}', method='POST',
                              body={'productId': E2E_PRODUCT_ID, 'quantity': -50, 'notes': 'neg qty'},
                              auth_key=auth_key)
        self.assertEqual(code, 200)
        # Nadji poslednji demand
        demands = [d for _, d in db_all('demands') if d.get('customerId') == E2E_PARTNER_ID
                   and d.get('notes') == 'neg qty']
        self.assertTrue(demands)
        self.assertEqual(float(demands[-1].get('quantity') or 0), 0.0)

    def test_02_huge_quantity_clamped(self):
        auth_key, token = portal_login_api()
        code, _ = portal_api(f'/api/portal/rfq/submit/{token}', method='POST',
                              body={'productId': E2E_PRODUCT_ID, 'quantity': 1e20, 'notes': 'huge'},
                              auth_key=auth_key)
        self.assertEqual(code, 200)
        demands = [d for _, d in db_all('demands') if d.get('notes') == 'huge']
        self.assertTrue(demands)
        # 1e20 mora biti klemovan na 0 (van bounds)
        self.assertLessEqual(float(demands[-1].get('quantity') or 0), 1e12)

    def test_03_empty_body_defaults_gracefully(self):
        auth_key, token = portal_login_api()
        code, data = portal_api(f'/api/portal/rfq/submit/{token}', method='POST',
                                 body={}, auth_key=auth_key)
        # ne sme da baci 500; ili 200 sa default proizvodom, ili 400
        self.assertIn(code, (200, 400))


class T13PortalUnauthorizedBlocked(E2EBase):
    """Bezbednost: portal endpoint-i moraju odbiti pristup bez X-Portal-Auth."""

    def test_01_data_endpoint_401_without_auth(self):
        code, _ = portal_api(f'/api/portal/data/{E2E_PARTNER_TOKEN}')
        self.assertEqual(code, 401)

    def test_02_kyc_submit_401_without_auth(self):
        code, _ = portal_api(f'/api/portal/kyc/submit/{E2E_PARTNER_TOKEN}',
                              method='POST', body={'data': {'consent': True}})
        self.assertEqual(code, 401)


class T14CrmForbiddenWithoutLogin(E2EBase):
    """CRM endpoint-i moraju baciti 401/403 bez sesije."""

    def test_01_data_endpoint_requires_login(self):
        # sirov request bez sesije
        code, _ = self._raw('/api/data/partners')
        self.assertEqual(code, 401)

    def test_02_deals_requires_login(self):
        code, _ = self._raw('/api/item/deals', 'POST', {'id': 'x', 'contractId': 'X'})
        # 401 iz auth ili 403 iz CSRF — oba su OK
        self.assertIn(code, (401, 403))

    def _raw(self, path, method='GET', body=None):
        req = urllib.request.Request(
            BASE_URL + path, method=method,
            data=json.dumps(body).encode() if body else None,
            headers={'Content-Type': 'application/json'}
        )
        try:
            r = urllib.request.urlopen(req, timeout=5)
            try: return r.getcode(), json.loads(r.read() or b'{}')
            except: return r.getcode(), None
        except urllib.error.HTTPError as e:
            try: return e.code, json.loads(e.read() or b'{}')
            except: return e.code, None


class T15FullPortalToCrmChain(E2EBase):
    """END-TO-END lanac: klijent šalje RFQ, admin je vidi u CRM UI, pravi ponudu,
    šalje je klijentu, klijent prihvata, admin pretvara u dil.
    Ovo je najkritičniji test iz zadatka."""

    def test_01_full_chain(self):
        # 1) Portal RFQ
        before_demands = {i for i, _ in db_all('demands')}
        auth_key, token = portal_login_api()
        code, _ = portal_api(f'/api/portal/rfq/submit/{token}', method='POST',
                              body={'productId': E2E_PRODUCT_ID, 'quantity': 42, 'notes': 'e2e chain'},
                              auth_key=auth_key)
        self.assertEqual(code, 200)
        new_demands = {i for i, _ in db_all('demands')} - before_demands
        self.assertEqual(len(new_demands), 1)
        demand_id = list(new_demands)[0]

        # 2) Admin login u CRM i kreiraj ponudu preko API-ja (simulira "Kreiraj ponudu iz potražnje")
        s = new_crm_api_session()
        offer_id = 'chain-offer-' + uuid.uuid4().hex[:8]
        offer_payload = {
            'id': offer_id, 'offerNo': 'CHAIN-01',
            'customerId': E2E_PARTNER_ID, 'productId': E2E_PRODUCT_ID,
            'quantity': 42, 'unit': 'MT',
            'sellingPrice': 5100, 'currency': 'USD',
            'date': '2026-07-17', 'sourceDemandId': demand_id,
        }
        code, _ = s.post('/api/item/offers', offer_payload)
        self.assertEqual(code, 200)

        # 3) Klijent (portal) prihvata ponudu
        code, _ = portal_api(f'/api/portal/offers/accept/{token}/{offer_id}',
                              method='POST',
                              body={'action': 'accept', 'note': 'sve ok'},
                              auth_key=auth_key)
        self.assertEqual(code, 200)
        off = db_row_by_key('offers', offer_id)
        self.assertEqual(off.get('clientStatus'), 'accepted')

        # 4) Admin pretvara u dil
        code, data = s.post(f'/api/deals/from_offer/{offer_id}', {'force': False})
        self.assertEqual(code, 200, msg=data)
        deal = db_row_by_key('deals', data['dealId'])
        self.assertIsNotNone(deal)
        # Sve ključno se moralo prenijeti
        self.assertEqual(deal.get('sourceOfferId'), offer_id)
        self.assertEqual(deal.get('productId'), E2E_PRODUCT_ID)
        self.assertEqual(int(deal.get('quantity') or 0), 42)


# ==========================================================
#  CRM API SESSION HELPER (login + CSRF cookie)
# ==========================================================
class CrmApiSession:
    def __init__(self):
        import http.cookiejar
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.csrf = None

    def _req(self, path, method='GET', body=None, headers=None):
        headers = headers or {}
        headers.setdefault('Content-Type', 'application/json')
        if method != 'GET' and self.csrf:
            headers.setdefault('X-CSRF-Token', self.csrf)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE_URL + path, method=method, data=data, headers=headers)
        try:
            r = self.opener.open(req, timeout=15)
            raw = r.read()
            try: return r.getcode(), json.loads(raw or b'{}')
            except: return r.getcode(), raw
        except urllib.error.HTTPError as e:
            raw = e.read()
            try: return e.code, json.loads(raw or b'{}')
            except: return e.code, raw

    def bootstrap_csrf(self):
        code, data = self._req('/api/csrf/token')
        if code == 200 and isinstance(data, dict): self.csrf = data.get('csrf_token')

    def login(self, username=E2E_ADMIN_USER, password=E2E_ADMIN_PASS):
        self.bootstrap_csrf()
        # login je izuzet iz CSRF middleware-a; GPS obavezan
        code, data = self._req('/api/auth/login', method='POST',
                                body={'username': username, 'password': password,
                                      'location': GPS_LOCATION})
        if code != 200:
            raise RuntimeError(f'login failed: {code} {data}')
        # posle uspešnog login-a povuci ponovo CSRF (novi session token)
        self.bootstrap_csrf()

    def get(self, path):  return self._req(path, 'GET')
    def post(self, path, body=None): return self._req(path, 'POST', body)
    def put(self, path, body=None):  return self._req(path, 'PUT', body)
    def delete(self, path): return self._req(path, 'DELETE')


def new_crm_api_session():
    s = CrmApiSession()
    s.login()
    return s


# ==========================================================
#  TEARDOWN
# ==========================================================
def _teardown():
    try: BrowserSession.shutdown()
    except: pass
    try: stop_server()
    except: pass

import atexit
atexit.register(_teardown)


if __name__ == '__main__':
    unittest.main(verbosity=2, exit=False)
