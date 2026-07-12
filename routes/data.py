import json
import sqlite3
import uuid
from flask import Blueprint, request, jsonify, session
from config import DB_FILE
from utils import log_audit, login_required, encrypt_data, decrypt_data

data_bp = Blueprint('data', __name__)

def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=60.0) 
    conn.execute('PRAGMA journal_mode=WAL;') 
    conn.execute('PRAGMA synchronous=NORMAL;')
    conn.execute('PRAGMA busy_timeout=60000;')
    return conn

# Moduli koji podrzavaju ownerId/sharedWith model vlasnistva.
# NAPOMENA: ranije je ownership filtriranje bilo hardkodirano samo za 'partners' i
# 'deals', zbog cega je '*_view_own' permisija za ostale module (accounts,
# transactions, demands, connections, offers) bila potpuno neefikasna - korisnik je
# video SVE zapise umesto samo svojih. Sada je generalizovano za sve module.
OWNERSHIP_MODULES = {'partners', 'deals', 'accounts', 'transactions', 'recurringExpenses', 'connections', 'offers', 'demands', 'shared_documents'}

# Settings kljucevi koji sadrze kredencijale/osetljive podatke i smeju se citati/pisati samo od strane admina.
SENSITIVE_SETTINGS_KEYS = {'comms_settings'}

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
        return jsonify({"error": f"Database error. ({str(e)})"}), 503
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

            # OPTIMIZACIJA: Čist JSON upis za maksimalnu brzinu baze
            c.execute(f'INSERT OR REPLACE INTO {key} (id, data) VALUES (?, ?)', (item_id, json.dumps(item)))
            action_log_msg = (action, key, f'Updated item ID: {item_id}', False)
        
        elif key == 'settings' or key == 'company' or key in SENSITIVE_SETTINGS_KEYS:
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
        
        conn.commit()
        
        if action_log_msg:
            log_audit(action_log_msg[0], action_log_msg[1], action_log_msg[2], is_suspicious=action_log_msg[3])
            
        return jsonify({"status": "success", "id": item_id})
        
    except Exception as e:
        if conn: conn.rollback() 
        return jsonify({"error": f"Internal server error. ({str(e)})"}), 500
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
        return jsonify({"error": f"Internal server error. ({str(e)})"}), 500
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
        return jsonify({"error": f"Critical error. ({str(e)})"}), 500
    finally:
        if conn: conn.close()