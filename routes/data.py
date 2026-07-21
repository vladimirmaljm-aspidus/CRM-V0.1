import json
import logging
import sqlite3
import uuid
from flask import Blueprint, request, jsonify, session
from config import DB_FILE
from utils import log_audit, login_required, encrypt_data, decrypt_data

logger = logging.getLogger(__name__)

data_bp = Blueprint('data', __name__)

def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=60.0)
    conn.execute('PRAGMA busy_timeout=30000;')
    conn.execute('PRAGMA synchronous=NORMAL;')
    conn.execute('PRAGMA busy_timeout=60000;')
    return conn


def _retry_on_lock(fn, *args, max_attempts=6, **kwargs):
    """Wraps a callable so that transient 'database is locked' errors trigger
    retry with exponential backoff instead of bubbling up as 500.

    Root cause of the production symptoms 20/07/2026:
      - PythonAnywhere shared filesystem → SQLite locks propagate slowly
      - Background backup thread holds DB briefly during snapshot
      - Two concurrent uWSGI workers writing to same table race for lock

    Retry pattern: 100ms, 200ms, 400ms, 800ms, 1600ms, 3200ms — total up to
    6.3s before giving up. In practice locks resolve inside 500ms.
    """
    import time as _t
    import sqlite3 as _sq3
    for attempt in range(max_attempts):
        try:
            return fn(*args, **kwargs)
        except _sq3.OperationalError as e:
            msg = str(e).lower()
            if 'database is locked' not in msg and 'database is busy' not in msg:
                raise
            if attempt == max_attempts - 1:
                logger.error(f'DB lock persisted after {max_attempts} retries — giving up: {e}')
                raise
            wait = 0.1 * (2 ** attempt)
            logger.warning(f'DB locked (attempt {attempt+1}/{max_attempts}) — retrying in {wait:.2f}s')
            _t.sleep(wait)

# Moduli koji podrzavaju ownerId/sharedWith model vlasnistva.
# NAPOMENA: ranije je ownership filtriranje bilo hardkodirano samo za 'partners' i
# 'deals', zbog cega je '*_view_own' permisija za ostale module (accounts,
# transactions, demands, connections, offers) bila potpuno neefikasna - korisnik je
# video SVE zapise umesto samo svojih. Sada je generalizovano za sve module.
OWNERSHIP_MODULES = {'partners', 'deals', 'accounts', 'transactions', 'recurringExpenses', 'connections', 'offers', 'demands', 'shared_documents'}

# Settings kljucevi koji sadrze kredencijale/osetljive podatke i smeju se citati/pisati samo od strane admina.
SENSITIVE_SETTINGS_KEYS = {'comms_settings', 'firewall'}

def filter_by_ownership(key, item, module_name, permissions, user_id, role):
    """Vraca False ako korisnik NE sme da vidi ovaj zapis (nema view_all i nije vlasnik/deljeno sa njim)."""
    if role == 'admin':
        return True
    if permissions.get(f'{module_name}_view_all', False):
        return True
    if key not in OWNERSHIP_MODULES:
        return True
    owner_id = item.get('ownerId')
    shared_with = item.get('sharedWith', [])
    if owner_id is None:
        return True
    return owner_id == user_id or user_id in shared_with

@data_bp.route('/api/data/<key>', methods=['GET'])
@login_required
def get_data(key):
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT role, permissions FROM users WHERE id=?', (session['user_id'],))
        user_data = c.fetchone()
        
        if not user_data: 
            return jsonify({"error": "User not found"}), 401
        
        role = user_data[0]
        # Permisije su kriptovane, ovde ih čitamo
        permissions = decrypt_data(user_data[1]) if user_data[1] else {}
        user_id = session['user_id']
        
        def can_view(module):
            return role == 'admin' or permissions.get(f'{module}_view_all', False) or permissions.get(f'{module}_view_own', False) or permissions.get(f'{module}_view', False)

        perm_map = { 'partners':'partners', 'products':'products', 'deals':'deals', 'demands':'products', 'accounts':'finances', 'transactions':'finances', 'recurringExpenses':'finances', 'connections':'partners', 'offers':'offers', 'shared_documents':'shared_documents' }
        
        if key in perm_map and not can_view(perm_map[key]):
            return jsonify({"value": [], "error": "Unauthorized"}), 403 
        
        tables = ['partners', 'products', 'deals', 'demands', 'accounts', 'transactions', 'recurringExpenses', 'connections', 'offers', 'shared_documents']
        if key in tables:
            c.execute(f'SELECT data FROM {key}')
            rows = c.fetchall()
            data = []
            for row in rows:
                # Decrypt_data je pametan: pročitaće i ako je staro/kriptovano, i ako je novo/čisto
                item = decrypt_data(row[0]) 
                
                if role != 'admin':
                    module_name = perm_map.get(key, key)
                    if not filter_by_ownership(key, item, module_name, permissions, user_id, role):
                        continue

                    if key == 'deals' and not permissions.get('deals_view_costs', False):
                        item['purchasePrice'] = 0
                        item['bankCosts'] = 0
                        item['costs'] = []
                        item['supplierName'] = '*** HIDDEN ***'
                        item['supplierId'] = None
                        item['supplierBankDetails'] = ''
                    if key == 'products' and not permissions.get('products_view_prices', False):
                        for offer in item.get('supplyOffers', []):
                            offer['price'] = 0
                            offer['supplierId'] = None
                
                data.append(item)
                
            return jsonify({"value": data})
        else:
            # ISPRAVKA: comms_settings (SMTP host/user/LOZINKA) je ranije mogao da
            # procita BILO KOJI ulogovan korisnik. 'settings'/'company' ostaju javni
            # jer ih frontend koristi za osnovni prikaz aplikacije, ali osetljivi
            # kljucevi zahtevaju admin rolu.
            if key in SENSITIVE_SETTINGS_KEYS and role != 'admin':
                log_audit('SECURITY', 'database', f'Prevented read access to sensitive settings key: {key}', is_suspicious=True)
                return jsonify({"error": "Unauthorized"}), 403

            c.execute('SELECT value FROM settings WHERE key=?', (key,))
            row = c.fetchone()
            # Settings je OBAVEZNO kriptovan jer čuva SMTP lozinke
            return jsonify({"value": decrypt_data(row[0]) if row else None})
            
    except Exception as e:
        logger.error(f"get_data({key}) failed", exc_info=True)
        log_audit('ERROR', 'database', f'Read failed for module {key}', is_suspicious=True)
        return jsonify({"error": "DATABASE_ERROR"}), 503
    finally:
        if conn: conn.close()

