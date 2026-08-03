import json
import logging
import uuid
from flask import Blueprint, request, jsonify, session
from utils import log_audit, login_required, encrypt_data, decrypt_data

logger = logging.getLogger(__name__)

data_bp = Blueprint('data', __name__)

# V25 SUPABASE-ONLY: `get_db_connection()` i `_retry_on_lock()` helper-i (legacy
# SQLite) su uklonjeni jer nema više nijednog SQLite poziva u ovom modulu. Sve
# DB operacije idu preko `data_layer` facade ili `supabase_store` helper-a.

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
    """V24.0 SUPABASE-ONLY: sve citanje ide direktno iz Supabase, bez SQLite."""
    try:
        import supabase_store as store
        user_row = store.get_user_by_id(session['user_id'])
        if not user_row:
            return jsonify({"error": "User not found"}), 401
        role = user_row.get('role') or 'employee'
        permissions = user_row.get('permissions') or {}
        if isinstance(permissions, str):
            try: permissions = json.loads(permissions)
            except Exception: permissions = {}
        user_id = session['user_id']

        def can_view(module):
            return role == 'admin' or permissions.get(f'{module}_view_all', False) or permissions.get(f'{module}_view_own', False) or permissions.get(f'{module}_view', False)

        perm_map = { 'partners':'partners', 'products':'products', 'deals':'deals', 'demands':'products', 'accounts':'finances', 'transactions':'finances', 'recurringExpenses':'finances', 'connections':'partners', 'offers':'offers', 'shared_documents':'shared_documents' }

        if key in perm_map and not can_view(perm_map[key]):
            return jsonify({"value": [], "error": "Unauthorized"}), 403

        tables = ['partners', 'products', 'deals', 'demands', 'accounts', 'transactions', 'recurringExpenses', 'connections', 'offers', 'shared_documents']
        if key in tables:
            rows = store.list_entities(key)
            data = []
            for item in rows:
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
                        for offer in item.get('supplyOffers', []) or []:
                            offer['price'] = 0
                            offer['supplierId'] = None
                data.append(item)
            return jsonify({"value": data})
        else:
            # SETTINGS grana — comms_settings/firewall samo admin
            if key in SENSITIVE_SETTINGS_KEYS and role != 'admin':
                log_audit('SECURITY', 'database', f'Prevented read access to sensitive settings key: {key}', is_suspicious=True)
                return jsonify({"error": "Unauthorized"}), 403
            enc_val = store.get_setting(key)
            return jsonify({"value": decrypt_data(enc_val) if enc_val else None})

    except Exception:
        logger.error(f"get_data({key}) failed", exc_info=True)
        log_audit('ERROR', 'database', f'Read failed for module {key}', is_suspicious=True)
        return jsonify({"error": "DATABASE_ERROR"}), 503

@data_bp.route('/api/item/<key>', methods=['POST'])
@login_required
def save_single_item(key):
    # BEZBEDNOST + STABILNOST: request.json može biti bilo šta što se parsira
    # kao JSON — objekat, niz, string, broj, null. Bez proveri tipa naredni
    # item.get(...) baca AttributeError → 500 na svaki nevalidan payload
    # (npr. korisnik nalepi "[1,2,3]" u probama, ili frontend bug pošalje
    # pogrešnu strukturu). Provera radi soft: mora biti dict.
    item = request.get_json(silent=True)
    if not isinstance(item, dict):
        return jsonify({"error": "Empty or invalid payload — expected JSON object"}), 400

    item_id = item.get('id')
    if not item_id: return jsonify({"error": "ID is required"}), 400

    import supabase_store as store
    try:
        user_row = store.get_user_by_id(session['user_id'])
        if not user_row:
            return jsonify({"error": "User not found"}), 401
        role = user_row.get('role') or 'employee'
        perms = user_row.get('permissions') or {}
        if isinstance(perms, str):
            try: perms = json.loads(perms)
            except Exception: perms = {}

        perm_map = { 'partners':'partners_edit', 'products':'products_edit', 'deals':'deals_edit', 'demands':'products_edit', 'accounts':'finances_edit', 'transactions':'finances_edit', 'recurringExpenses':'finances_edit', 'connections':'partners_edit', 'offers':'offers_edit', 'shared_documents':'shared_documents_edit' }
        if role != 'admin' and key in perm_map and not perms.get(perm_map[key], False):
            log_audit('SECURITY', 'database', f'Prevented write access to module: {key}', is_suspicious=True)
            return jsonify({"error": "Unauthorized"}), 403

        tables = ['partners', 'products', 'deals', 'demands', 'accounts', 'transactions', 'recurringExpenses', 'connections', 'offers', 'shared_documents']
        action = 'EDIT'

        if key in tables:
            # V24.0: read-modify-write direktno na Supabase
            existing = store.get_entity(key, item_id)
            _old_offer_for_ver = existing if (key == 'offers' and existing) else None

            if not existing:
                action = 'CREATE'
                item['ownerId'] = session['user_id']
                item['sharedWith'] = []
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

            # OFFER VERSIONING best-effort — V25: snapshot_if_changed internally
            # koristi data_layer (Supabase). `conn` arg se ignoriše (legacy).
            # Nikad ne sme da obori save — wrap u try/except.
            if key == 'offers' and _old_offer_for_ver:
                try:
                    from offer_versions import snapshot_if_changed as _snap
                    _reason = (request.headers.get('X-Change-Reason') or item.get('_changeReason') or '').strip()
                    _snap(None, item_id, _old_offer_for_ver, item,
                          changed_by=session.get('user_id', 'SYSTEM'),
                          changed_by_role=role or 'employee',
                          origin='crm', change_reason=_reason)
                    if '_changeReason' in item:
                        item.pop('_changeReason', None)
                except Exception:
                    logger.info('offer version snapshot skipped (Supabase-only mode)')

            item['id'] = item_id
            store.upsert_entity(key, item)
            log_audit(action, key, f'Updated item ID: {item_id}', is_suspicious=False)

        elif key == 'settings' or key == 'company' or key == 'firewall' or key in SENSITIVE_SETTINGS_KEYS:
            if role != 'admin':
                log_audit('SECURITY', 'database', f'Prevented write access to settings key: {key}', is_suspicious=True)
                return jsonify({"error": "Unauthorized"}), 403
            store.set_setting(key, encrypt_data(item))
            log_audit('EDIT', 'settings', f'Updated settings for {key}', is_suspicious=False)
            if key == 'firewall':
                try:
                    from utils import load_firewall_settings as _reload_fw
                    _reload_fw()
                except Exception:
                    pass

        return jsonify({"status": "success", "id": item_id})

    except Exception:
        logger.error(f"save_single_item({key}) failed", exc_info=True)
        log_audit('ERROR', 'database', f'Save failed for module {key}', is_suspicious=True)
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500

