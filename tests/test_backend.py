"""End-to-end backend integration tests koji hit-uju sve API endpointe kroz
Werkzeug test client. Nema mreže/browser — čist Flask u istom procesu, pa je
brz i deterministički.

Pokreni:
    python -m tests.test_backend

Za svaki test se čisti auth stanje. Uvek počinjemo sa pravim login-om
(kroz /api/auth/login) da bi CSRF/session/cookie flow radio kao u produkciji.
"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from typing import Optional

# Ubaci projekt u path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Prepiši config.DATA_DIR pre importa app da bi svaki test suite dobio SVOJU praznu bazu.
# Ovim se garantuje reproduktibilnost.
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="crm_test_data_")
os.environ["DATA_DIR"] = _TEST_DATA_DIR
os.environ["SESSION_COOKIE_SECURE"] = "false"
os.environ["ADMIN_USERNAME"] = "testadmin"
os.environ["ADMIN_PASSWORD"] = "TestAdmin!12345"

import app as app_module  # noqa: E402


class BaseCase(unittest.TestCase):
    """Zajednička klasa — svaki test dobija novi klijent, čisti CSRF, i po
    potrebi može da se uloguje kao admin ili radnik."""

    @classmethod
    def setUpClass(cls):
        app_module.app.config['TESTING'] = True
        # Test klijent održava sesijski cookie kroz zahteve — kao pravi browser.
        cls.client = app_module.app.test_client()

    def _login_admin(self):
        """Klasičan login flow: povuci CSRF, POST /api/auth/login sa GPS
        koordinatama (obavezne kroz backend kontrolu lokacije)."""
        # GPS lokacija obavezna — ali login endpoint je izuzet iz CSRF middleware-a.
        res = self.client.post('/api/auth/login', json={
            'username': 'testadmin',
            'password': 'TestAdmin!12345',
            'location': '44.7866,20.4489',
            'device': 'test-suite/1.0'
        })
        return res

    def _get_csrf(self) -> str:
        res = self.client.get('/api/csrf/token')
        self.assertEqual(res.status_code, 200, msg=f"CSRF: {res.data}")
        return res.get_json()['csrf_token']

    def _post_with_csrf(self, url, json_body=None, headers=None):
        h = dict(headers or {})
        h['X-CSRF-Token'] = self._get_csrf()
        return self.client.post(url, json=json_body, headers=h)


class T01Auth(BaseCase):
    """Autentifikacija — login, logout, sesija, CSRF."""

    def test_01_csrf_token_available_pre_login(self):
        """CSRF token mora biti dostupan bez auth-a — inače login CSRF flow puca."""
        r = self.client.get('/api/csrf/token')
        self.assertEqual(r.status_code, 200)
        self.assertIn('csrf_token', r.get_json())

    def test_02_login_without_gps_blocked(self):
        """Login bez GPS koordinata mora biti odbijen sa LOCATION_REQUIRED."""
        r = self.client.post('/api/auth/login', json={
            'username': 'testadmin', 'password': 'TestAdmin!12345',
        })
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json().get('error'), 'LOCATION_REQUIRED')

    def test_03_login_with_bad_password_returns_401(self):
        r = self.client.post('/api/auth/login', json={
            'username': 'testadmin', 'password': 'WrongPw12345!',
            'location': '44.78,20.44'
        })
        self.assertEqual(r.status_code, 401)

    def test_04_login_success_and_me(self):
        r = self._login_admin()
        self.assertEqual(r.status_code, 200, msg=r.data)
        r2 = self.client.get('/api/auth/me')
        self.assertEqual(r2.status_code, 200)
        me = r2.get_json()
        self.assertEqual(me['user']['role'], 'admin')

    def test_05_csrf_missing_blocks_mutation(self):
        """Bez CSRF header-a, POST na /api/item/partners mora vratiti 403."""
        self._login_admin()
        r = self.client.post('/api/item/partners', json={'id': 'x'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json().get('error'), 'CSRF_TOKEN_INVALID')

    def test_06_logout(self):
        self._login_admin()
        # Logout je mutation → mora imati CSRF token (kao svaka druga POST)
        r = self._post_with_csrf('/api/auth/logout')
        self.assertEqual(r.status_code, 200)
        r2 = self.client.get('/api/auth/me')
        self.assertEqual(r2.status_code, 401)


class T02DataCrud(BaseCase):
    """Osnovni CRUD nad glavnim modulima."""

    def setUp(self):
        self._login_admin()

    def test_01_partners_create_read_delete(self):
        pid = str(uuid.uuid4())
        payload = {
            'id': pid, 'companyName': 'Test Partner LLC',
            'taxId': 'RS111', 'contact': {'email': 'x@y.com'}
        }
        r = self._post_with_csrf('/api/item/partners', payload)
        self.assertEqual(r.status_code, 200, msg=r.data)
        # Read
        r2 = self.client.get('/api/data/partners')
        self.assertEqual(r2.status_code, 200)
        items = r2.get_json().get('value', [])
        self.assertTrue(any(p.get('id') == pid for p in items),
                        f"Partner nije nađen posle upisa. Items: {items}")
        # Delete
        r3 = self.client.delete(f'/api/item/partners/{pid}',
                                headers={'X-CSRF-Token': self._get_csrf()})
        self.assertEqual(r3.status_code, 200, msg=r3.data)

    def test_02_products_create_read_delete(self):
        pid = str(uuid.uuid4())
        payload = {'id': pid, 'name': 'Sugar ICUMSA 45', 'category': 'Sugar', 'hsCode': '170114'}
        r = self._post_with_csrf('/api/item/products', payload)
        self.assertEqual(r.status_code, 200, msg=r.data)
        r2 = self.client.get('/api/data/products')
        self.assertTrue(any(p.get('id') == pid for p in r2.get_json().get('value', [])))
        r3 = self.client.delete(f'/api/item/products/{pid}',
                                headers={'X-CSRF-Token': self._get_csrf()})
        self.assertEqual(r3.status_code, 200)

    def test_03_invalid_module_returns_error(self):
        """Nepoznat modul ne sme puknuti — mora vratiti graceful grešku."""
        r = self.client.get('/api/data/random_junk')
        # Frontend očekuje ili 200 sa null value ili 4xx — samo ne 500
        self.assertNotEqual(r.status_code, 500,
                            msg=f"Nepoznat modul izazvao 500 crash: {r.data}")

    def test_04_delete_nonexistent_id_graceful(self):
        r = self.client.delete('/api/item/partners/does_not_exist',
                               headers={'X-CSRF-Token': self._get_csrf()})
        self.assertNotEqual(r.status_code, 500)


class T03OfferHash(BaseCase):
    def setUp(self):
        self._login_admin()

    def test_01_hash_verify_offer_not_found(self):
        r = self._post_with_csrf('/api/offers/verify_hash',
                                 {'offer_no': 'NONE_001', 'hash': 'VER-000000000000-00000000'})
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertFalse(j['valid'])
        self.assertEqual(j['reason'], 'OFFER_NOT_FOUND')

    def test_02_hash_verify_missing_fields(self):
        r = self._post_with_csrf('/api/offers/verify_hash', {'offer_no': ''})
        self.assertEqual(r.status_code, 400)

    def test_03_hash_verify_valid_match(self):
        """Ubaci ponudu direktno u DB, pa proveri da hash-check radi za validan."""
        from pdf_generator import _make_verification_hash
        from config import DB_FILE
        oid = str(uuid.uuid4()); ono = f"T-{oid[:6]}"
        offer = {'id': oid, 'offerNo': ono, 'currency': 'USD',
                 'items': [{'productId': None, 'quantity': 5, 'price': 100}],
                 'ownerId': 'testadmin'}
        with sqlite3.connect(DB_FILE, timeout=10.0) as conn:
            conn.execute("INSERT INTO offers (id, data) VALUES (?, ?)",
                         (oid, json.dumps(offer)))
            conn.commit()
        expected = _make_verification_hash(oid, ono)
        r = self._post_with_csrf('/api/offers/verify_hash',
                                 {'offer_no': ono, 'hash': expected})
        j = r.get_json()
        self.assertTrue(j['valid'], msg=f"Expected valid, got: {j}")
        # Provera i za pogrešan hash
        r2 = self._post_with_csrf('/api/offers/verify_hash',
                                  {'offer_no': ono, 'hash': 'VER-DEAD-BEEF'})
        j2 = r2.get_json()
        self.assertFalse(j2['valid'])
        self.assertEqual(j2['reason'], 'HASH_MISMATCH')


class T04Pdf(BaseCase):
    def setUp(self):
        self._login_admin()

    def test_01_preview_pdf_generates_bytes(self):
        offer = {'offerNo': 'PV-001', 'currency': 'EUR',
                 'items': [{'productId': None, 'productName': 'Item A',
                            'quantity': 1, 'price': 10, 'unit': 'MT'}]}
        r = self._post_with_csrf('/api/offers/preview_pdf', offer)
        self.assertEqual(r.status_code, 200, msg=r.data[:200])
        self.assertEqual(r.mimetype, 'application/pdf')
        self.assertTrue(r.data.startswith(b'%PDF'), "Nije validan PDF signature")

    def test_02_preview_pdf_empty_payload_400(self):
        r = self._post_with_csrf('/api/offers/preview_pdf', {})
        self.assertEqual(r.status_code, 400)


class T05Portal(BaseCase):
    """Portal endpointi rade bez CSRF (imaju svoj X-Portal-Auth)."""

    def test_01_login_page_renders(self):
        r = self.client.get('/portal/login')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'otp-boxes', r.data, "OTP 6-boxes UI nije renderovan")

    def test_02_login_missing_email(self):
        r = self.client.post('/api/portal/auth/login', json={})
        # Email nije valid — treba 400
        self.assertEqual(r.status_code, 400)

    def test_03_login_unregistered_email_generic_response(self):
        """Neregistrovan email vraća isti generic uspeh (anti-enumeration)."""
        r = self.client.post('/api/portal/auth/login',
                             json={'email': 'no-such@user.com'})
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(j['status'], 'success')
        # Mora imati session_id (fake) i generic poruku
        self.assertIn('session_id', j)

    def test_04_verify_otp_without_location_blocked(self):
        r = self.client.post('/api/portal/auth/verify_otp/anytoken',
                             json={'otp': '123456'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json().get('error'), 'LOCATION_REQUIRED')

    def test_05_login_verify_missing_location(self):
        r = self.client.post('/api/portal/auth/login/verify',
                             json={'session_id': 'x', 'otp': '123456'})
        self.assertEqual(r.status_code, 403)

    def test_06_admin_portal_activity_requires_admin(self):
        # Bez login-a → 401
        r = self.client.get('/api/portal/admin/activity')
        self.assertEqual(r.status_code, 401)
        # Sa admin login-om → 200
        self._login_admin()
        r2 = self.client.get('/api/portal/admin/activity')
        self.assertEqual(r2.status_code, 200)
        j = r2.get_json()
        self.assertIn('rows', j)
        self.assertIn('meta', j)

    def test_07_admin_pending_counts_shape(self):
        self._login_admin()
        r = self.client.get('/api/portal/admin/pending_counts')
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        # Ovo je bio bug pre — vraćalo je listu unutar total. Sad mora biti int.
        for key in ['kyc', 'products', 'profile_requests', 'rfqs',
                    'offer_responses', 'total']:
            self.assertIn(key, j)
            self.assertIsInstance(j[key], int,
                                  f"'{key}' nije int nego {type(j[key])}: {j[key]!r}")
        # offer_responses_detail mora biti lista
        self.assertIsInstance(j.get('offer_responses_detail'), list)


class T06System(BaseCase):
    def test_01_system_health_admin_only(self):
        r = self.client.get('/api/system/health')
        self.assertEqual(r.status_code, 401)
        self._login_admin()
        r2 = self.client.get('/api/system/health')
        self.assertEqual(r2.status_code, 200)
        j = r2.get_json()
        for key in ['databases', 'storage', 'backups', 'firewall']:
            self.assertIn(key, j)

    def test_02_robots_txt_disallow_portal(self):
        r = self.client.get('/robots.txt')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Disallow: /portal', r.data)

    def test_03_security_headers_present(self):
        r = self.client.get('/')
        for h in ['Content-Security-Policy', 'X-Frame-Options',
                  'Referrer-Policy', 'Permissions-Policy',
                  'X-Content-Type-Options']:
            self.assertIn(h, r.headers, f"Missing security header: {h}")

    def test_04_portal_noindex_header(self):
        r = self.client.get('/portal/login')
        self.assertIn('X-Robots-Tag', r.headers)
        self.assertIn('noindex', r.headers['X-Robots-Tag'])


class T07Firewall(BaseCase):
    def setUp(self):
        self._login_admin()

    def test_01_firewall_settings_get(self):
        r = self.client.get('/api/firewall/settings')
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        for key in ['active', 'defaults', 'descriptions']:
            self.assertIn(key, j)

    def test_02_firewall_blacklist_invalid_ip_rejected(self):
        r = self._post_with_csrf('/api/firewall/blacklist/add', {'ip': 'not-an-ip'})
        self.assertEqual(r.status_code, 400)


class T09OfferToDeal(BaseCase):
    """Kritičan test: konverzija prihvaćene ponude u dil MORA prenositi sva
    polja verno. Ovo je bio bug — nedostajali su bankDetails, notes, taxClause,
    weights, discount itd."""
    def setUp(self):
        self._login_admin()

    def test_01_full_data_transfer(self):
        """Kreiraj ponudu sa mnogo popunjenih polja, obeleži clientAccepted,
        pa proveri da se DEAL kreira sa svim tim poljima."""
        from config import DB_FILE
        # Ubaci partnera
        pid = str(uuid.uuid4())
        partner_data = {'id': pid, 'companyName': 'Test Buyer LLC',
                        'contact': {'email': 'buyer@x.com', 'phone': '+123'},
                        'address': {'street': 'Main 1', 'city': 'City', 'country': 'RS'},
                        'taxId': 'RS999', 'regNumber': 'MB111'}
        with sqlite3.connect(DB_FILE, timeout=10.0) as conn:
            conn.execute("INSERT INTO partners (id, data) VALUES (?, ?)",
                         (pid, json.dumps(partner_data)))
            conn.commit()

        # Ubaci ponudu sa svim relevantnim poljima
        oid = str(uuid.uuid4())
        offer = {
            'id': oid, 'offerNo': 'FULL-001', 'date': '2026-01-15T00:00:00Z',
            'customerId': pid, 'productName': 'Sugar ICUMSA 45',
            'quantity': 100, 'unit': 'MT', 'currency': 'EUR',
            'sellingPrice': 500, 'incoterm': 'CIF',
            'pol': 'Santos', 'pod': 'Rijeka', 'vessel': 'MV Star',
            'containerNo': '20FCL x5', 'packaging': '50kg PP bags',
            'leadTime': '30 days', 'paymentTerms': 'L/C 90 days',
            'discount': 500, 'customVatRate': 5, 'advance': 20000,
            'taxClause': 'EU export, VAT 0%',
            'bankDetails': 'Bank: ABC\nIBAN: DE1234\nSWIFT: DEUTDEFF',
            'notes': 'Delivery on time please',
            'weights': {'net': 100, 'gross': 105, 'cbm': 200, 'unit': 'MT'},
            'clientStatus': 'accepted',
            'clientAcceptedAt': '2026-01-16T00:00:00Z',
            'clientNote': 'Accepted, please proceed',
            'items': [{'productId': None, 'quantity': 100, 'unit': 'MT', 'price': 500}]
        }
        with sqlite3.connect(DB_FILE, timeout=10.0) as conn:
            conn.execute("INSERT INTO offers (id, data) VALUES (?, ?)",
                         (oid, json.dumps(offer)))
            conn.commit()

        # Sada konvertuj u dil
        r = self._post_with_csrf(f'/api/deals/from_offer/{oid}', {})
        self.assertEqual(r.status_code, 200, msg=r.data)
        result = r.get_json()
        deal = result['deal']

        # Provera da sva ključna polja stigla u dil
        self.assertEqual(deal['buyerName'], 'Test Buyer LLC')
        self.assertEqual(deal['buyerContactEmail'], 'buyer@x.com')
        self.assertEqual(deal['buyerAddress'], 'Main 1, City, RS')
        self.assertEqual(deal['buyerTaxId'], 'RS999')

        # Komercijalna polja
        self.assertEqual(deal['sellingPrice'], 500)
        self.assertEqual(deal['sellingCurrency'], 'EUR')
        self.assertEqual(deal['quantity'], 100)
        self.assertEqual(deal['incoterm'], 'CIF')

        # Logistika
        self.assertEqual(deal['logistics']['pol'], 'Santos')
        self.assertEqual(deal['logistics']['pod'], 'Rijeka')
        self.assertEqual(deal['logistics']['vessel'], 'MV Star')
        self.assertEqual(deal['logistics']['containerNo'], '20FCL x5')
        self.assertEqual(deal['logistics']['packaging'], '50kg PP bags')
        self.assertEqual(deal['logistics']['leadTime'], '30 days')

        # Finansije
        self.assertEqual(deal['paymentTerms'], 'L/C 90 days')
        self.assertEqual(deal['discount'], 500)
        self.assertEqual(deal['customVatRate'], 5)
        self.assertEqual(deal['advance'], 20000)
        self.assertEqual(deal['taxClause'], 'EU export, VAT 0%')

        # Bank details — KRITIČNO
        self.assertIn('IBAN: DE1234', deal['bankDetails'])

        # Notes i tražioci
        self.assertEqual(deal['notes'], 'Delivery on time please')
        self.assertEqual(deal['weights']['net'], 100)
        self.assertEqual(deal['weights']['cbm'], 200)

        # Reference nazad na ponudu (traceability)
        self.assertEqual(deal['sourceOfferId'], oid)
        self.assertEqual(deal['sourceOfferNo'], 'FULL-001')
        self.assertEqual(deal['clientAcceptanceNote'], 'Accepted, please proceed')


class T10PortalAcceptDecline(BaseCase):
    """Portal offer accept/decline test — direktno kroz backend."""

    def test_01_decline_without_reason_rejected(self):
        # Prvo napravi partnera sa portal token-om + ponudu za tog partnera
        import secrets
        from config import DB_FILE
        pid = str(uuid.uuid4()); token = secrets.token_urlsafe(32)
        partner_data = {'id': pid, 'companyName': 'Portal Test',
                        'portalToken': token, 'isPortalActive': True,
                        'contact': {'email': 'p@x.com'}}
        oid = str(uuid.uuid4())
        offer = {'id': oid, 'offerNo': 'DEC-001', 'customerId': pid,
                 'currency': 'USD', 'sellingPrice': 100}
        with sqlite3.connect(DB_FILE, timeout=10.0) as conn:
            conn.execute("INSERT INTO partners (id, data) VALUES (?, ?)",
                         (pid, json.dumps(partner_data)))
            conn.execute("INSERT INTO offers (id, data) VALUES (?, ?)",
                         (oid, json.dumps(offer)))
            conn.commit()

        # Napravi lažnu portal auth sesiju (in-memory dict)
        from routes.portal import portal_auth_sessions, PORTAL_SESSION_TTL
        import time as _t
        auth_key = 'test-auth-key-' + secrets.token_hex(8)
        portal_auth_sessions[token] = {
            'key': auth_key, 'expires': _t.time() + PORTAL_SESSION_TTL,
            'last_active': _t.time(), 'partner_id': pid, 'bound_ip': '127.0.0.1'
        }

        # POST decline bez razloga → mora vratiti 400 DECLINE_REASON_REQUIRED
        r = self.client.post(f'/api/portal/offers/accept/{token}/{oid}',
                             json={'action': 'decline', 'note': ''},
                             headers={'X-Portal-Auth': auth_key})
        self.assertEqual(r.status_code, 400)
        j = r.get_json()
        self.assertEqual(j.get('error'), 'DECLINE_REASON_REQUIRED')


class T08Users(BaseCase):
    def setUp(self):
        self._login_admin()

    def test_01_users_list(self):
        r = self.client.get('/api/users')
        self.assertEqual(r.status_code, 200)
        users = r.get_json()
        self.assertTrue(any(u.get('username') == 'testadmin' for u in users))

    def test_02_change_password_weak_rejected(self):
        r = self._post_with_csrf('/api/auth/change_password',
                                 {'new_password': 'weak'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json().get('error'), 'WEAK_PASSWORD')


class T13PdfPaymentBank(BaseCase):
    """PDF generator: paymentBankIdx bira konkretnu banku iz company.bankAccounts."""

    def test_01_specific_bank_selected(self):
        """Testira _bank_details_string helper direktno — PDF stream je kompresovan
        pa ne možemo raw bytes pretraživati."""
        from pdf_generator import _bank_details_string, build_offer_pdf
        company = {
            'name': 'Aspidus', 'address': 'Street 1',
            'bankAccounts': [
                {'bankName': 'Bank Zero', 'accountNumber': 'IBAN0', 'swiftCode': 'ZZZ', 'currency': 'USD'},
                {'bankName': 'Bank One EUR', 'accountNumber': 'DE12EUR', 'swiftCode': 'EUROBIC', 'currency': 'EUR'},
                {'bankName': 'Bank Two AED', 'accountNumber': 'AE99AED', 'swiftCode': 'AEDBIC', 'currency': 'AED'}
            ]
        }
        # Default (bez idx) → prva banka
        s0 = _bank_details_string(company)
        self.assertIn('Bank Zero', s0)
        self.assertIn('IBAN0', s0)
        self.assertNotIn('DE12EUR', s0)

        # idx=1 → EUR banka
        s1 = _bank_details_string(company, 1)
        self.assertIn('DE12EUR', s1)
        self.assertIn('EUR', s1)
        self.assertNotIn('IBAN0', s1)

        # idx=2 → AED banka
        s2 = _bank_details_string(company, 2)
        self.assertIn('AE99AED', s2)
        self.assertIn('AEDBIC', s2)

        # Ekstremni idx (out-of-range) → fallback na prvi
        s99 = _bank_details_string(company, 99)
        self.assertIn('IBAN0', s99)

        # Legacy company bez bankAccounts (samo flat polja)
        legacy = {'bankName': 'LegacyBank', 'accountNum': 'LGCY999', 'swift': 'LGCYBIC'}
        sl = _bank_details_string(legacy)
        self.assertIn('LegacyBank', sl)
        self.assertIn('LGCY999', sl)

        # Sanity: PDF generacija ne pada
        offer = {'offerNo': 'B-001', 'currency': 'USD',
                 'items': [{'productId': None, 'quantity': 1, 'price': 100, 'unit': 'kg'}],
                 'paymentBankIdx': 1}
        pdf = build_offer_pdf(offer, company=company, settings={})
        self.assertTrue(pdf.startswith(b'%PDF'))


class T11DocumentManager(BaseCase):
    """Admin document manager — list, delete, ZIP export."""

    def setUp(self):
        self._login_admin()

    def test_01_list_requires_admin(self):
        # Bez admin login-a
        self.client.post('/api/auth/logout', headers={'X-CSRF-Token': self._get_csrf()})
        r = self.client.get('/api/admin/documents/list')
        # Nakon logout je unauthorized
        self.assertIn(r.status_code, (401, 403))

    def test_02_list_returns_shape(self):
        r = self.client.get('/api/admin/documents/list')
        self.assertEqual(r.status_code, 200, msg=r.data[:300])
        j = r.get_json()
        self.assertIn('files', j)
        self.assertIn('stats', j)
        self.assertIsInstance(j['files'], list)
        for key in ['total_count', 'total_bytes', 'total_mb', 'by_partner']:
            self.assertIn(key, j['stats'])

    def test_03_delete_empty_payload_graceful(self):
        r = self._post_with_csrf('/api/admin/documents/delete', {'files': []})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['deleted_count'], 0)

    def test_04_delete_path_traversal_blocked(self):
        """Napadač pokušava da izbriše fajl van upload foldera preko '..' u nazivu."""
        r = self._post_with_csrf('/api/admin/documents/delete',
                                 {'files': [{'folder': 'uploads', 'name': '../../etc/passwd'}]})
        self.assertEqual(r.status_code, 200)
        # secure_filename striplje '..' pa ostane 'etc_passwd' — koje ne postoji, pa deleted=0
        self.assertEqual(r.get_json()['deleted_count'], 0)

    def test_05_delete_actual_file(self):
        """Kreiraj privremeni fajl u uploads/, obriši ga preko API."""
        from config import UPLOAD_FOLDER
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        test_name = 'test_delete_me.pdf'
        p = os.path.join(UPLOAD_FOLDER, test_name)
        with open(p, 'wb') as f:
            f.write(b'%PDF-1.4\n%test\n')
        r = self._post_with_csrf('/api/admin/documents/delete',
                                 {'files': [{'folder': 'uploads', 'name': test_name}]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['deleted_count'], 1)
        self.assertFalse(os.path.exists(p))

    def test_06_bulk_zip_returns_zip(self):
        """ZIP export bez filtera vraća validan ZIP fajl."""
        from config import UPLOAD_FOLDER
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        with open(os.path.join(UPLOAD_FOLDER, 'zip_test.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4\n%zip test\n')
        r = self.client.get('/api/admin/documents/bulk_zip')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, 'application/zip')
        # ZIP local header magic
        self.assertTrue(r.data.startswith(b'PK'), "Nije validan ZIP signature")


class T12BankSync(BaseCase):
    """Sync između settings.company.bankAccounts i cashflow accounts + offer."""

    def setUp(self):
        self._login_admin()

    def test_01_company_bank_accounts_stored_and_retrieved(self):
        """Snimi company sa 2 bankAccounts, potvrdi da /api/data/company vrati istu strukturu."""
        payload = {
            'name': 'TestCo', 'address': 'Street 1', 'taxId': '111',
            'bankAccounts': [
                {'bankName': 'Bank A', 'accountNumber': 'IBAN1', 'swiftCode': 'BANKAAAAA', 'currency': 'EUR'},
                {'bankName': 'Bank B', 'accountNumber': 'IBAN2', 'swiftCode': 'BANKBBBBB', 'currency': 'USD'}
            ]
        }
        # save
        r = self._post_with_csrf('/api/item/company', payload)
        # Company nije u "tables" liste — save ide kroz settings/company grana
        # Ako 400 zbog missing id → fallback na /api/data/company:
        if r.status_code >= 400:
            r = self._post_with_csrf('/api/data/company', {'value': payload})
            self.assertEqual(r.status_code, 200, msg=r.data[:300])
        # read
        r2 = self.client.get('/api/data/company')
        self.assertEqual(r2.status_code, 200)
        v = r2.get_json()['value']
        self.assertIsInstance(v, dict)
        self.assertEqual(len(v.get('bankAccounts', [])), 2)
        self.assertEqual(v['bankAccounts'][0]['bankName'], 'Bank A')
        self.assertEqual(v['bankAccounts'][1]['currency'], 'USD')


class T13bPdfMetadata(BaseCase):
    """PDF metadata (title, author, subject, keywords, creator, producer) mora
    biti native ubačen — Windows Explorer i macOS Preview ih pokazuju u
    Properties dialog-u."""

    def test_01_metadata_embedded_in_pdf_bytes(self):
        from pdf_generator import build_offer_pdf
        company = {'name': 'Aspidus Global Traders', 'address': 'HQ St 1',
                   'taxId': '111222', 'brandColor': '#1a56db'}
        offer = {'id': 'offer-meta-01', 'offerNo': 'META-0001', 'customerId': None,
                 'items': [{'quantity': 5, 'price': 100, 'unit': 'MT'}],
                 'currency': 'USD', 'date': '2026-07-17'}
        pdf_bytes = build_offer_pdf(offer, company=company, settings={'lang': 'en'})
        # PDF metadata polja se pojavljuju u info dict-u.
        # ReportLab ih upisuje kao PDF strings u catalogu. Nisu kompresovani —
        # samo hex-encoded ili literalni. Dovoljno je da pretražimo bytes.
        head = pdf_bytes[:8192]
        self.assertIn(b'%PDF', head)
        # Neki reader-i info koriste iz PDF trailer info dict; potvrđujemo
        # da su title/author/subject/creator/producer bytes prisutni.
        # ReportLab može enkodovati kao FEFF-BE UTF-16, pa proveravamo obe forme.
        tail = pdf_bytes[-4096:]
        # Sledeći test kroz pikepdf/PyPDF2 nije dostupan (nema u requirements-u),
        # pa umesto toga verifikujemo prisustvo tag-ova
        combined = pdf_bytes
        self.assertTrue(b'/Title' in combined, msg='PDF missing /Title metadata tag')
        self.assertTrue(b'/Author' in combined, msg='PDF missing /Author metadata tag')
        self.assertTrue(b'/Subject' in combined, msg='PDF missing /Subject metadata tag')
        self.assertTrue(b'/Keywords' in combined, msg='PDF missing /Keywords metadata tag')
        self.assertTrue(b'/Creator' in combined, msg='PDF missing /Creator metadata tag')
        self.assertTrue(b'/Producer' in combined, msg='PDF missing /Producer metadata tag')
        # Producer se postavlja preko canvas._doc.info.producer — verifikacija
        # da nije default "ReportLab" već naš brend
        self.assertIn(b'Aspidus CRM PDF Engine', combined,
                      msg='Producer field should be branded Aspidus CRM engine')

    def test_02_no_crash_on_string_address(self):
        """Regresija: PDF ne sme da padne kad je partner.address STRING (legacy)."""
        from pdf_generator import build_offer_pdf, _normalize_address, _normalize_contact
        # Direktan test helper-a
        p = {'name': 'Legacy Co', 'address': 'Old Street 42', 'city': 'Belgrade', 'country': 'RS'}
        s, geo = _normalize_address(p)
        self.assertEqual(s, 'Old Street 42')
        self.assertEqual(geo, 'Belgrade RS')
        # Sa dict-om
        p2 = {'name': 'New Co', 'address': {'street': 'New Str 1', 'city': 'NS', 'country': 'RS'}}
        s2, geo2 = _normalize_address(p2)
        self.assertEqual(s2, 'New Str 1')
        self.assertEqual(geo2, 'NS RS')
        # Contact varijante
        email, phone, person = _normalize_contact({'contact': 'not a dict', 'email': 'a@b.c', 'phone': '123'})
        self.assertEqual(email, 'a@b.c'); self.assertEqual(phone, '123')


class T14LogisticsPlanner(BaseCase):
    """Multimodalni planer — /api/logistics/{ports,airports,plan,disruptions}."""

    def setUp(self):
        self._login_admin()

    def test_01_ports_list_returns_data(self):
        r = self.client.get('/api/logistics/ports?limit=5')
        self.assertEqual(r.status_code, 200)
        js = r.get_json()
        self.assertGreater(js['total'], 100)
        self.assertLessEqual(len(js['items']), 5)
        for p in js['items']:
            self.assertIn('lat', p); self.assertIn('lon', p); self.assertIn('unlocode', p)

    def test_02_airports_list_search_by_iata(self):
        r = self.client.get('/api/logistics/airports?q=JFK&limit=5')
        self.assertEqual(r.status_code, 200)
        js = r.get_json()
        # JFK mora biti pronađen
        self.assertTrue(any(a.get('iata') == 'JFK' for a in js['items']))

    def test_03_disruptions_returns_seed(self):
        r = self.client.get('/api/logistics/disruptions')
        self.assertEqual(r.status_code, 200)
        js = r.get_json()
        ids = {d['id'] for d in js['disruptions']}
        self.assertIn('red-sea-2024', ids)

    def test_04_plan_road_only_short_distance(self):
        # Beograd → Budimpešta (~330km road)
        r = self._post_with_csrf('/api/logistics/plan', {
            'origin': {'lat': 44.7866, 'lon': 20.4489, 'label': 'Belgrade'},
            'destination': {'lat': 47.4979, 'lon': 19.0402, 'label': 'Budapest'},
            'cargo_tons': 10.0
        })
        self.assertEqual(r.status_code, 200, msg=r.data[:400])
        js = r.get_json()
        modes = {p['mode'] for p in js['plans']}
        self.assertIn('road', modes)
        road = next(p for p in js['plans'] if p['mode'] == 'road')
        self.assertLess(road['total_days'], 3)
        self.assertGreater(road['total_distance_km'], 200)

    def test_05_plan_intercontinental_uses_sea(self):
        # Rotterdam port → New York port
        r = self._post_with_csrf('/api/logistics/plan', {
            'origin': {'lat': 51.9225, 'lon': 4.4792, 'label': 'Rotterdam'},
            'destination': {'lat': 40.7128, 'lon': -74.0060, 'label': 'New York'},
            'cargo_tons': 100.0
        })
        self.assertEqual(r.status_code, 200)
        js = r.get_json()
        modes = {p['mode'] for p in js['plans']}
        self.assertIn('sea', modes)
        self.assertIn('air', modes)
        sea = next(p for p in js['plans'] if p['mode'] == 'sea')
        # Prekooceanski minimum ~5000km
        self.assertGreater(sea['total_distance_km'], 5000)
        # Sea mora imati 3 etape (kopno-more-kopno)
        self.assertEqual(len(sea['legs']), 3)

    def test_06_plan_red_sea_disruption_reroutes(self):
        # Rotterdam → Shanghai — Suez rerouting oko Rta Dobre Nade
        r = self._post_with_csrf('/api/logistics/plan', {
            'origin': {'lat': 51.9225, 'lon': 4.4792, 'label': 'Rotterdam'},
            'destination': {'lat': 31.2304, 'lon': 121.4737, 'label': 'Shanghai'},
            'cargo_tons': 100.0
        })
        self.assertEqual(r.status_code, 200)
        js = r.get_json()
        sea = next(p for p in js['plans'] if p['mode'] == 'sea')
        wps = sea['legs'][1].get('via_waypoints', [])
        # Kada je Crveno more aktivno kao high-severity, treba da izbegne Suez
        self.assertIn('cape_good_hope', wps)
        self.assertNotIn('suez', wps)

    def test_07a_search_returns_ports_and_airports(self):
        r = self.client.get('/api/logistics/search?q=rotterdam&limit=10')
        self.assertEqual(r.status_code, 200)
        js = r.get_json()
        hits = js.get('hits', [])
        self.assertTrue(any(h['type'] == 'port' and 'Rotterdam' in h['name'] for h in hits))
        # Score sortiranje: port sa exact code match ide gore
        self.assertGreater(hits[0]['score'], 0)

    def test_07b_search_iata_code_top_hit(self):
        r = self.client.get('/api/logistics/search?q=JFK&limit=5')
        self.assertEqual(r.status_code, 200)
        hits = r.get_json()['hits']
        self.assertTrue(hits, 'no results for JFK')
        # JFK je exact IATA code — prvi hit MORA biti aerodrom
        self.assertEqual(hits[0]['type'], 'airport')
        self.assertEqual(hits[0]['code'], 'JFK')

    def test_07c_search_short_query_returns_empty(self):
        r = self.client.get('/api/logistics/search?q=&limit=5')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['hits'], [])

    def test_08_smart_perishable_light_recommends_air(self):
        """Kritičan business scenario: 300kg perishable + 3-day deadline → AIR."""
        r = self._post_with_csrf('/api/logistics/plan', {
            'origin': {'lat': 51.9, 'lon': 4.5, 'label': 'Rotterdam'},
            'destination': {'lat': 40.7, 'lon': -74.0, 'label': 'NYC'},
            'cargo_tons': 0.3,
            'perishable': True,
            'deadline_days': 3,
        })
        self.assertEqual(r.status_code, 200)
        js = r.get_json()
        self.assertEqual(js['recommended_mode'], 'air',
                         msg=f'expected air for 300kg perishable + 3d deadline, got {js["recommended_mode"]}')
        air_plan = next(p for p in js['plans'] if p['mode'] == 'air')
        self.assertGreaterEqual(air_plan['fitness_score'], 70)
        self.assertTrue(any('perishable' in r.lower() for r in air_plan['fitness_reasons']))

    def test_09_smart_heavy_bulk_recommends_sea(self):
        """500t suvog rasutog tereta bez roka → SEA."""
        r = self._post_with_csrf('/api/logistics/plan', {
            'origin': {'lat': 51.9, 'lon': 4.5, 'label': 'Rotterdam'},
            'destination': {'lat': 40.7, 'lon': -74.0, 'label': 'NYC'},
            'cargo_tons': 500,
            'container_type': 'bulk_dry',
        })
        self.assertEqual(r.status_code, 200)
        js = r.get_json()
        self.assertEqual(js['recommended_mode'], 'sea')
        sea_plan = next(p for p in js['plans'] if p['mode'] == 'sea')
        self.assertGreaterEqual(sea_plan['fitness_score'], 60)

    def test_10_port_dwell_uses_per_port_data(self):
        """Za identičan cargo Singapore mora imati kraći dwell od Lagos-a."""
        # Rotterdam → Singapore (top_tier)
        r1 = self._post_with_csrf('/api/logistics/plan', {
            'origin': {'lat': 51.9, 'lon': 4.5, 'label': 'Rotterdam'},
            'destination': {'lat': 1.264, 'lon': 103.842, 'label': 'Singapore'},
            'cargo_tons': 20, 'container_type': 'teu',
        })
        # Rotterdam → Lagos (congested)
        r2 = self._post_with_csrf('/api/logistics/plan', {
            'origin': {'lat': 51.9, 'lon': 4.5, 'label': 'Rotterdam'},
            'destination': {'lat': 6.45, 'lon': 3.4, 'label': 'Lagos'},
            'cargo_tons': 20, 'container_type': 'teu',
        })
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        sea1 = next(p for p in r1.get_json()['plans'] if p['mode'] == 'sea')
        sea2 = next(p for p in r2.get_json()['plans'] if p['mode'] == 'sea')
        # Singapore ima kraći dwell u destination portu nego Lagos (congested)
        sg_dwell = sea1['legs'][1]['destination_port']['dwell_hours']
        lg_dwell = sea2['legs'][1]['destination_port']['dwell_hours']
        self.assertLess(sg_dwell, lg_dwell,
                        msg=f'Singapore dwell ({sg_dwell}h) should be < Lagos ({lg_dwell}h)')
        # Tier metadata je prisutan
        self.assertEqual(sea1['legs'][1]['destination_port']['tier'], 'top_tier')
        self.assertEqual(sea2['legs'][1]['destination_port']['tier'], 'congested')

    def test_11_cost_estimate_present(self):
        r = self._post_with_csrf('/api/logistics/plan', {
            'origin': {'lat': 51.9, 'lon': 4.5, 'label': 'Rotterdam'},
            'destination': {'lat': 40.7, 'lon': -74.0, 'label': 'NYC'},
            'cargo_tons': 20, 'container_type': 'feu',
        })
        js = r.get_json()
        for p in js['plans']:
            self.assertIn('estimated_cost_usd', p)
            self.assertGreater(p['estimated_cost_usd'], 0)

    def test_07d_search_filter_by_country(self):
        r = self.client.get('/api/logistics/search?q=port&country=NL&limit=15')
        self.assertEqual(r.status_code, 200)
        hits = r.get_json()['hits']
        for h in hits:
            self.assertEqual(h['country'], 'Netherlands' if h['type'] == 'port' else 'NL')

    def test_07_plan_missing_coords_returns_400(self):
        r = self._post_with_csrf('/api/logistics/plan', {
            'origin': {'address': 'Somewhere'},  # bez lat/lon
            'destination': {'lat': 40.7, 'lon': -74.0, 'label': 'NYC'}
        })
        self.assertEqual(r.status_code, 400)


def main():
    unittest.main(verbosity=2, exit=False)


if __name__ == '__main__':
    main()