@data_bp.route('/api/item/<key>', methods=['POST'])
@login_required
def save_single_item(key):
    item = request.json
    if not item: return jsonify({"error": "Empty payload"}), 400
    
    item_id = item.get('id')
    if not item_id: return jsonify({"error": "ID is required"}), 400
    
    conn = None
    action_log_msg = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute('BEGIN TRANSACTION;')
        
        c.execute('SELECT role, permissions FROM users WHERE id=?', (session['user_id'],))
        user_row = c.fetchone()
        if not user_row:
            conn.rollback()
            return jsonify({"error": "User not found"}), 401
            
        role = user_row[0]
        perms = decrypt_data(user_row[1]) if user_row[1] else {}
        
        perm_map = { 'partners':'partners_edit', 'products':'products_edit', 'deals':'deals_edit', 'demands':'products_edit', 'accounts':'finances_edit', 'transactions':'finances_edit', 'recurringExpenses':'finances_edit', 'connections':'partners_edit', 'offers':'offers_edit', 'shared_documents':'shared_documents_edit' }
        if role != 'admin' and key in perm_map and not perms.get(perm_map[key], False):
            conn.rollback()
            log_audit('SECURITY', 'database', f'Prevented write access to module: {key}', is_suspicious=True)
            return jsonify({"error": "Unauthorized"}), 403

        tables = ['partners', 'products', 'deals', 'demands', 'accounts', 'transactions', 'recurringExpenses', 'connections', 'offers', 'shared_documents']
        action = 'EDIT'
        
        if key in tables:
            c.execute(f'SELECT data FROM {key} WHERE id=?', (item_id,))
            existing_row = c.fetchone()
            
            if not existing_row: 
                action = 'CREATE'
                item['ownerId'] = session['user_id']
                item['sharedWith'] = []

                # ISPRAVKA (eskalacija privilegija): sanitizacija cena/troskova se
                # ranije radila SAMO pri izmeni postojeceg zapisa. Korisnik bez
                # 'deals_view_costs'/'products_view_prices' je pri KREIRANJU novog
                # deal-a/proizvoda i dalje mogao da upise nabavnu cenu, bankovne
                # podatke dobavljaca ili cene ponuda.
                if role != 'admin':
                    if key == 'deals' and not perms.get('deals_view_costs', False):
                        item['purchasePrice'] = 0
                        item['supplierId'] = None
                        item['supplierName'] = ''
                        item['supplierBankDetails'] = ''
                        item['costs'] = []
                        item['bankCosts'] = 0
                    if key == 'products' and not perms.get('products_view_prices', False):
                        for offer in item.get('supplyOffers', []) or []:
                            offer['price'] = 0
                            offer['supplierId'] = None
            else:
                existing = decrypt_data(existing_row[0])
                item['ownerId'] = existing.get('ownerId')
                item['sharedWith'] = existing.get('sharedWith', [])
                
                if role != 'admin':
                    if key == 'deals' and not perms.get('deals_view_costs', False):
                        item['purchasePrice'] = existing.get('purchasePrice', 0)
                        item['supplierId'] = existing.get('supplierId', '')
                        item['supplierName'] = existing.get('supplierName', '')
                        item['supplierBankDetails'] = existing.get('supplierBankDetails', '')
                        item['costs'] = existing.get('costs', [])
                        item['bankCosts'] = existing.get('bankCosts', 0)
                    if key == 'products' and not perms.get('products_view_prices', False):
                        item['supplyOffers'] = existing.get('supplyOffers', [])

            # OPTIMIZACIJA: Čist JSON upis za maksimalnu brzinu baze.
            # v22 FIX: wrap u retry helper — pod PythonAnywhere SQLite lock je čest,
            # bez retry-ja svaki lock = izgubljen zapis + 500 error klijentu.
            _retry_on_lock(c.execute,
                           f'INSERT OR REPLACE INTO {key} (id, data) VALUES (?, ?)',
                           (item_id, json.dumps(item)))
            action_log_msg = (action, key, f'Updated item ID: {item_id}', False)
        
        elif key == 'settings' or key == 'company' or key == 'firewall' or key in SENSITIVE_SETTINGS_KEYS:
            # KRITICNA ISPRAVKA: ova grana ranije uopste nije proveravala rolu, pa je
            # SVAKI ulogovani korisnik mogao da prepise SMTP lozinku, podatke firme i
            # druge sistemske postavke preko ovog endpointa.
            if role != 'admin':
                conn.rollback()
                log_audit('SECURITY', 'database', f'Prevented write access to settings key: {key}', is_suspicious=True)
                return jsonify({"error": "Unauthorized"}), 403
            # ENKRIPCIJA: Podešavanja ostaju bezbedna u trezoru
            c.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, encrypt_data(item)))
            action_log_msg = ('EDIT', 'settings', f'Updated settings for {key}', False)
            # Ako je admin promenio firewall postavke, odmah ih primeni bez rekstart-a.
            if key == 'firewall':
                try:
                    from utils import load_firewall_settings as _reload_fw
                    _reload_fw()
                except Exception:
                    pass
        
        conn.commit()
        
        if action_log_msg:
            log_audit(action_log_msg[0], action_log_msg[1], action_log_msg[2], is_suspicious=action_log_msg[3])
            
        return jsonify({"status": "success", "id": item_id})
        
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"save_single_item({key}) failed", exc_info=True)
        log_audit('ERROR', 'database', f'Save failed for module {key}', is_suspicious=True)
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500
    finally:
        if conn: conn.close()