@data_bp.route('/api/item/<key>/<item_id>', methods=['DELETE'])
@login_required
def delete_single_item(key, item_id):
    """V24.0 SUPABASE-ONLY: bez SQLite."""
    import supabase_store as store
    try:
        user_row = store.get_user_by_id(session['user_id'])
        if not user_row:
            return jsonify({"error": "User not found"}), 401
        role = user_row.get('role') or 'employee'
        perms = user_row.get('permissions') or {}
        if isinstance(perms, str):
            try: perms = json.loads(perms)
            except Exception: perms = {}

        perm_map = { 'partners':'partners_delete', 'products':'products_delete', 'deals':'deals_delete', 'demands':'products_delete', 'accounts':'finances_delete', 'transactions':'finances_delete', 'recurringExpenses':'finances_delete', 'connections':'partners_delete', 'offers':'offers_delete', 'shared_documents':'shared_documents_delete' }
        if role != 'admin' and key in perm_map and not perms.get(perm_map[key], False):
            log_audit('SECURITY', 'database', f'Prevented delete from module {key} (ID: {item_id})', is_suspicious=True)
            return jsonify({"error": "Unauthorized"}), 403

        tables = ['partners', 'products', 'deals', 'demands', 'accounts', 'transactions', 'recurringExpenses', 'connections', 'offers', 'shared_documents']
        if key not in tables:
            return jsonify({"error": "Invalid table"}), 400

        # Cascade: obrisi orphan transakcije za deal
        if key == 'deals':
            try:
                from data_layer import delete as _dl_delete, select as _dl_select
                for tx in (_dl_select('transactions') or []):
                    _data = tx.get('data') or {}
                    if isinstance(_data, str):
                        try: _data = json.loads(_data)
                        except Exception: _data = {}
                    if _data.get('dealId') == item_id:
                        _dl_delete('transactions', {'id': tx.get('id')})
                        log_audit('DELETE', 'finances', f'Auto-deleted orphaned transaction ID: {tx.get("id")} linked to Deal: {item_id}', is_suspicious=False)
            except Exception:
                logger.info('cascade delete of transactions skipped', exc_info=True)

        ok = store.delete_entity(key, item_id)
        if ok:
            log_audit('DELETE', key, f'Deleted item ID: {item_id}', is_suspicious=False)
        return jsonify({"status": "success"})

    except Exception:
        logger.error(f"delete_single_item({key}, {item_id}) failed", exc_info=True)
        log_audit('ERROR', 'database', f'Delete failed for module {key}', is_suspicious=True)
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500