@data_bp.route('/api/item/<key>/<item_id>', methods=['DELETE'])
@login_required
def delete_single_item(key, item_id):
    conn = None
    action_log_msg = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('BEGIN TRANSACTION;')
        
        c.execute('SELECT role, permissions FROM users WHERE id=?', (session['user_id'],))
        user_row = c.fetchone()
        if not user_row:
            conn.rollback()
            return jsonify({"error": "User not found"}), 401
            
        role = user_row[0]
        perms = decrypt_data(user_row[1]) if user_row[1] else {}
        
        perm_map = { 'partners':'partners_delete', 'products':'products_delete', 'deals':'deals_delete', 'demands':'products_delete', 'accounts':'finances_delete', 'transactions':'finances_delete', 'recurringExpenses':'finances_delete', 'connections':'partners_delete', 'offers':'offers_delete', 'shared_documents':'shared_documents_delete' }
        if role != 'admin' and key in perm_map and not perms.get(perm_map[key], False):
            conn.rollback()
            log_audit('SECURITY', 'database', f'Prevented delete from module {key} (ID: {item_id})', is_suspicious=True)
            return jsonify({"error": "Unauthorized"}), 403

        tables = ['partners', 'products', 'deals', 'demands', 'accounts', 'transactions', 'recurringExpenses', 'connections', 'offers', 'shared_documents']
        if key in tables:
            
            # Cascade Delete
            if key == 'deals':
                c.execute("SELECT id, data FROM transactions")
                for t_row in c.fetchall():
                    tx = decrypt_data(t_row[1])
                    if tx.get('dealId') == item_id:
                        c.execute("DELETE FROM transactions WHERE id=?", (t_row[0],))
                        log_audit('DELETE', 'finances', f'Auto-deleted orphaned transaction ID: {t_row[0]} linked to Deal: {item_id}', is_suspicious=False)

            c.execute(f'DELETE FROM {key} WHERE id = ?', (item_id,))
            conn.commit()
            action_log_msg = ('DELETE', key, f'Deleted item ID: {item_id}', False)
        else:
            conn.rollback()
            return jsonify({"error": "Invalid table"}), 400
            
        if action_log_msg:
            log_audit(action_log_msg[0], action_log_msg[1], action_log_msg[2], is_suspicious=action_log_msg[3])
            
        return jsonify({"status": "success"})
        
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"delete_single_item({key}, {item_id}) failed", exc_info=True)
        log_audit('ERROR', 'database', f'Delete failed for module {key}', is_suspicious=True)
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500
    finally:
        if conn: conn.close()

@data_bp.route('/api/data/<key>', methods=['POST'])
@login_required
def save_data(key):
    conn = None
    action_log_msg = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute('BEGIN TRANSACTION;')
        
        tables = ['partners', 'products', 'deals', 'demands', 'accounts', 'transactions', 'recurringExpenses', 'connections', 'offers', 'shared_documents']
        
        if key in tables:
            if session.get('role') != 'admin':
                conn.rollback()
                log_audit('SECURITY', 'database', f'Prevented Bulk Save for module: {key}', is_suspicious=True)
                return jsonify({"error": "Unauthorized"}), 403
                
            data = request.json.get('value', [])
            c.execute(f'DELETE FROM {key}') 
            for item in data:
                c.execute(f'INSERT INTO {key} (id, data) VALUES (?, ?)', (item.get('id', str(uuid.uuid4())), json.dumps(item)))
            action_log_msg = ('CREATE', key, 'Admin performed bulk save on table.', False)
        else:
            # KRITICNA ISPRAVKA: identicna rupa kao gore - ova grana nije proveravala
            # rolu, pa je bilo koji ulogovan korisnik mogao da prepise proizvoljan
            # settings kljuc (ukljucujuci SMTP kredencijale) preko bulk-save rute.
            if session.get('role') != 'admin':
                conn.rollback()
                log_audit('SECURITY', 'database', f'Prevented settings write for key: {key}', is_suspicious=True)
                return jsonify({"error": "Unauthorized"}), 403

            data = request.json.get('value')
            c.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, encrypt_data(data)))
            action_log_msg = ('EDIT', 'settings', f'Updated settings for {key}', False)
            
        conn.commit()
        
        if action_log_msg:
            log_audit(action_log_msg[0], action_log_msg[1], action_log_msg[2], is_suspicious=action_log_msg[3])
            
        return jsonify({"status": "success"})
        
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"save_data({key}) failed", exc_info=True)
        log_audit('ERROR', 'database', f'Bulk save failed for module {key}', is_suspicious=True)
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500
    finally:
        if conn: conn.close()