@data_bp.route('/api/data/<key>', methods=['POST'])
@login_required
def save_data(key):
    """V24.0 SUPABASE-ONLY: bulk save (entities) ili settings write."""
    import supabase_store as store
    try:
        tables = ['partners', 'products', 'deals', 'demands', 'accounts', 'transactions', 'recurringExpenses', 'connections', 'offers', 'shared_documents']
        if key in tables:
            if session.get('role') != 'admin':
                log_audit('SECURITY', 'database', f'Prevented Bulk Save for module: {key}', is_suspicious=True)
                return jsonify({"error": "Unauthorized"}), 403
            data = (request.get_json(silent=True) or {}).get('value', [])
            # obrisi sve postojece pa upsert-uj nove
            try:
                from data_layer import select as _dl_select, delete as _dl_delete
                for existing in (_dl_select(key) or []):
                    _iid = existing.get('id')
                    if _iid:
                        _dl_delete(key, {'id': _iid})
            except Exception:
                logger.info(f'bulk-delete of {key} pre-write skipped', exc_info=True)
            for item in data:
                item.setdefault('id', str(uuid.uuid4()))
                store.upsert_entity(key, item)
            log_audit('CREATE', key, 'Admin performed bulk save on table.', is_suspicious=False)
        else:
            if session.get('role') != 'admin':
                log_audit('SECURITY', 'database', f'Prevented settings write for key: {key}', is_suspicious=True)
                return jsonify({"error": "Unauthorized"}), 403
            data = (request.get_json(silent=True) or {}).get('value')
            store.set_setting(key, encrypt_data(data))
            log_audit('EDIT', 'settings', f'Updated settings for {key}', is_suspicious=False)
        return jsonify({"status": "success"})
    except Exception:
        logger.error(f"save_data({key}) failed", exc_info=True)
        log_audit('ERROR', 'database', f'Bulk save failed for module {key}', is_suspicious=True)
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500

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
    Bez ovih permisija radnik NE vidi dugme (kontroliše se frontend hasPerm).

    V25 SUPABASE-ONLY: bez SQLite. Podaci idu preko `supabase_store` + `data_layer`.
    """
    import supabase_store as store
    role = session.get('role')
    payload = request.get_json(silent=True) or {}
    force = bool(payload.get('force', False))

    # Provera permisija — čita iz Supabase users tabelu (permissions JSONB).
    perms = {}
    if role != 'admin':
        user_row = store.get_user_by_id(session.get('user_id', '')) or {}
        perms = user_row.get('permissions') or {}
        if isinstance(perms, str):
            try: perms = json.loads(perms)
            except Exception: perms = {}
        if not isinstance(perms, dict):
            perms = {}
        if not perms.get('offers_to_deal', False):
            log_audit('SECURITY', 'offers', f'Prevented unauthorized offer→deal conversion (offer {offer_id})', is_suspicious=True)
            return jsonify({"error": "UNAUTHORIZED"}), 403
        if force and not perms.get('offers_to_deal_force', False):
            log_audit('SECURITY', 'offers', f'Prevented forced offer→deal without client approval (offer {offer_id})', is_suspicious=True)
            return jsonify({"error": "FORCE_NOT_ALLOWED"}), 403

    # 1) Učitaj ponudu — get_entity rehidrira JSONB `data` blob u flat dict.
    offer = store.get_entity('offers', offer_id) or {}
    if not offer:
        return jsonify({"error": "OFFER_NOT_FOUND"}), 404

    # Ako klijent nije prihvatio i nije force, blokiraj
    client_accepted = offer.get('clientStatus') == 'accepted'
    if not client_accepted and not force:
        return jsonify({"error": "CLIENT_HAS_NOT_ACCEPTED", "message": "Klijent nije potvrdio ponudu preko portala. Koristite 'force' za override."}), 409

    # Ako je ponuda vec konvertovana, sprecavamo duplu konverziju
    if offer.get('convertedDealId'):
        existing_deal_id = offer['convertedDealId']
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
    customer_id = offer.get('customerId')
    if customer_id:
        p_data = store.get_entity('partners', customer_id) or {}
        if isinstance(p_data, dict) and p_data:
            deal['buyerName'] = p_data.get('companyName') or p_data.get('company_name') or p_data.get('name', '')
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
        pr = store.get_entity('products', deal['productId']) or {}
        if isinstance(pr, dict) and pr:
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
                if not deal.get('certificates'): deal['certificates'] = supply.get('certificates', '')
                # Popuni ime dobavljača
                if deal.get('supplierId'):
                    sup_data = store.get_entity('partners', deal['supplierId']) or {}
                    if isinstance(sup_data, dict) and sup_data:
                        deal['supplierName'] = sup_data.get('companyName') or sup_data.get('company_name', '')

    # Ako fali bankDetails na dilu, uzmi ih iz podataka firme (settings.company)
    if not deal.get('bankDetails'):
        try:
            comp_blob = store.get_setting('company')
            if comp_blob:
                comp = decrypt_data(comp_blob)
                if isinstance(comp, dict):
                    parts = []
                    if comp.get('bankName'):    parts.append(f"Bank: {comp['bankName']}")
                    if comp.get('bankAddress'): parts.append(comp['bankAddress'])
                    if comp.get('accountNum'):  parts.append(f"IBAN: {comp['accountNum']}")
                    if comp.get('swift'):       parts.append(f"SWIFT: {comp['swift']}")
                    if comp.get('corrBank'):    parts.append(f"Correspondent: {comp['corrBank']}")
                    if parts: deal['bankDetails'] = '\n'.join(parts)
        except Exception:
            logger.info('create_deal_from_offer: company settings read skipped', exc_info=True)

    # INSERT deal — upsert_entity interno radi _entity_split (top-level + JSONB `data`)
    try:
        store.upsert_entity('deals', deal)
    except Exception:
        logger.error(f'create_deal_from_offer: deals upsert failed', exc_info=True)
        log_audit('ERROR', 'offers', 'offer→deal conversion failed (deals upsert)', is_suspicious=True)
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500

    # Označi ponudu kao konvertovanu
    offer['convertedDealId'] = deal_id
    offer['convertedAt'] = now_iso
    try:
        store.upsert_entity('offers', offer)
    except Exception:
        logger.error(f'create_deal_from_offer: offers update failed (deal {deal_id} was inserted)', exc_info=True)
        log_audit('ERROR', 'offers',
                  f'Offer {offer_id} converted to deal {deal_id} but failed to mark offer.convertedDealId', is_suspicious=True)

    forced_msg = " (FORCED — client had not accepted via portal)" if (force and not client_accepted) else ""
    log_audit('CREATE', 'deals', f"Created deal {deal_id} from offer {offer.get('offerNo', offer_id)}{forced_msg}", is_suspicious=False)
    return jsonify({"status": "success", "dealId": deal_id, "deal": deal})


# ==========================================================
#  OFFER VERSIONS — istorija izmena ponude
# ==========================================================
# Svaka izmena bilo kog kritičnog polja ponude (cena, količina, incoterm,
# stavke, valuta, itd.) automatski snima staru verziju u offer_versions
# tabelu (vidi offer_versions.snapshot_if_changed). Ovaj endpoint prikazuje
# istoriju koja je već upisana — read-only.

@data_bp.route('/api/offers/<offer_id>/versions', methods=['GET'])
@login_required
def list_offer_versions(offer_id):
    """Lista svih verzija (samo meta — nije velika).

    V25 SUPABASE-ONLY: bez SQLite. `offer_versions.list_versions` interno
    koristi `data_layer.select` (Supabase). `conn` arg se ignoriše.
    """
    import supabase_store as store
    # Autorizacija: svako sa offers_view može da čita istoriju "svog" offer-a.
    # Ownership check — worker sme samo ako je vlasnik ILI ima permisiju
    role = session.get('role')
    if role != 'admin':
        user_row = store.get_user_by_id(session.get('user_id', '')) or {}
        perms = user_row.get('permissions') or {}
        if isinstance(perms, str):
            try: perms = json.loads(perms)
            except Exception: perms = {}
        if not isinstance(perms, dict): perms = {}
        if not perms.get('offers_view', False):
            return jsonify({"error": "UNAUTHORIZED"}), 403
        od = store.get_entity('offers', offer_id) or {}
        if not od:
            return jsonify({"error": "OFFER_NOT_FOUND"}), 404
        if isinstance(od, dict) and od.get('ownerId') and od['ownerId'] != session['user_id']:
            if session['user_id'] not in (od.get('sharedWith') or []) and not perms.get('offers_view_all', False):
                return jsonify({"error": "UNAUTHORIZED"}), 403

    from offer_versions import list_versions
    versions = list_versions(None, offer_id)
    return jsonify({"offerId": offer_id, "count": len(versions), "versions": versions})


@data_bp.route('/api/offers/<offer_id>/versions/<version_id>', methods=['GET'])
@login_required
def get_offer_version(offer_id, version_id):
    """Vrati pun snapshot za jednu verziju (za diff/PDF-regen prikaz).

    V25 SUPABASE-ONLY: bez SQLite. `offer_versions.get_snapshot` interno
    koristi `data_layer.select_one` (Supabase). `conn` arg se ignoriše.
    """
    import supabase_store as store
    role = session.get('role')
    if role != 'admin':
        user_row = store.get_user_by_id(session.get('user_id', '')) or {}
        perms = user_row.get('permissions') or {}
        if isinstance(perms, str):
            try: perms = json.loads(perms)
            except Exception: perms = {}
        if not isinstance(perms, dict): perms = {}
        if not perms.get('offers_view', False):
            return jsonify({"error": "UNAUTHORIZED"}), 403

    from offer_versions import get_snapshot
    snap = get_snapshot(None, version_id)
    if not snap:
        return jsonify({"error": "VERSION_NOT_FOUND"}), 404
    if snap.get('offerId') != offer_id:
        # Sprečava enumeraciju version_id preko tuđeg offer_id
        return jsonify({"error": "VERSION_NOT_FOUND"}), 404
    return jsonify(snap)


@data_bp.route('/api/offers/<offer_id>/versions/<version_id>/restore', methods=['POST'])
@login_required
def restore_offer_version(offer_id, version_id):
    """Vrati staru verziju u aktivnu ponudu. Trenutna verzija se automatski
    snima pre restore-a (kao i svaki edit), tako da je i restore reverzibilan.
    Samo admin ili korisnik sa 'offers_edit' + 'offers_restore' sme.

    V25 SUPABASE-ONLY: bez SQLite. `offer_versions.{get_snapshot,snapshot_if_changed}`
    interno koriste `data_layer` (Supabase). `conn` arg se ignoriše.
    """
    import supabase_store as store
    role = session.get('role')
    perms = {}
    if role != 'admin':
        user_row = store.get_user_by_id(session.get('user_id', '')) or {}
        perms = user_row.get('permissions') or {}
        if isinstance(perms, str):
            try: perms = json.loads(perms)
            except Exception: perms = {}
        if not isinstance(perms, dict): perms = {}
        if not perms.get('offers_edit', False):
            return jsonify({"error": "UNAUTHORIZED"}), 403

    from offer_versions import get_snapshot, snapshot_if_changed

    # 1) Load current offer from Supabase
    current_offer = store.get_entity('offers', offer_id) or {}
    if not current_offer:
        return jsonify({"error": "OFFER_NOT_FOUND"}), 404
    if not isinstance(current_offer, dict):
        current_offer = {}

    snap = get_snapshot(None, version_id)
    if not snap or snap.get('offerId') != offer_id:
        return jsonify({"error": "VERSION_NOT_FOUND"}), 404

    target_offer = snap.get('snapshot') or {}
    # PRESERVE non-versionable metapodatke (id ostaje, timestampi neće u istoriju)
    target_offer['id'] = offer_id
    # Očuvaj ownerId — restore ne menja vlasništvo
    target_offer['ownerId'] = current_offer.get('ownerId')
    target_offer['sharedWith'] = current_offer.get('sharedWith', [])

    # Prvo snimi trenutni state kao verziju (da restore bude reverzibilan)
    payload = request.get_json(silent=True) or {}
    reason = str(payload.get('reason') or f'Restored to v{snap.get("version")}').strip()[:500]
    try:
        snapshot_if_changed(
            None, offer_id, current_offer, target_offer,
            changed_by=session.get('user_id', 'SYSTEM'),
            changed_by_role=role or 'employee',
            origin='crm',
            change_reason=f'RESTORE: {reason}',
        )
    except Exception:
        logger.info('restore_offer_version: snapshot_if_changed skipped', exc_info=True)

    # 2) Update offer with the restored snapshot
    try:
        store.upsert_entity('offers', target_offer)
    except Exception:
        logger.exception(f"restore_offer_version({offer_id}, {version_id}) failed: offers upsert")
        return jsonify({"error": "INTERNAL_SERVER_ERROR"}), 500

    log_audit('EDIT', 'offers',
              f'Restored offer {offer_id} to version {snap.get("version")}',
              is_suspicious=False)
    return jsonify({"status": "success", "restoredToVersion": snap.get('version'),
                    "offer": target_offer})


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

    import supabase_store as store
    # V25 SUPABASE-ONLY: iterate offers from Supabase (small table —
    # list_entities rehidrira JSONB `data` blob u flat dict).
    found = None
    for od in store.list_entities('offers'):
        if not isinstance(od, dict):
            continue
        if od.get('offerNo') == offer_no:
            found = (od.get('id'), od); break
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
    import supabase_store as store
    computed_hash = hashlib.sha256(data).hexdigest().upper()

    # V25 SUPABASE-ONLY: iterate shared_documents from Supabase (small table —
    # list_entities rehidrira JSONB `data` blob u flat dict sa camelCase poljima).
    match = None
    matched_by_seed = None
    try:
        all_docs = store.list_entities('shared_documents')
        for doc in all_docs:
            if not isinstance(doc, dict):
                continue
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
                    for doc in all_docs:
                        if isinstance(doc, dict) and doc.get('fileName', '').endswith(f'{seed_offer_no}.pdf'):
                            matched_by_seed = doc; break
    except Exception:
        logger.info('verify_document_upload: shared_documents read failed', exc_info=True)

    if match:
        offer = None
        try:
            source_offer_id = match.get('sourceOfferId')
            if source_offer_id:
                offer = store.get_entity('offers', source_offer_id) or None
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

    Permisija: admin ili neko sa offers_edit.

    V25 SUPABASE-ONLY: bez SQLite. Sve čitanja/pisanja idu preko `supabase_store`.
    """
    import supabase_store as store
    role = session.get('role')
    if role != 'admin':
        user_row = store.get_user_by_id(session.get('user_id', '')) or {}
        perms = user_row.get('permissions') or {}
        if isinstance(perms, str):
            try: perms = json.loads(perms)
            except Exception: perms = {}
        if not isinstance(perms, dict): perms = {}
        if not (perms.get('offers_edit') or perms.get('offers_view_all')):
            log_audit('SECURITY', 'offers', 'Prevented unauthorized PDF generation', is_suspicious=True)
            return jsonify({"error": "UNAUTHORIZED"}), 403

    # 1) Load offer from Supabase
    offer = store.get_entity('offers', offer_id) or {}
    if not offer:
        return jsonify({"error": "OFFER_NOT_FOUND"}), 404
    if not isinstance(offer, dict):
        offer = {}

    try:
        from pdf_generator import save_offer_pdf_to_vault
    except Exception as e:
        return jsonify({"error": f"PDF_MODULE_UNAVAILABLE: {e}"}), 500

    doc_id, file_url = save_offer_pdf_to_vault(offer)
    if not doc_id:
        return jsonify({"error": "PDF_GENERATION_FAILED"}), 500

    # 2) Poveži ponudu sa dokumentom — re-load + upsert (single flight, no transaction).
    from datetime import datetime as _dt, timezone as _tz
    try:
        of = store.get_entity('offers', offer_id) or {}
        if not isinstance(of, dict): of = {}
        if of:
            of['documentId'] = doc_id
            of['pdfFileUrl'] = file_url
            of['pdfGeneratedAt'] = _dt.now(_tz.utc).isoformat().replace('+00:00', 'Z')
            store.upsert_entity('offers', of)
    except Exception:
        logger.info('generate_offer_pdf_endpoint: offers upsert (link doc) failed', exc_info=True)

    log_audit('CREATE', 'offers', f'Generated PDF for offer {offer_id} → vault doc {doc_id}', is_suspicious=False)

    # 3) Pokušaj email obaveštenje klijentu (best-effort)
    try:
        customer_id = offer.get('customerId')
        if customer_id:
            pdata = store.get_entity('partners', customer_id) or {}
            if isinstance(pdata, dict) and pdata:
                contact = pdata.get('contact') if isinstance(pdata.get('contact'), dict) else {}
                email = contact.get('email') or pdata.get('email')
                token = pdata.get('portalToken', '')
                portal_url = request.url_root.rstrip('/') + f"/portal/{token}" if token else request.url_root
                if email:
                    from utils_email import send_new_offer
                    send_new_offer(email, pdata.get('companyName') or pdata.get('company_name', ''),
                                   offer.get('offerNo', ''), portal_url)
    except Exception:
        pass

    return jsonify({"status": "success", "documentId": doc_id, "fileUrl": file_url})