# ==========================================================
#  OFFERS: konverzija ponude u dil / fakturu
# ==========================================================

@data_bp.route('/api/deals/from_offer/<offer_id>', methods=['POST'])
@login_required
def create_deal_from_offer(offer_id):
    """Kreira novi dil iz postojeće ponude. Podržava dva režima:
    1. Klijent je već prihvatio ponudu preko portala (clientStatus='accepted') → svako
       sa 'offers_to_deal' permisijom može da klikne 'Kreiraj dil'.
    2. Klijent nema portal ili admin želi da bypass-uje (payload.force=true) → samo
       admin ili korisnik sa 'offers_to_deal_force' permisijom sme (jer preskače
       klijentovu potvrdu).
    Bez ovih permisija radnik NE vidi dugme (kontroliše se frontend hasPerm)."""
    role = session.get('role')
    payload = request.get_json(silent=True) or {}
    force = bool(payload.get('force', False))

    # Provera permisija
    perms = {}
    if role != 'admin':
        conn_p = get_db_connection()
        try:
            cp = conn_p.cursor()
            cp.execute('SELECT permissions FROM users WHERE id=?', (session['user_id'],))
            prow = cp.fetchone()
        finally:
            conn_p.close()
        perms = decrypt_data(prow[0]) if prow and prow[0] else {}
        if not perms.get('offers_to_deal', False):
            log_audit('SECURITY', 'offers', f'Prevented unauthorized offer→deal conversion (offer {offer_id})', is_suspicious=True)
            return jsonify({"error": "UNAUTHORIZED"}), 403
        if force and not perms.get('offers_to_deal_force', False):
            log_audit('SECURITY', 'offers', f'Prevented forced offer→deal without client approval (offer {offer_id})', is_suspicious=True)
            return jsonify({"error": "FORCE_NOT_ALLOWED"}), 403

    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute('BEGIN TRANSACTION;')
        c.execute("SELECT data FROM offers WHERE id=?", (offer_id,))
        row = c.fetchone()
        if not row:
            conn.rollback()
            return jsonify({"error": "OFFER_NOT_FOUND"}), 404
        offer = decrypt_data(row[0])
        if not isinstance(offer, dict): offer = json.loads(row[0]) if isinstance(row[0], str) else {}

        # Ako klijent nije prihvatio i nije force, blokiraj
        client_accepted = offer.get('clientStatus') == 'accepted'
        if not client_accepted and not force:
            conn.rollback()
            return jsonify({"error": "CLIENT_HAS_NOT_ACCEPTED", "message": "Klijent nije potvrdio ponudu preko portala. Koristite 'force' za override."}), 409

        # Ako je ponuda vec konvertovana, sprecavamo duplu konverziju
        if offer.get('convertedDealId'):
            existing_deal_id = offer['convertedDealId']
            conn.rollback()
            return jsonify({"error": "ALREADY_CONVERTED", "dealId": existing_deal_id}), 409

        # Kreiraj dil iz ponude
        deal_id = str(uuid.uuid4())
        now_iso = None
        try:
            from datetime import datetime as _dt, timezone as _tz
            now_iso = _dt.now(_tz.utc).isoformat().replace('+00:00', 'Z')
        except Exception:
            pass

        first_item = (offer.get('items') or [{}])[0] if isinstance(offer.get('items'), list) else {}

        # KRITIČNO: dil MORA biti VERNA kopija svega što je klijent prihvatio.
        # Svako polje koje ne prenesemo → admin bi ga morao ručno prepisati, sa
        # rizikom greške koja odstupa od onoga što je klijent potpisao.
        # Zato prenosimo apsolutno sve komercijalne/logističke/finansijske podatke.
        deal = {
            'id': deal_id,
            'contractId': f"D-{offer.get('offerNo', '')}",
            'sourceOfferId': offer_id,
            'sourceOfferNo': offer.get('offerNo', ''),
            'sourceOfferDate': offer.get('date') or offer.get('createdAt'),
            'sourceOfferAcceptedAt': offer.get('clientAcceptedAt'),
            'clientAcceptanceNote': offer.get('clientNote', ''),
            'status': 'negotiation',
            'createdAt': now_iso,
            'ownerId': session.get('user_id', 'SYSTEM'),
            'sharedWith': [],
            # === KUPAC ===
            'buyerId': offer.get('customerId'),
            'buyerName': '',
            'buyerContactEmail': '',
            'buyerContactPhone': '',
            'buyerAddress': '',
            # === PROIZVOD (glavni) — za backward-compat sa CRM prikazom ===
            'productId': offer.get('productId') or first_item.get('productId'),
            'productName': offer.get('productName') or first_item.get('productName') or '',
            'hsCode': offer.get('hsCode') or first_item.get('hsCode') or '',
            'origin': offer.get('origin') or first_item.get('origin') or offer.get('productOrigin') or '',
            'detailedSpec': offer.get('detailedSpec') or offer.get('productSpec') or first_item.get('detailedSpec') or '',
            'quantity': offer.get('quantity') or first_item.get('quantity'),
            'unit': offer.get('unit') or first_item.get('unit') or '',
            # === CENA I VALUTA (glavna stavka) ===
            'sellingPrice': offer.get('sellingPrice') or offer.get('price') or first_item.get('price'),
            'sellingCurrency': offer.get('currency') or 'USD',
            # === KOMPLETNA LISTA STAVKI (multi-line offer) ===
            'items': offer.get('items') or [],
            'services': offer.get('services') or [],
            # === LOGISTIKA (POL/POD/vessel/container/lead) ===
            'incoterm': offer.get('incoterm') or first_item.get('incoterm') or '',
            'logistics': {
                'pol': offer.get('pol', ''),
                'pod': offer.get('pod', ''),
                'vessel': offer.get('vessel', ''),
                'containerNo': offer.get('containerNo', ''),
                'packaging': offer.get('packaging') or first_item.get('packaging') or '',
                'leadTime': offer.get('leadTime') or first_item.get('leadTime') or '',
                'shipmentDate': '',   # Popuni admin kad se ugovori tačan datum
                'blNumber': ''         # Popuni tek pri utovaru
            },
            # === TEŽINE / VOLUMEN — bitno za space u kontejneru ===
            'weights': offer.get('weights') or {},
            # === FINANSIJE ===
            'paymentTerms': offer.get('paymentTerms', ''),
            'discount': offer.get('discount') or 0,
            'customVatRate': offer.get('customVatRate') or 0,
            'advance': offer.get('advance') or 0,
            'taxClause': offer.get('taxClause', ''),
            # === BANKARSKE INSTRUKCIJE — kritično, admin ne sme da ih ručno prepisuje ===
            'bankDetails': offer.get('bankDetails', ''),
            # === NAPOMENE I DODATNO ===
            'notes': offer.get('notes', ''),
            'certificates': (first_item.get('certificates') if isinstance(first_item, dict) else '') or '',
            # === PDF REFERENCE — dokument koji je klijent video/potpisao ===
            'sourceOfferDocumentId': offer.get('documentId'),
            'sourceOfferPdfFileUrl': offer.get('pdfFileUrl'),
        }

        # Uzmi kupca iz partners tabele — puni podaci umesto samo ime.
        # NAPOMENA: legacy record-i imaju `address` kao STRING (jedan textarea),
        # dok noviji zapisi koriste dict {street, city, country}. Kod mora
        # da podržava obe forme — inače ovde bio 500 pri konverziji.
        c.execute("SELECT data FROM partners WHERE id=?", (offer.get('customerId'),))
        p_row = c.fetchone()
        if p_row:
            p_data = decrypt_data(p_row[0])
            if isinstance(p_data, dict):
                deal['buyerName'] = p_data.get('companyName') or p_data.get('name', '')
                contact = p_data.get('contact')
                contact = contact if isinstance(contact, dict) else {}
                deal['buyerContactEmail'] = contact.get('email') or p_data.get('email', '')
                deal['buyerContactPhone'] = contact.get('phone') or p_data.get('phone', '')
                addr = p_data.get('address')
                if isinstance(addr, dict):
                    deal['buyerAddress'] = ', '.join(filter(None, [
                        addr.get('street', ''), addr.get('city', ''), addr.get('country', '')
                    ]))
                elif isinstance(addr, str):
                    deal['buyerAddress'] = ', '.join(filter(None, [
                        addr, p_data.get('city', ''), p_data.get('country', '')
                    ]))
                else:
                    deal['buyerAddress'] = ''
                deal['buyerTaxId'] = p_data.get('taxId', '')
                deal['buyerRegNumber'] = p_data.get('regNumber', '')

        # Ako ponuda ima productId, obogati proizvod-specifikaciju iz kataloga
        if deal.get('productId'):
            c.execute("SELECT data FROM products WHERE id=?", (deal['productId'],))
            pr_row = c.fetchone()
            if pr_row:
                pr = decrypt_data(pr_row[0])
                if isinstance(pr, dict):
                    if not deal.get('productName'): deal['productName'] = pr.get('name', '')
                    if not deal.get('hsCode'): deal['hsCode'] = pr.get('hsCode', '')
                    if not deal.get('detailedSpec'): deal['detailedSpec'] = pr.get('detailedSpec', '')
                    # Ako je jedan supply offer selektovan preko supplyOfferIndex, prenesi origin i cenu nabavke
                    supply_offers = pr.get('supplyOffers') or []
                    idx = first_item.get('supplyOfferIndex') if isinstance(first_item, dict) else None
                    supply = None
                    if isinstance(idx, int) and 0 <= idx < len(supply_offers):
                        supply = supply_offers[idx]
                    elif first_item.get('supplierId') if isinstance(first_item, dict) else None:
                        for so in supply_offers:
                            if so.get('supplierId') == first_item.get('supplierId'):
                                supply = so; break
                    if supply:
                        if not deal.get('origin'): deal['origin'] = supply.get('country', '')
                        deal['purchasePrice'] = supply.get('price', 0)
                        deal['purchaseCurrency'] = supply.get('currency', '')
                        deal['supplierId'] = supply.get('supplierId')
                        deal['purchaseIncoterm'] = supply.get('incoterm', '')
                        if not deal['certificates']: deal['certificates'] = supply.get('certificates', '')
                        # Popuni ime dobavljača
                        if deal.get('supplierId'):
                            c.execute("SELECT data FROM partners WHERE id=?", (deal['supplierId'],))
                            sup_row = c.fetchone()
                            if sup_row:
                                sup_data = decrypt_data(sup_row[0])
                                if isinstance(sup_data, dict):
                                    deal['supplierName'] = sup_data.get('companyName', '')

        # Ako fali bankDetails na dilu, uzmi ih iz podataka firme (settings.company)
        if not deal.get('bankDetails'):
            c.execute("SELECT value FROM settings WHERE key='company'")
            comp_row = c.fetchone()
            if comp_row:
                comp = decrypt_data(comp_row[0])
                if isinstance(comp, dict):
                    parts = []
                    if comp.get('bankName'):    parts.append(f"Bank: {comp['bankName']}")
                    if comp.get('bankAddress'): parts.append(comp['bankAddress'])
                    if comp.get('accountNum'):  parts.append(f"IBAN: {comp['accountNum']}")
                    if comp.get('swift'):       parts.append(f"SWIFT: {comp['swift']}")
                    if comp.get('corrBank'):    parts.append(f"Correspondent: {comp['corrBank']}")
                    if parts: deal['bankDetails'] = '\n'.join(parts)

        _retry_on_lock(c.execute, "INSERT INTO deals (id, data) VALUES (?, ?)", (deal_id, json.dumps(deal)))

        # Označi ponudu kao konvertovanu
        offer['convertedDealId'] = deal_id
        offer['convertedAt'] = now_iso
        c.execute("UPDATE offers SET data=? WHERE id=?", (json.dumps(offer), offer_id))
        conn.commit()

        forced_msg = " (FORCED — client had not accepted via portal)" if (force and not client_accepted) else ""
        log_audit('CREATE', 'deals', f"Created deal {deal_id} from offer {offer.get('offerNo', offer_id)}{forced_msg}", is_suspicious=False)
        return jsonify({"status": "success", "dealId": deal_id, "deal": deal})
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"create_deal_from_offer({offer_id}) failed", exc_info=True)
        log_audit('ERROR', 'offers', 'offer→deal conversion failed', is_suspicious=True)
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500
    finally:
        if conn: conn.close()