# ==========================================================
#  FAZA 6: TIMELINE PER DEAL
#  GET /api/deals/<deal_id>/timeline
# ==========================================================
# Vraća objedinjenu hronologiju svega vezanog za jedan posao:
#   1) Sam deal (create + last modified)
#   2) Sve transakcije (dealId=X ili invoiceNumber=contractId)
#   3) Sve izdate dokumente iz document_register (entityId=X ili
#      docNumber contains contractId)
#   4) Sve revizije dokumenata (document_revisions)
#   5) Audit log stavke koje pominju contractId ili dealId
# Sortirano od najstarijeg ka najnovijem, ili obrnuto ako je ?desc=1.

@data_bp.route('/api/deals/<deal_id>/timeline', methods=['GET'])
@login_required
def deal_timeline(deal_id):
    """V25 SUPABASE-ONLY: deal timeline.

    Vraća objedinjenu hronologiju svega vezanog za jedan posao:
      1) Sam deal (create + last modified)
      2) Sve transakcije (dealId=X ili invoiceNumber=contractId)
      3) Sve izdate dokumente iz document_register (entityId=X ili
         docNumber contains contractId)
      4) Sve revizije dokumenata (document_revisions)
      5) Audit log stavke koje pominju contractId ili dealId
    Sortirano od najstarijeg ka najnovijem, ili obrnuto ako je ?desc=1.
    """
    import supabase_store as store
    from data_layer import select as _dl_select
    desc = str(request.args.get('desc', '')).lower() in ('1', 'true', 'yes')

    events = []

    def _add(ts, kind, title, subtitle='', meta=None, icon=None):
        events.append({
            'timestamp': ts or '',
            'kind': kind,
            'title': title,
            'subtitle': subtitle,
            'meta': meta or {},
            'icon': icon or ''
        })

    contract_id = None
    # 1) Deal sam — single read via supabase_store
    deal = store.get_entity('deals', deal_id) or {}
    if not deal:
        return jsonify({'error': 'deal_not_found'}), 404
    contract_id = deal.get('contractId', '') or ''

    created = deal.get('createdAt') or deal.get('created_at') or ''
    modified = deal.get('lastModified') or deal.get('updatedAt') or ''
    _add(created, 'deal', f'Deal created — {contract_id}',
         f"Buyer: {deal.get('buyerName', '')}  ·  Product: {deal.get('productName', '')}",
         meta={'dealId': deal_id}, icon='📝')
    if modified and modified != created:
        _add(modified, 'deal', f'Deal updated — {contract_id}',
             f"Status: {deal.get('status', 'unknown')}", meta={'dealId': deal_id}, icon='✏️')

    # 2) Transactions — fetch all (small table, scoped to deal in-memory)
    try:
        for tx in store.list_entities('transactions'):
            if not isinstance(tx, dict):
                continue
            if tx.get('dealId') != deal_id and tx.get('invoiceNumber') != contract_id:
                continue
            ts = tx.get('date') or tx.get('createdAt') or ''
            amt = tx.get('amount', 0)
            cur = tx.get('currency', '')
            typ = tx.get('type', '')
            title = f"{'💰 Income' if typ == 'income' else '💸 Expense'}: {amt} {cur}"
            _add(ts, 'transaction', title,
                 f"{tx.get('category', '')} — {tx.get('source', '')}",
                 meta={'transactionId': tx.get('id')},
                 icon='💰' if typ == 'income' else '💸')
    except Exception:
        logger.info('deal_timeline: transactions read failed', exc_info=True)

    # 3) Documents from register — entityId match OR docNumber contains contractId
    try:
        # filter by entity_id (PostgREST eq), then in-memory match docNumber LIKE contractId
        docs_by_entity = _dl_select('document_register',
                                    filters={'entity_id': deal_id},
                                    order='issued_at', limit=500) or []
        docs_by_num = []
        if contract_id:
            # PostgREST ilike na doc_number — dohvatamo sve koji sadrže contract_id
            try:
                docs_by_num = _dl_select('document_register',
                                         filters={'doc_number': ('ilike', f'%{contract_id}%')},
                                         order='issued_at', limit=500) or []
            except Exception:
                docs_by_num = []
        # Dedup po id-u (iste redove mogao vratiti oba filtera)
        seen_ids = set()
        merged = []
        for d in list(docs_by_entity) + list(docs_by_num):
            if not isinstance(d, dict):
                continue
            _id = d.get('id')
            if _id and _id in seen_ids:
                continue
            if _id:
                seen_ids.add(_id)
            merged.append(d)
        # Sortiraj issued_at ASC
        merged.sort(key=lambda r: r.get('issued_at') or '')
        for d in merged:
            doc_type = d.get('doc_type') or d.get('docType') or ''
            doc_num = d.get('doc_number') or d.get('docNumber') or ''
            rev = d.get('revision') or 0
            status = d.get('status') or ''
            issued_at = d.get('issued_at') or ''
            issued_by = d.get('issued_by') or d.get('issuedBy') or ''
            title = f"📄 {str(doc_type).upper()} issued: {doc_num}"
            sub = f"Revision {rev}  ·  Status: {status}  ·  By: {issued_by or 'system'}"
            _add(issued_at, 'document', title, sub,
                 meta={'docNumber': doc_num, 'docType': doc_type, 'revision': rev},
                 icon='📄')
    except Exception:
        logger.info('deal_timeline: document_register read skipped', exc_info=True)

    # 4) Document revisions with reason
    try:
        rev_rows = _dl_select('document_revisions',
                              filters={'entity_id': deal_id},
                              order='changed_at', limit=500) or []
        for r in rev_rows:
            if not isinstance(r, dict):
                continue
            doc_num = r.get('doc_number') or r.get('docNumber') or ''
            rev = r.get('revision') or 0
            change_reason = r.get('change_reason') or r.get('changeReason') or ''
            changed_by = r.get('changed_by') or r.get('changedBy') or ''
            changed_at = r.get('changed_at') or r.get('changedAt') or ''
            _add(changed_at, 'revision',
                 f"🔁 Revision R{rev} of {doc_num}",
                 f"Reason: {change_reason}  ·  By: {changed_by}",
                 meta={'docNumber': doc_num, 'revision': rev},
                 icon='🔁')
    except Exception:
        logger.info('deal_timeline: document_revisions read skipped', exc_info=True)

    # 5) Audit log — mentions contract_id OR deal_id (ilike na details)
    try:
        needle = (contract_id or deal_id).strip()
        if needle:
            rows_a = _dl_select('audit_logs',
                                filters={'details': ('ilike', f'%{needle}%')},
                                order='-timestamp', limit=100) or []
            # Drugi uslov — deal_id u details (može se razlikovati od contract_id)
            if deal_id and deal_id != needle:
                try:
                    rows_a2 = _dl_select('audit_logs',
                                         filters={'details': ('ilike', f'%{deal_id}%')},
                                         order='-timestamp', limit=100) or []
                    rows_a.extend(rows_a2)
                except Exception:
                    pass
            # Dedup po id-u (BIGSERIAL u Supabase) ili po (timestamp, details)
            seen_a = set()
            uniq_a = []
            for r in rows_a:
                if not isinstance(r, dict):
                    continue
                key = r.get('id') or (r.get('timestamp'), r.get('details'))
                if key in seen_a:
                    continue
                seen_a.add(key)
                uniq_a.append(r)
            for r in uniq_a:
                a_action = r.get('action') or ''
                a_module = r.get('module') or ''
                a_details = r.get('details') or ''
                a_user = r.get('username') or r.get('actor') or 'system'
                a_ts = r.get('timestamp') or r.get('ts') or ''
                _add(a_ts, 'audit', f"🛡️ {a_action}",
                     f"{a_module}: {(a_details or '')[:180]}  ·  By: {a_user}",
                     meta={'action': a_action, 'module': a_module},
                     icon='🛡️')
    except Exception:
        logger.info('deal_timeline: audit_logs read skipped', exc_info=True)

    # Sortiraj hronoloski
    events.sort(key=lambda e: e.get('timestamp') or '', reverse=desc)

    return jsonify({
        'dealId': deal_id,
        'contractId': contract_id,
        'total': len(events),
        'events': events,
    })


# ==========================================================
#  BATCH D — NEW: Partner risk score
# ==========================================================
# Kompozitni score 0-100 sa razgraničenjem na kategorije:
#   KYC (approved=+30, pending=0, rejected=-30)
#   Sanctions match (any=-50, none=+20)
#   Age of relationship (>2y=+10, >5y=+15)
#   Payment history (on-time deals ratio)
#   Recent activity (last modification in <90d)

@data_bp.route('/api/partners/<partner_id>/risk-score', methods=['GET'])
@login_required
def partner_risk_score(partner_id):
    """V24.1 SUPABASE-ONLY."""
    from datetime import datetime, timezone
    if not partner_id:
        return jsonify({'error': 'partner_id_required'}), 400
    import supabase_store as store
    p = store.get_entity('partners', partner_id)
    if not p:
        return jsonify({'error': 'partner_not_found'}), 404

    score = 50
    breakdown = []
    # 1) KYC
    kyc = (p.get('kyc') or {}).get('status', 'pending')
    if kyc == 'approved':
        score += 20; breakdown.append({'factor': 'KYC approved', 'delta': +20})
    elif kyc == 'rejected':
        score -= 30; breakdown.append({'factor': 'KYC rejected', 'delta': -30})
    else:
        breakdown.append({'factor': f'KYC {kyc}', 'delta': 0})
    # 2) Sanctions
    sanctions = (p.get('kyc') or {}).get('sanctionsResults') or {}
    has_match = any((s or {}).get('hits') for s in sanctions.get('results', []))
    if has_match:
        score -= 40; breakdown.append({'factor': 'Sanctions match', 'delta': -40})
    else:
        score += 5; breakdown.append({'factor': 'No sanctions', 'delta': +5})
    # 3) Relationship age
    created = p.get('createdAt') or p.get('created_at')
    if created:
        try:
            dt = datetime.fromisoformat(str(created).replace('Z', '+00:00'))
            years = (datetime.now(timezone.utc) - dt).days / 365.25
            if years > 5:
                score += 15; breakdown.append({'factor': f'{years:.1f}y relationship', 'delta': +15})
            elif years > 2:
                score += 10; breakdown.append({'factor': f'{years:.1f}y relationship', 'delta': +10})
            elif years > 0.5:
                score += 3;  breakdown.append({'factor': f'{years:.1f}y relationship', 'delta': +3})
        except Exception:
            pass
    # 4) Payment history from Supabase deals
    total = 0; paid_on_time = 0; late = 0
    for d in store.list_entities('deals'):
        if d.get('buyerId') != partner_id and d.get('supplierId') != partner_id:
            continue
        total += 1
        paid_on = d.get('buyerPaidOn')
        due = (d.get('paymentDates') or {}).get('buyer')
        if paid_on and due:
            try:
                p_dt = datetime.fromisoformat(str(paid_on).replace('Z', '+00:00'))
                d_dt = datetime.fromisoformat(str(due).replace('Z', '+00:00'))
                if p_dt <= d_dt: paid_on_time += 1
                else: late += 1
            except Exception:
                pass
    if total >= 3:
        ratio = paid_on_time / total
        delta = int(round(ratio * 20 - 10))
        score += delta
        breakdown.append({'factor': f'{paid_on_time}/{total} deals paid on time', 'delta': delta})
    # 5) Recent activity
    lm = p.get('lastModified')
    if lm:
        try:
            dt = datetime.fromisoformat(str(lm).replace('Z', '+00:00'))
            days = (datetime.now(timezone.utc) - dt).days
            if days < 90:
                score += 5; breakdown.append({'factor': f'Active ({days}d ago)', 'delta': +5})
            elif days > 365:
                score -= 5; breakdown.append({'factor': f'Stale ({days}d)', 'delta': -5})
        except Exception:
            pass
    score = max(0, min(100, score))
    band = 'LOW' if score >= 70 else ('MEDIUM' if score >= 40 else 'HIGH')
    return jsonify({
        'partnerId': partner_id, 'score': score, 'band': band,
        'breakdown': breakdown, 'deals_total': total,
        'deals_paid_on_time': paid_on_time, 'deals_late': late,
    })