@data_bp.route('/api/offers/verify_hash', methods=['POST'])
@login_required
def verify_offer_hash():
    """Provera autentičnosti PDF-a preko VERIFICATION HASH-a koji je ugrađen u
    footer svake ponude. Admin unosi hash iz sumnjivog dokumenta; server
    poredi sa hash-om koji bi trenutno generisao za tu ponudu (deterministički).

    Vraća:
      { valid: true, offer_no, customer, generated_at } — ako se poklapa
      { valid: false, reason: 'HASH_MISMATCH' | 'OFFER_NOT_FOUND' } — inače

    Bilo koja izmena na dokumentu (imena, cifre, datumi) menja renderovani PDF.
    Ali verification hash u footeru je deterministički vezan za offer_id +
    offer_no — pa ako je neko menjao dokument u editoru A HASH OSTAVIO, ovaj
    endpoint neće prijaviti mismatch. Zato se autentifikacija vrši sledećim:
      1) Admin unosi hash + broj ponude (offerNo) iz PDF-a.
      2) Server pronađe ponudu po offerNo u bazi.
      3) Regeneriše hash iz DB podataka.
      4) Vraća da li se poklapa I ceo trenutan sadržaj ponude — pa admin
         može vizuelno da uporedi ceo dokument sa sumnjivim.
    """
    payload = request.get_json(silent=True) or {}
    offer_no = str(payload.get('offer_no') or payload.get('offerNo') or '').strip()
    provided_hash = str(payload.get('hash') or payload.get('verification_hash') or '').strip().upper()
    if not offer_no or not provided_hash:
        return jsonify({"valid": False, "reason": "OFFER_NO_AND_HASH_REQUIRED"}), 400

    from pdf_generator import _make_verification_hash

    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT id, data FROM offers")
        found = None
        for r in c.fetchall():
            od = decrypt_data(r[1])
            if isinstance(od, dict) and od.get('offerNo') == offer_no:
                found = (r[0], od); break
        if not found:
            return jsonify({"valid": False, "reason": "OFFER_NOT_FOUND"})
        offer_id, offer_data = found
        expected = _make_verification_hash(offer_id, offer_no)
        valid = provided_hash == expected
        log_audit('SECURITY', 'offers',
                  f"Document hash verification: offer {offer_no} → {'VALID' if valid else 'MISMATCH'}",
                  is_suspicious=(not valid))
        return jsonify({
            "valid": valid,
            "expected_hash": expected if valid else None,
            "provided_hash": provided_hash,
            "offer_no": offer_no,
            "customer_id": offer_data.get('customerId'),
            "generated_at": offer_data.get('pdfGeneratedAt') or offer_data.get('date'),
            "current_total": offer_data.get('sellingPrice'),
            "current_currency": offer_data.get('currency'),
            "reason": None if valid else "HASH_MISMATCH"
        })
    finally:
        conn.close()