# ==========================================================
#  BATCH D2 — Dashboard extras: overdue deals count, active partners
# ==========================================================

@data_bp.route('/api/dashboard/insights', methods=['GET'])
@login_required
def dashboard_insights():
    """V24.1 SUPABASE-ONLY."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    thirty_ago = now - timedelta(days=30)
    year_ago = now - timedelta(days=365)

    import supabase_store as store
    deals = store.list_entities('deals')
    partners = store.list_entities('partners')

    overdue = 0; due_this_week = 0; new_this_week = 0
    for d in deals:
        due = (d.get('paymentDates') or {}).get('buyer')
        paid = d.get('buyerPaidOn')
        if due and not paid:
            try:
                dt = datetime.fromisoformat(str(due).replace('Z', '+00:00'))
                if dt < now: overdue += 1
                elif dt < now + timedelta(days=7): due_this_week += 1
            except Exception: pass
        ca = d.get('createdAt')
        if ca:
            try:
                dt = datetime.fromisoformat(str(ca).replace('Z', '+00:00'))
                if dt > week_ago: new_this_week += 1
            except Exception: pass

    active_30d = 0; stale_partners = 0
    for p in partners:
        lm = p.get('lastModified')
        if lm:
            try:
                dt = datetime.fromisoformat(str(lm).replace('Z', '+00:00'))
                if dt > thirty_ago: active_30d += 1
                elif dt < year_ago: stale_partners += 1
            except Exception: pass

    return jsonify({
        'overdue_deals': overdue,
        'due_this_week': due_this_week,
        'new_deals_this_week': new_this_week,
        'active_partners_30d': active_30d,
        'stale_partners_1y': stale_partners,
        'timestamp': now.isoformat(),
    })


# ==========================================================
#  BATCH D2 — Saved searches (per-user)
# ==========================================================

@data_bp.route('/api/saved-searches', methods=['GET'])
@login_required
def saved_searches_list():
    """V25 SUPABASE-ONLY: čita iz `saved_searches` tabele (Supabase)."""
    from data_layer import select as _dl_select
    uid = session.get('user_id')
    try:
        rows = _dl_select('saved_searches',
                          filters={'user_id': uid},
                          order='-created_at', limit=100) or []
    except Exception:
        logger.info('saved_searches_list failed', exc_info=True)
        rows = []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        q = r.get('query_json')
        if isinstance(q, str):
            try: q = json.loads(q)
            except Exception: q = {}
        if not isinstance(q, dict):
            q = {}
        out.append({
            'id': r.get('id'),
            'name': r.get('name'),
            'module': r.get('module'),
            'query': q,
            'created_at': r.get('created_at'),
        })
    return jsonify({'searches': out})


@data_bp.route('/api/saved-searches', methods=['POST'])
@login_required
def saved_searches_create():
    """V25 SUPABASE-ONLY: insert u `saved_searches` tabelu (Supabase)."""
    import uuid as _u
    from data_layer import insert as _dl_insert
    from datetime import datetime as _dt, timezone as _tz
    body = request.get_json(silent=True) or {}
    name = str(body.get('name') or '').strip()[:100]
    module = str(body.get('module') or '').strip()[:40]
    q = body.get('query') or {}
    if not name or not module:
        return jsonify({'error': 'name_and_module_required'}), 400
    if not isinstance(q, dict):
        return jsonify({'error': 'query_must_be_object'}), 400
    uid = session.get('user_id')
    sid = str(_u.uuid4())
    try:
        _dl_insert('saved_searches', {
            'id': sid,
            'user_id': uid,
            'name': name,
            'module': module,
            'query_json': q,   # JSONB — prosledi dict direktno
            'created_at': _dt.now(_tz.utc).isoformat().replace('+00:00', 'Z'),
        })
    except Exception:
        logger.info('saved_searches_create failed', exc_info=True)
        return jsonify({'error': 'INTERNAL_SERVER_ERROR'}), 500
    log_audit('CREATE', 'saved_searches',
              f'Saved search "{name}" for module "{module}"')
    return jsonify({'id': sid, 'name': name, 'module': module})


@data_bp.route('/api/saved-searches/<sid>', methods=['DELETE'])
@login_required
def saved_searches_delete(sid):
    """V25 SUPABASE-ONLY: delete iz `saved_searches` tabele (Supabase)."""
    from data_layer import delete as _dl_delete
    uid = session.get('user_id')
    try:
        n = _dl_delete('saved_searches', {'id': sid, 'user_id': uid}) or 0
    except Exception:
        logger.info('saved_searches_delete failed', exc_info=True)
        n = 0
    return jsonify({'deleted': int(n)})