@data_bp.route('/api/documents/verify_upload', methods=['POST'])
@login_required
def verify_document_upload():
    """Kriptografska provera integriteta uploadovanog PDF-a.

    Admin (ili neko sa dozvolom) uploaduje PDF fajl koji je vratio klijent
    (potpisan, ili čak i ne). Server:
      1) Računa SHA-256 nad bajtovima.
      2) Traži u shared_documents zapis koji ima taj pdfContentHash.
      3) Ako pronađe → binding hash + snapshot ponude → dokument je autentičan.
      4) Ako NE pronađe → poredi sa svim shared_documents.pdfContentHash-ovima
         i vraća prvi mismatch kao "modified" ako binding hash SEED odgovara
         (offerNo se pronađe u PDF metapodacima ili fajl-imenu).
      5) U svakom slučaju vraća detaljne rezultate za forensiku.

    Ovo štiti od:
      - Zamene brojeva (cena, količina) u PDF editoru
      - Umetanja/brisanja stranica
      - Menjanja footer verification hash-a
      - Menjanja metadata (Author, Title, Subject)
    """
    if 'file' not in request.files:
        return jsonify({"error": "FILE_REQUIRED"}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({"error": "FILE_EMPTY"}), 400
    data = f.read()
    if not data:
        return jsonify({"error": "FILE_EMPTY"}), 400
    if not data.startswith(b'%PDF'):
        return jsonify({"error": "NOT_A_PDF"}), 400

    import hashlib
    computed_hash = hashlib.sha256(data).hexdigest().upper()

    # Traži direct match
    conn = get_db_connection()
    match = None
    matched_by_seed = None
    try:
        c = conn.cursor()
        c.execute("SELECT id, data FROM shared_documents")
        rows = c.fetchall()
        for r in rows:
            doc = decrypt_data(r[1])
            if not isinstance(doc, dict): continue
            if doc.get('pdfContentHash') == computed_hash:
                match = doc; break
        if not match:
            # Sekundarni pretres: možda je fajl-ime `Offer_XYZ.pdf` → izvuci offerNo
            import re as _re
            m = _re.search(rb'/Title\s*\((.*?)\)', data[:16384])
            if m:
                title = m.group(1).decode('utf-8', errors='replace')
                m2 = _re.search(r'Offer\s+([A-Za-z0-9\-/_]+)', title)
                if m2:
                    seed_offer_no = m2.group(1)
                    for r in rows:
                        doc = decrypt_data(r[1])
                        if isinstance(doc, dict) and doc.get('fileName', '').endswith(f'{seed_offer_no}.pdf'):
                            matched_by_seed = doc; break
    finally:
        conn.close()

    if match:
        offer = None
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT data FROM offers WHERE id=?", (match.get('sourceOfferId'),))
            row = c.fetchone()
            if row: offer = decrypt_data(row[0])
            conn.close()
        except Exception:
            pass
        log_audit('SECURITY', 'documents',
                  f"PDF integrity check: MATCH for {match.get('fileName')} (hash={computed_hash[:16]}...)",
                  is_suspicious=False)
        return jsonify({
            "valid": True,
            "status": "AUTHENTIC",
            "message": "Document is bit-exact identical to the version sent from this system.",
            "computed_hash": computed_hash,
            "document": {
                "id": match.get('id'),
                "fileName": match.get('fileName'),
                "docType": match.get('docType'),
                "createdAt": match.get('createdAt'),
                "sourceOfferId": match.get('sourceOfferId'),
                "bindingHash": match.get('bindingHash'),
                "shortVerification": match.get('shortVerification'),
            },
            "offer_snapshot": {
                "offerNo": offer.get('offerNo') if offer else None,
                "customerId": offer.get('customerId') if offer else None,
                "sellingPrice": offer.get('sellingPrice') if offer else None,
                "currency": offer.get('currency') if offer else None,
                "quantity": offer.get('quantity') if offer else None,
            } if offer else None,
        })

    if matched_by_seed:
        log_audit('SECURITY', 'documents',
                  f"PDF integrity check: MODIFIED — filename matches {matched_by_seed.get('fileName')} but content hash differs (uploaded={computed_hash[:16]}, expected={matched_by_seed.get('pdfContentHash', '')[:16]})",
                  is_suspicious=True)
        return jsonify({
            "valid": False,
            "status": "MODIFIED",
            "message": "This PDF appears to be a modified version of a document issued from this system. Content has been altered.",
            "computed_hash": computed_hash,
            "expected_hash": matched_by_seed.get('pdfContentHash'),
            "document": {
                "id": matched_by_seed.get('id'),
                "fileName": matched_by_seed.get('fileName'),
                "docType": matched_by_seed.get('docType'),
                "createdAt": matched_by_seed.get('createdAt'),
            }
        })

    log_audit('SECURITY', 'documents',
              f"PDF integrity check: UNKNOWN document (hash={computed_hash[:16]}...)",
              is_suspicious=True)
    return jsonify({
        "valid": False,
        "status": "UNKNOWN",
        "message": "This PDF was not issued from this system. Content hash not found in our records.",
        "computed_hash": computed_hash,
    })


@data_bp.route('/api/offers/preview_pdf', methods=['POST'])
@login_required
def preview_offer_pdf():
    """Vraća PDF bytes za ponudu koja MOŽDA nije snimljena u bazi. CRM koristi
    ovo za preview u browseru (blob URL) pre 'Save & Generate' — tako admin
    vidi TAČNO onaj isti PDF koji će klijent kasnije videti u portalu.
    Time se uklanja stara nekonzistentnost između client-side jsPDF u CRM-u
    i server-side ReportLab-a u portalu — sada je JEDAN pravi izvor istine."""
    payload = request.get_json(silent=True) or {}
    offer = payload if isinstance(payload, dict) else {}
    if not offer:
        return jsonify({"error": "OFFER_PAYLOAD_REQUIRED"}), 400
    try:
        from pdf_generator import build_offer_pdf
        pdf_bytes = build_offer_pdf(offer)
    except Exception as e:
        logger.error(f"preview_offer_pdf failed: {e}", exc_info=True)
        return jsonify({"error": "PDF_GENERATION_FAILED"}), 500
    from flask import Response
    return Response(pdf_bytes, mimetype='application/pdf',
                    headers={'Content-Disposition': 'inline; filename="offer_preview.pdf"',
                             'Cache-Control': 'no-store'})


@data_bp.route('/api/offers/<offer_id>/generate_pdf', methods=['POST'])
@login_required
def generate_offer_pdf_endpoint(offer_id):
    """Generise (i cuva u vault) profesionalan PDF ponude. Klijent u portalu tada
    moze da preuzme dokument preko standardnog /api/portal/document/... koji
    audit-loguje download.

    Permisija: admin ili neko sa offers_edit."""
    role = session.get('role')
    if role != 'admin':
        conn_p = get_db_connection()
        try:
            cp = conn_p.cursor()
            cp.execute('SELECT permissions FROM users WHERE id=?', (session['user_id'],))
            prow = cp.fetchone()
        finally:
            conn_p.close()
        perms = decrypt_data(prow[0]) if prow and prow[0] else {}
        if not (perms.get('offers_edit') or perms.get('offers_view_all')):
            log_audit('SECURITY', 'offers', 'Prevented unauthorized PDF generation', is_suspicious=True)
            return jsonify({"error": "UNAUTHORIZED"}), 403

    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT data FROM offers WHERE id=?", (offer_id,))
        row = c.fetchone()
        if not row:
            return jsonify({"error": "OFFER_NOT_FOUND"}), 404
        offer = decrypt_data(row[0])
        if not isinstance(offer, dict): offer = json.loads(row[0]) if isinstance(row[0], str) else {}
    finally:
        conn.close()

    try:
        from pdf_generator import save_offer_pdf_to_vault
    except Exception as e:
        return jsonify({"error": f"PDF_MODULE_UNAVAILABLE: {e}"}), 500

    doc_id, file_url = save_offer_pdf_to_vault(offer)
    if not doc_id:
        return jsonify({"error": "PDF_GENERATION_FAILED"}), 500

    # Poveži ponudu sa dokumentom kako bi klijent u portalu imao dugme download
    from datetime import datetime as _dt, timezone as _tz
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT data FROM offers WHERE id=?", (offer_id,))
        row = c.fetchone()
        if row:
            of = decrypt_data(row[0])
            if not isinstance(of, dict): of = json.loads(row[0]) if isinstance(row[0], str) else {}
            of['documentId'] = doc_id
            of['pdfFileUrl'] = file_url
            of['pdfGeneratedAt'] = _dt.now(_tz.utc).isoformat().replace('+00:00', 'Z')
            c.execute("UPDATE offers SET data=? WHERE id=?", (json.dumps(of), offer_id))
            conn.commit()
    finally:
        conn.close()

    log_audit('CREATE', 'offers', f'Generated PDF for offer {offer_id} → vault doc {doc_id}', is_suspicious=False)

    # Pokušaj email obaveštenje klijentu
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT data FROM partners WHERE id=?", (offer.get('customerId'),))
        prow = c.fetchone()
        conn.close()
        if prow:
            pdata = decrypt_data(prow[0])
            if isinstance(pdata, dict):
                email = pdata.get('contact', {}).get('email') or pdata.get('email')
                token = pdata.get('portalToken', '')
                portal_url = request.url_root.rstrip('/') + f"/portal/{token}" if token else request.url_root
                if email:
                    from utils_email import send_new_offer
                    send_new_offer(email, pdata.get('companyName', ''), offer.get('offerNo', ''), portal_url)
    except Exception:
        pass

    return jsonify({"status": "success", "documentId": doc_id, "fileUrl": file_url})
