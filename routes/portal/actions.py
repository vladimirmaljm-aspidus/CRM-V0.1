"""V25 SUPABASE-ONLY — Portal actions (KYC, RFQ, orders, documents, profile changes).

Sve DB operacije idu kroz `data_layer` facade ili `supabase_store` helper.
Nema vise `sqlite3.connect(...)` poziva — podaci prezive PythonAnywhere
redeploy jer zive u Supabase Postgres-u.

Interfejs (route path, method, JSON shape, status code) je identican prethodnoj
SQLite verziji, tako da frontend JS ne mora nista da se menja.
"""
import os
import json
import logging
import secrets
import uuid
from datetime import datetime, timezone

from werkzeug.utils import secure_filename
from flask import request, jsonify, abort, send_from_directory, current_app, session
from config import PORTAL_UPLOAD_FOLDER, ALLOWED_EXTENSIONS
from utils import (log_audit, login_required, encrypt_data, decrypt_data,
                   is_safe_file_content, rate_limit)
from bank_validation import validate_iban, validate_bic
import re as _re_bv

import supabase_store as store
from data_layer import (select as _dl_select, select_one as _dl_select_one,
                        insert as _dl_insert, update as _dl_update,
                        upsert as _dl_upsert, delete as _dl_delete,
                        count as _dl_count)
from . import (portal_bp, verify_portal_session,
               log_portal_activity, is_partner_premium)

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


# ==========================================================================
#  Helpers — partner lookup by portal_token, offer snapshot, etc.
# ==========================================================================

def _find_partner_by_token(token, enforce_active=True):
    """Lokalna Supabase verzija `find_partner_by_token` iz `__init__.py`.

    Original u `__init__.py` prima `cursor` (SQLite) i iterira kroz sve
    partnere da bi matchovao `portalToken` iz JSONB-a. Ova verzija koristi
    top-level `portal_token` kolonu (dodata u Faza 1) — jedan SELECT, nema
    skeniranja. Vraća (partner_id, partner_dict) ili (None, None).

    Partner dict je "merged" — sadrži i top-level kolone (snake_case) i
    JSONB `data` payload (camelCase, ono što frontend čita).
    """
    if not token:
        return None, None
    try:
        row = _dl_select_one('partners', {'portal_token': token})
    except Exception as _e:
        logger.info(f'_find_partner_by_token failed: {_e}')
        return None, None
    if not row:
        return None, None
    partner = store._entity_join(row) if hasattr(store, '_entity_join') else dict(row)
    if enforce_active:
        active = partner.get('isPortalActive', partner.get('is_portal_active', True))
        if active is False:
            return None, None
    # Backward-compat: osiguraj da `isPremium` postoji i u JSONB obliku
    # (top-level `is_premium` kolona moze da postoji bez JSONB ekvivalenta).
    if 'isPremium' not in partner and 'is_premium' in partner:
        partner['isPremium'] = partner['is_premium']
    pid = partner.get('id') or row.get('id')
    return pid, partner


def _portal_offer_snapshot(offer_id, old_offer, new_offer, changed_by,
                            change_reason=''):
    """Best-effort snapshot stare verzije ponude u `offer_versions` tabelu.

    Zamena za `offer_versions.snapshot_if_changed` (koji prima sqlite3
    Connection i koristi stara camelCase imena kolona). Ova verzija radi
    direktno preko data_layer-a sa snake_case Supabase šemom.
    """
    try:
        from offer_versions import _diff_fields
        changed = _diff_fields(old_offer, new_offer)
        if not changed:
            return None
        existing = _dl_select('offer_versions',
                              filters={'offer_id': offer_id},
                              columns='version',
                              order='-version', limit=1) or []
        next_version = (int((existing[0] or {}).get('version', 0)) + 1) if existing else 1
        ver_id = str(uuid.uuid4())
        _dl_insert('offer_versions', {
            'id': ver_id,
            'offer_id': offer_id,
            'version': next_version,
            'snapshot': old_offer or {},   # JSONB — prosledi dict direktno
            'changed_fields': ','.join(changed)[:500],
            'change_reason': (change_reason or '').strip()[:500],
            'changed_by': changed_by,
            'changed_by_role': 'partner',
            'changed_at': _iso_now(),
            'origin': 'portal',
        })
        return ver_id
    except Exception as _e:
        logger.info(f'portal offer snapshot skipped: {_e}')
        return None


def _partner_name_map():
    """Vraca dict {partner_id: companyName} — za enrichment admin listi."""
    out = {}
    try:
        for p in store.list_entities('partners'):
            pid = p.get('id')
            if pid:
                out[pid] = p.get('companyName') or p.get('company_name') or 'Unknown'
    except Exception as _e:
        logger.info(f'_partner_name_map failed: {_e}')
    return out


def _sanitize_persons(raw_list):
    """Prima listu direktora/UBO objekata iz portal KYC form-a i vrati
    strogo sanitizovanu verziju: max 10 osoba, po osobi max 10 file url-ova,
    svaka url max 250 karaktera. Očekivan input format iz frontend-a:
      [{name, passport, nationality, files: [urls...]}, ...]
    Ako nešto nije lista/dict — tiho preskačemo (KYC ostaje validan bez toga)."""
    if not isinstance(raw_list, list):
        return []
    out = []
    for person in raw_list[:10]:
        if not isinstance(person, dict):
            continue
        clean = {
            'name': str(person.get('name', ''))[:200].strip(),
            'passport': str(person.get('passport', ''))[:100].strip(),
            'nationality': str(person.get('nationality', ''))[:100].strip(),
        }
        if not clean['name'] and not clean['passport']:
            continue
        files = person.get('files') or []
        if isinstance(files, list):
            clean_files = []
            for f in files[:10]:
                if isinstance(f, str) and len(f) <= 250 and f.startswith('/portal_uploads/'):
                    clean_files.append(f)
            clean['files'] = clean_files
        else:
            clean['files'] = []
        out.append(clean)
    return out


def _load_user_permissions():
    """Vraća (user_dict_or_None, permissions_dict_or_None) za session user-a.
    Permissions dolaze kao JSONB iz `users.permissions` kolone — posle
    `supabase_store._coerce_user_out` to je vec dict."""
    uid = session.get('user_id')
    if not uid:
        return None, None
    user_row = store.get_user_by_id(uid)
    if not user_row:
        return None, None
    perms = user_row.get('permissions') or {}
    if isinstance(perms, str):
        try: perms = json.loads(perms)
        except Exception: perms = {}
    return user_row, perms


def require_portal_admin():
    """Provera pristupa admin rutama portala (KYC review, products approval...).
    Dozvoljeno: admin rola ili eksplicitna 'partners_edit' permisija.
    Vraća None ako je pristup dozvoljen, ili Flask response sa 401/403."""
    if 'user_id' not in session:
        return jsonify({"error": "UNAUTHORIZED"}), 401
    if session.get('role') == 'admin':
        return None
    _, perms = _load_user_permissions()
    if perms and perms.get('partners_edit', False):
        return None
    log_audit('SECURITY', 'portal', 'Prevented unauthorized access to portal admin endpoint', is_suspicious=True)
    return jsonify({"error": "Unauthorized"}), 403


def verify_portal_auth(token, auth_header):
    """Provera portal sesije (constant-time + TTL). Delegira na centralizovanu logiku."""
    return verify_portal_session(token, auth_header)


def require_partner_view():
    """KYC/portal dokumenti (pasoši, bankovni podaci, UBO...) su compliance-osetljivi.
    Sme ih preuzeti admin ili korisnik sa nekom 'partners' view/edit permisijom.
    Vraća None ako je dozvoljeno, ili Flask response ako nije."""
    if 'user_id' not in session:
        return jsonify({"error": "UNAUTHORIZED"}), 401
    if session.get('role') == 'admin':
        return None
    _, perms = _load_user_permissions()
    allowed_keys = ('partners_view_all', 'partners_view', 'partners_view_own', 'partners_edit')
    if perms and any(perms.get(k, False) for k in allowed_keys):
        return None
    log_audit('SECURITY', 'portal', 'Prevented unauthorized KYC/portal document download', is_suspicious=True)
    return jsonify({"error": "Unauthorized"}), 403


# ==========================================================================
#  PORTAL CATALOG + PRODUCT SUBMIT (klijent predlaže robu)
# ==========================================================================

@portal_bp.route('/api/portal/products/submit/<token>', methods=['POST'])
@rate_limit(max_per_minute=20, key='portal_product_submit')
def submit_portal_product(token):
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_auth(token, auth_header):
        abort(401)

    partner_id, partner = _find_partner_by_token(token, enforce_active=True)
    if not partner_id:
        abort(403)
    company_name = partner.get('companyName', 'Unknown')

    prod_data = request.json or {}

    # OWNERSHIP: 'own' — klijentova roba (standardna polja); 'third_party' — roba
    # dobavljača/preprodavca (obavezno sourceCompany.name + taxId; admin ovo
    # koristi da po odobrenju kreira novog partnera vezanog za klijenta koji ga
    # je uveo → introducedByPartnerId).
    ownership = str(prod_data.get('ownership', 'own')).strip().lower()
    if ownership not in ('own', 'third_party'):
        ownership = 'own'
    prod_data['ownership'] = ownership
    if ownership == 'third_party':
        src = prod_data.get('sourceCompany') or {}
        if not isinstance(src, dict): src = {}
        name = str(src.get('name', '')).strip()[:200]
        tax_id = str(src.get('taxId', '')).strip()[:80]
        if not name or not tax_id:
            return jsonify({"error": "SOURCE_COMPANY_REQUIRED",
                            "message": "Third-party goods require source company name and tax ID."}), 400
        prod_data['sourceCompany'] = {
            'name': name, 'taxId': tax_id,
            'country': str(src.get('country', '')).strip()[:100],
            'city': str(src.get('city', '')).strip()[:100],
            'address': str(src.get('address', '')).strip()[:250],
            'website': str(src.get('website', '')).strip()[:200],
            'email': str(src.get('email', '')).strip()[:200],
            'phone': str(src.get('phone', '')).strip()[:80],
            'relationship': str(src.get('relationship', '')).strip()[:100],
            'notes': str(src.get('notes', '')).strip()[:600],
        }
    else:
        prod_data['sourceCompany'] = None
    prod_data['submittedByPartnerId'] = partner_id
    prod_data['submittedByPartnerName'] = company_name

    client_id = prod_data.get('id')
    product_id = None
    if client_id:
        # Proveri da li postoji i da li pripada ovom partneru
        existing = _dl_select_one('portal_products', {'id': client_id}) or {}
        if existing and existing.get('partner_id') == partner_id:
            product_id = client_id
    if not product_id:
        product_id = str(uuid.uuid4())
    prod_data['id'] = product_id

    created_at = _iso_now()
    _dl_upsert('portal_products', {
        'id': product_id,
        'partner_id': partner_id,
        'data': prod_data,           # JSONB — prosledi dict direktno
        'status': 'pending',
        'created_at': created_at,
    }, on_conflict='id')

    log_audit('EDIT', 'portal', f"Partner '{company_name}' submitted product: {prod_data.get('name')} (ownership={ownership})", is_suspicious=False)
    log_portal_activity(partner_id, 'PRODUCT_SUBMIT', f"Submitted product: {prod_data.get('name')} (ownership={ownership})")
    return jsonify({"status": "success", "message": "Product securely staged for review", "id": product_id})


@portal_bp.route('/api/portal/catalog/<token>', methods=['GET'])
@rate_limit(max_per_minute=60, key='portal_catalog')
def portal_catalog(token):
    """Vraća listu proizvoda vidljivih ovom klijentu — BEZ CENA, bez dobavljača.
    Vidljivost se kontroliše preko partner.portalVisibleProducts (lista productId).
    Ako partner nema listu (ili je prazna), i partner ima 'catalog' u
    portalPermissions vraćamo katalog, ali samo naziv/kategorija/HS/spec."""
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_auth(token, auth_header):
        abort(401)

    partner_id, partner = _find_partner_by_token(token, enforce_active=True)
    if not partner_id:
        abort(403)

    visible_ids = partner.get('portalVisibleProducts')
    if not isinstance(visible_ids, list):
        visible_ids = []

    catalog = []
    try:
        rows = _dl_select('products', limit=5000) or []
    except Exception as _e:
        logger.info(f'portal_catalog products load failed: {_e}')
        rows = []
    for row in rows:
        # `products` je entitet — spojiti top-level + JSONB
        pd = store._entity_join(row) if hasattr(store, '_entity_join') else dict(row)
        if not isinstance(pd, dict):
            continue
        pid = pd.get('id') or row.get('id')
        if pid not in visible_ids:
            continue
        supply = pd.get('supplyOffers') or []
        origins = sorted({str(so.get('country', '')).strip() for so in supply if so.get('country')})
        certificates = sorted({c.strip() for so in supply for c in str(so.get('certificates', '')).split(',') if c.strip()})
        catalog.append({
            'id': pid,
            'name': pd.get('name', ''),
            'category': pd.get('category', ''),
            'hsCode': pd.get('hsCode', ''),
            'brand': pd.get('brand', ''),
            'shortDescription': pd.get('shortDescription') or (pd.get('detailedSpec') or '')[:400],
            'origins': origins,
            'certificates': certificates,
            'packaging': pd.get('packaging', ''),
            'unit': pd.get('unit') or (supply[0].get('unit') if supply else ''),
            'imageUrl': pd.get('imageUrl', ''),
        })
    catalog.sort(key=lambda x: (x.get('name') or '').lower())
    return jsonify({"products": catalog, "count": len(catalog)})


# Poznati Incoterms 2020 skup + kategorije koje koristimo za automation upozorenja
_INCOTERMS_ANY = {'EXW', 'FCA', 'CPT', 'CIP', 'DAP', 'DPU', 'DDP'}
_INCOTERMS_SEA = {'FAS', 'FOB', 'CFR', 'CIF'}
_INCOTERMS_ALL = _INCOTERMS_ANY | _INCOTERMS_SEA
_INCOTERMS_BUYER_ARRANGES = {'EXW', 'FCA', 'FAS', 'FOB'}
_INCOTERMS_SELLER_INSURES = {'CIF', 'CIP'}

_PAYMENT_TERMS_ALLOWED = {
    'TT_100_advance', 'TT_50_50', 'TT_30_70', 'TT_30_days', 'TT_60_days',
    'LC_sight', 'LC_30_days', 'LC_60_days', 'LC_90_days',
    'CAD', 'DA', 'Escrow', 'OpenAccount', 'Other'
}


def _analyze_incoterm_mismatch(product_data, requested_incoterm):
    """Pretvara supplyOffers.incoterm listu u set i poredi sa traženim.
    Vraća listu čitljivih automation hint-ova koji idu u CRM (demand.autoHints)."""
    hints = []
    if not isinstance(product_data, dict):
        return hints
    supply_offers = product_data.get('supplyOffers') or []
    supplier_incoterms = {str(so.get('incoterm', '')).upper() for so in supply_offers if so.get('incoterm')}
    supplier_countries = {str(so.get('country', '')).strip() for so in supply_offers if so.get('country')}

    req = (requested_incoterm or '').upper()
    if req and req in _INCOTERMS_ALL and supplier_incoterms and req not in supplier_incoterms:
        hints.append(f"INCOTERM_CONVERSION: Client requests {req} but supplier offers only "
                     f"{sorted(supplier_incoterms)}. Additional lead time required for freight"
                     f"{'+insurance' if req in _INCOTERMS_SELLER_INSURES else ''} calculation.")

    if req in _INCOTERMS_SEA and supplier_incoterms and not (supplier_incoterms & _INCOTERMS_SEA):
        hints.append(f"MODE_MISMATCH: Client asks for sea-mode {req} but supplier offers are "
                     f"road/multi-modal only. Consider whether sea freight is feasible from origin.")

    if supplier_countries:
        hints.append(f"KNOWN_ORIGINS: Product currently sourced from {sorted(supplier_countries)}.")

    return hints


@portal_bp.route('/api/portal/quote_request/<token>', methods=['POST'])
@rate_limit(max_per_minute=10, key='portal_quote_request')
def portal_quote_request(token):
    """Klijent klikne 'Request Quote' iz kataloga. Prihvata pun payload:
    Incoterm, destination, payment terms, banka, logistički agent, notes,
    optional end-buyer (ako klijent traži za drugu firmu).

    Automation: computes Incoterm mismatch (npr. klijent CIF vs supplier EXW),
    upisuje autoHints u demand kako bi admin u CRM-u odmah video šta treba
    dodatno da izračuna (freight, insurance, lead time)."""
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_auth(token, auth_header):
        abort(401)

    partner_id, partner = _find_partner_by_token(token, enforce_active=True)
    if not partner_id:
        abort(403)

    data = request.json or {}
    product_id = str(data.get('productId') or '').strip()
    if not product_id:
        return jsonify({"error": "PRODUCT_REQUIRED"}), 400

    visible = partner.get('portalVisibleProducts') or []
    if product_id not in visible:
        log_audit('SECURITY', 'portal', f'Blocked quote request for hidden product {product_id} by partner {partner_id}', is_suspicious=True)
        return jsonify({"error": "PRODUCT_NOT_VISIBLE"}), 403

    def _safe_num(v, minv=0.0, maxv=1e12):
        try:
            n = float(v)
            if n != n or n < minv or n > maxv: return 0.0
            return round(n, 4)
        except (TypeError, ValueError):
            return 0.0

    incoterm = str(data.get('incoterm', '')).upper().strip()
    if incoterm and incoterm not in _INCOTERMS_ALL:
        return jsonify({"error": "INVALID_INCOTERM"}), 400

    payment_terms = str(data.get('paymentTerms', '')).strip()
    if payment_terms and payment_terms not in _PAYMENT_TERMS_ALLOWED:
        return jsonify({"error": "INVALID_PAYMENT_TERMS"}), 400

    requestor = str(data.get('requestor', 'self')).lower()
    if requestor not in ('self', 'third_party'):
        requestor = 'self'

    end_buyer = None
    if requestor == 'third_party':
        eb = data.get('endBuyer') or {}
        if not isinstance(eb, dict) or not str(eb.get('companyName', '')).strip():
            return jsonify({"error": "END_BUYER_REQUIRED"}), 400
        end_buyer = {
            'companyName': str(eb.get('companyName', '')).strip()[:200],
            'taxId': str(eb.get('taxId', '')).strip()[:80],
            'country': str(eb.get('country', '')).strip()[:100],
            'email': str(eb.get('email', '')).strip()[:200],
            'phone': str(eb.get('phone', '')).strip()[:80],
        }

    # Uzmi proizvod iz baze radi mismatch analize + prikaza imena
    product_row = _dl_select_one('products', {'id': product_id}) or {}
    product_data = store._entity_join(product_row) if hasattr(store, '_entity_join') else dict(product_row)
    prod_name = product_data.get('name', 'Unknown Product') if isinstance(product_data, dict) else 'Unknown Product'

    auto_hints = _analyze_incoterm_mismatch(product_data, incoterm)

    demand_id = str(uuid.uuid4())
    now_iso = _iso_now()
    demand_obj = {
        "id": demand_id,
        "customerId": partner_id, "buyerId": partner_id, "buyer_id": partner_id,
        "productId": product_id, "isNewProduct": False,
        "productName": prod_name,
        "quantity": _safe_num(data.get("quantity")),
        "targetPrice": _safe_num(data.get("targetPrice")),
        "currency": str(data.get("currency", "USD")).strip()[:10],
        "neededBy": str(data.get("neededBy", "")).strip()[:20],
        "incoterm": incoterm,
        "destination": str(data.get("destination", "")).strip()[:250],
        "paymentTerms": payment_terms,
        "buyerBank": str(data.get("buyerBank", "")).strip()[:150],
        "logisticsAgent": str(data.get("logisticsAgent", "")).strip()[:200],
        "logisticsAgentContact": str(data.get("logisticsAgentContact", "")).strip()[:200],
        "notes": str(data.get("notes", "")).strip()[:1500],
        "requestor": requestor,
        "endBuyer": end_buyer,
        "autoHints": auto_hints,
        "date": now_iso, "createdAt": now_iso,
        "status": "pending", "source": "B2B Portal Catalog"
    }
    _dl_insert('demands', {
        'id': demand_id,
        'data': demand_obj,    # JSONB
        'buyer_id': partner_id,
        'created_at': now_iso,
    })

    log_audit('CREATE', 'demands',
              f"Portal quote request from partner {partner_id} for '{prod_name}' "
              f"(qty {demand_obj['quantity']}, incoterm {incoterm}, requestor {requestor}, "
              f"hints: {len(auto_hints)})",
              is_suspicious=False)
    log_portal_activity(partner_id, 'QUOTE_REQUEST',
                        f"Quote for '{prod_name}' qty {demand_obj['quantity']} {incoterm or ''} → {demand_obj['destination'][:60]}")

    # Obavesti admina emailom ako je konfigurisan (best-effort)
    try:
        from utils_email import _send_smtp
        hints_txt = "\n".join(f"  · {h}" for h in auto_hints) if auto_hints else "  (no automation flags)"
        body = (f"New quote request received via B2B Portal Catalog.\n\n"
                f"Client:      {partner.get('companyName', partner_id)}\n"
                f"Product:     {prod_name}\n"
                f"Quantity:    {demand_obj['quantity']}\n"
                f"Incoterm:    {incoterm or '(not specified)'}\n"
                f"Destination: {demand_obj['destination']}\n"
                f"Payment:     {payment_terms or '(not specified)'}\n"
                f"Requestor:   {requestor}\n"
                f"{('End-buyer:   ' + end_buyer['companyName']) if end_buyer else ''}\n"
                f"Notes:       {demand_obj['notes'] or '(none)'}\n\n"
                f"Automation flags:\n{hints_txt}\n")
        try: _send_smtp(subject=f"[Portal] New quote request — {prod_name}", body=body)
        except Exception: pass
    except Exception:
        pass

    return jsonify({"status": "success", "message": "Quote request submitted.",
                    "auto_hints": auto_hints})


@portal_bp.route('/api/portal/rfq/submit/<token>', methods=['POST'])
@rate_limit(max_per_minute=10, key='portal_rfq_submit')
def submit_rfq(token):
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_auth(token, auth_header):
        abort(401)

    partner_id, partner = _find_partner_by_token(token, enforce_active=True)
    if not partner_id:
        abort(403)
    company_name = partner.get('companyName', 'Unknown')

    demand_data = request.json or {}

    def _safe_num(v, minv=0.0, maxv=1e12):
        try:
            n = float(v)
            if n != n or n < minv or n > maxv:
                return 0.0
            return round(n, 4)
        except (TypeError, ValueError):
            return 0.0

    # Ako klijent traži ponudu za KONKRETAN proizvod iz kataloga (klik na "Request quote"
    # sa katalog kartice), prosleđuje productId. Validiraćemo da postoji u bazi i da je
    # taj proizvod dostupan klijentu (nalazi se u partner.portalVisibleProducts).
    raw_product_id = str(demand_data.get("productId") or "").strip()
    linked_product_id = None
    linked_product = None
    if raw_product_id:
        prow = _dl_select_one('products', {'id': raw_product_id}) or {}
        if prow:
            pdata = store._entity_join(prow) if hasattr(store, '_entity_join') else dict(prow)
            if isinstance(pdata, dict):
                visible = partner.get('portalVisibleProducts')
                if not isinstance(visible, list) or raw_product_id in visible:
                    linked_product_id = raw_product_id
                    linked_product = pdata

    product_name = str(demand_data.get("productName", "")).strip()[:100]
    if not product_name and linked_product:
        product_name = str(linked_product.get('name', '')).strip()[:100]
    if not product_name:
        product_name = "Unspecified Commodity"

    demand_id = str(uuid.uuid4())
    now_iso = _iso_now()
    demand_obj = {
        "id": demand_id,
        "customerId": partner_id, "buyerId": partner_id, "buyer_id": partner_id,
        "productId": linked_product_id,
        "isNewProduct": linked_product_id is None,
        "productName": product_name,
        "hsCode": (linked_product or {}).get('hsCode') if linked_product else None,
        "unit": (linked_product or {}).get('unit') if linked_product else demand_data.get('unit'),
        "quantity": _safe_num(demand_data.get("quantity")),
        "targetPrice": _safe_num(demand_data.get("targetPrice")),
        "notes": str(demand_data.get("notes", "")).strip()[:1000],
        "date": now_iso,
        "createdAt": now_iso,
        "status": "pending",
        "source": "B2B Portal"
    }
    _dl_insert('demands', {
        'id': demand_id,
        'data': demand_obj,
        'buyer_id': partner_id,
        'created_at': now_iso,
    })
    log_audit('CREATE', 'demands', f"New RFQ for {product_name} submitted via portal by partner ID: {partner_id} ({company_name})", is_suspicious=False)
    log_portal_activity(partner_id, 'RFQ_SUBMIT', f"RFQ for {product_name}, qty: {demand_obj.get('quantity')}")
    return jsonify({"status": "success", "message": "Request for Quote securely submitted."})


# ==========================================================================
#  ADMIN: portal products review/import + source company approval
# ==========================================================================

@portal_bp.route('/api/portal/admin/products', methods=['GET'])
@login_required
def admin_get_portal_products():
    denied = require_portal_admin()
    if denied: return denied
    try:
        rows = _dl_select('portal_products', order='-created_at', limit=5000) or []
    except Exception as _e:
        logger.info(f'admin_get_portal_products failed: {_e}')
        rows = []

    partners_map = _partner_name_map()
    return jsonify([
        {
            "id": r.get('id'),
            "partner_id": r.get('partner_id'),
            "partner_name": partners_map.get(r.get('partner_id'), 'Unknown Partner'),
            "data": r.get('data') if isinstance(r.get('data'), dict) else {},
            "status": r.get('status'),
            "created_at": r.get('created_at'),
        } for r in rows
    ])


@portal_bp.route('/api/portal/admin/products/import/<product_id>', methods=['POST'])
@login_required
def admin_import_portal_product(product_id):
    """Admin uvozi predloženu robu iz portal_products u glavnu products bazu.
    Snima porijeklo: submittedByPartnerId (klijent koji ju je uneo), ownership
    ('own'/'third_party'), sourceCompany snapshot (ako je 3rd-party).

    Ako je 3rd-party i sourceCompanyPartnerId je već popunjen (npr. admin je već
    approve-ovao sourceCompany kao partnera), veže se supplyOffers.supplierId
    na taj partnerId. Inače, admin dobija u odgovoru {needs_company_approval:
    true, portal_product_id} kako bi frontend znao da pozove companies/approve."""
    denied = require_portal_admin()
    if denied: return denied

    row = _dl_select_one('portal_products', {'id': product_id}) or {}
    if not row:
        return jsonify({"error": "Product staging entry not found"}), 404

    submitting_partner_id = row.get('partner_id')
    raw_data = row.get('data')
    current_status = row.get('status')
    prod_data = raw_data if isinstance(raw_data, dict) else {}
    ownership = prod_data.get('ownership', 'own')
    source_company_partner_id = prod_data.get('sourceCompanyPartnerId')

    # Ako je 3rd-party i sourceCompany nije još pretvoren u partnera → tražimo taj korak prvo
    if ownership == 'third_party' and not source_company_partner_id:
        return jsonify({
            "needs_company_approval": True,
            "portal_product_id": product_id,
            "source_company": prod_data.get('sourceCompany') or {}
        }), 200

    # OK — kreiraj / update proizvod u glavnoj bazi
    new_id = str(uuid.uuid4())
    now_iso = _iso_now()
    new_product = {
        'id': new_id,
        'name': prod_data.get('name', ''),
        'category': prod_data.get('category', ''),
        'hsCode': prod_data.get('hsCode', ''),
        'brand': prod_data.get('brand', ''),
        'sku': prod_data.get('sku', ''),
        'detailedSpec': prod_data.get('detailedSpec', '') or prod_data.get('shortDescription', ''),
        'packaging': prod_data.get('packaging', ''),
        'imageUrl': prod_data.get('imageUrl', ''),
        'supplyOffers': prod_data.get('supplyOffers') or [],
        'coaParams': prod_data.get('coaParams') or [],
        'logistics': prod_data.get('logistics') or {},
        # Porijeklo — vidljivo u CRM Products
        'importedFromPortal': True,
        'submittedByPartnerId': submitting_partner_id,
        'submittedByPartnerName': prod_data.get('submittedByPartnerName', ''),
        'ownership': ownership,
        'sourcePartnerId': source_company_partner_id,
        'createdAt': now_iso,
        'ownerId': session.get('user_id', 'SYSTEM'),
        'sharedWith': []
    }
    if ownership == 'third_party' and source_company_partner_id:
        for so in new_product['supplyOffers']:
            if not so.get('supplierId'):
                so['supplierId'] = source_company_partner_id

    store.upsert_entity('products', new_product)

    _dl_update('portal_products', {'id': product_id}, {'status': 'imported'})

    log_audit('CREATE', 'products',
              f"Admin imported portal product '{new_product['name']}' (from partner {submitting_partner_id}, ownership={ownership})",
              is_suspicious=False)
    return jsonify({"status": "success", "product_id": new_id})


@portal_bp.route('/api/portal/admin/companies/approve', methods=['POST'])
@login_required
def admin_approve_source_company():
    """Kreira novog partnera iz sourceCompany objekta i veže ga za klijenta koji
    ga je uveo (introducedByPartnerId). Payload:
      { portal_product_id, decision: 'approve'|'reject', notes? }
    Na approve: kreira Partner (type='supplier'), i update-uje portal_product-a
    sa sourceCompanyPartnerId; admin zatim može da klikne 'Import' na proizvod.
    Na reject: samo obeležava portal_product kao 'company_rejected'."""
    denied = require_portal_admin()
    if denied: return denied

    payload = request.get_json(silent=True) or {}
    portal_product_id = payload.get('portal_product_id') or ''
    decision = str(payload.get('decision', 'approve')).lower()
    notes = str(payload.get('notes', '')).strip()[:800]
    if decision not in ('approve', 'reject'):
        return jsonify({"error": "INVALID_DECISION"}), 400

    row = _dl_select_one('portal_products', {'id': portal_product_id}) or {}
    if not row:
        return jsonify({"error": "PORTAL_PRODUCT_NOT_FOUND"}), 404
    introducing_partner_id = row.get('partner_id')
    raw = row.get('data')
    pdata = raw if isinstance(raw, dict) else {}
    src = pdata.get('sourceCompany') or {}
    if pdata.get('ownership') != 'third_party' or not src.get('name'):
        return jsonify({"error": "NOT_A_THIRD_PARTY_PRODUCT"}), 400

    if decision == 'reject':
        _dl_update('portal_products', {'id': portal_product_id}, {'status': 'company_rejected'})
        log_audit('REJECT', 'portal', f"Admin rejected source company {src.get('name')} (portal_product {portal_product_id})",
                  is_suspicious=False)
        return jsonify({"status": "success", "message": "Source company rejected."})

    # APPROVE — kreiraj partnera
    new_partner_id = str(uuid.uuid4())
    now_iso = _iso_now()
    partner_obj = {
        'id': new_partner_id,
        'companyName': src.get('name', ''),
        'taxId': src.get('taxId', ''),
        'types': ['Dobavljač'] if (src.get('relationship') or '').lower() in ('supplier', 'dobavljac', 'dobavljač') else ['Partner'],
        'address': {
            'street': src.get('address', ''),
            'city': src.get('city', ''),
            'country': src.get('country', ''),
        },
        'contact': {
            'email': src.get('email', ''),
            'phone': src.get('phone', ''),
            'website': src.get('website', ''),
        },
        'notes': notes or src.get('notes', ''),
        'introducedByPartnerId': introducing_partner_id,
        'introducedAt': now_iso,
        'createdViaPortal': True,
        'lastModified': now_iso,
        'ownerId': session.get('user_id', 'SYSTEM'),
        'sharedWith': [],
    }
    store.upsert_entity('partners', partner_obj)

    pdata['sourceCompanyPartnerId'] = new_partner_id
    _dl_update('portal_products', {'id': portal_product_id},
               {'data': pdata, 'status': 'company_approved'})

    log_audit('APPROVE', 'partners',
              f"Admin approved source company '{partner_obj['companyName']}' → new partner {new_partner_id} (introduced by {introducing_partner_id})",
              is_suspicious=False)
    return jsonify({"status": "success", "partner_id": new_partner_id,
                    "message": "Source company approved and created as partner."})


@portal_bp.route('/api/portal/admin/preview/<partner_id>', methods=['GET'])
@login_required
def admin_portal_preview(partner_id):
    """Vraća pun snapshot onoga što klijent VIDI u portalu — služi za admin
    'Impersonate' pregled bez otvaranja novog browser prozora."""
    denied = require_portal_admin()
    if denied: return denied

    partner = store.get_entity('partners', partner_id)
    if not partner:
        return jsonify({"error": "PARTNER_NOT_FOUND"}), 404

    permissions = partner.get('portalPermissions', ['shipments', 'offers', 'kyc', 'goods', 'profile', 'rfq', 'documents', 'catalog'])
    visible_products = partner.get('portalVisibleProducts') or []

    # Brojači vezani za partnera
    my_offers = 0
    my_deals = 0
    my_demands = 0
    my_docs = 0
    try:
        offers = store.list_entities('offers')
    except Exception:
        offers = []
    try:
        deals = store.list_entities('deals')
    except Exception:
        deals = []
    try:
        demands = store.list_entities('demands')
    except Exception:
        demands = []
    try:
        docs = store.list_entities('shared_documents')
    except Exception:
        docs = []

    for o in offers:
        if isinstance(o, dict) and o.get('customerId') == partner_id:
            my_offers += 1
    for d in deals:
        if isinstance(d, dict) and (d.get('customerId') == partner_id or d.get('buyerId') == partner_id):
            my_deals += 1
    for dm in demands:
        if isinstance(dm, dict) and dm.get('customerId') == partner_id:
            my_demands += 1
    for dc in docs:
        if isinstance(dc, dict) and dc.get('partnerId') == partner_id:
            my_docs += 1

    return jsonify({
        "partner_id": partner_id,
        "company_name": partner.get('companyName'),
        "email": partner.get('contact', {}).get('email') or partner.get('email', ''),
        "isPortalActive": partner.get('isPortalActive', True),
        "portalToken": partner.get('portalToken') or '',
        "permissions": permissions,
        "visible_products_count": len(visible_products),
        "visible_products": visible_products,
        "counts": {
            "offers": my_offers, "deals": my_deals,
            "demands": my_demands, "documents": my_docs
        }
    })


@portal_bp.route('/api/portal/admin/permissions/<partner_id>', methods=['POST'])
@login_required
def admin_update_portal_permissions(partner_id):
    """Admin menja koji su tabovi vidljivi u portalu ovog klijenta, i listu
    proizvoda koje vidi u katalogu."""
    denied = require_portal_admin()
    if denied: return denied
    data = request.get_json(silent=True) or {}
    permissions = data.get('permissions')
    visible_products = data.get('visible_products')

    partner = store.get_entity('partners', partner_id)
    if not partner:
        return jsonify({"error": "PARTNER_NOT_FOUND"}), 404
    if isinstance(permissions, list):
        allowed_tabs = {'shipments', 'offers', 'kyc', 'goods', 'profile', 'rfq', 'documents', 'catalog'}
        partner['portalPermissions'] = [str(p) for p in permissions if str(p) in allowed_tabs]
    if isinstance(visible_products, list):
        partner['portalVisibleProducts'] = [str(x) for x in visible_products if x]
    store.upsert_entity('partners', partner)

    log_audit('EDIT', 'portal', f"Admin updated portal permissions for partner {partner_id} "
                                f"(tabs: {len(partner.get('portalPermissions', []))}, products: {len(partner.get('portalVisibleProducts', []))})",
              is_suspicious=False)
    return jsonify({"status": "success", "permissions": partner.get('portalPermissions'),
                    "visible_products_count": len(partner.get('portalVisibleProducts', []))})


@portal_bp.route('/api/portal/admin/products/review/<product_id>', methods=['POST'])
@login_required
def admin_review_portal_product(product_id):
    denied = require_portal_admin()
    if denied: return denied
    action = (request.get_json(silent=True) or {}).get('action')
    row = _dl_select_one('portal_products', {'id': product_id}) or {}
    if not row:
        return jsonify({"error": "Product target not found"}), 404

    partner_id = row.get('partner_id')
    raw_data = row.get('data')
    prod_data = raw_data if isinstance(raw_data, dict) else {}

    if action == 'approve':
        prod_data['id'] = product_id
        prod_data['isPartnerApproved'] = True
        if 'supplyOffers' in prod_data and len(prod_data['supplyOffers']) > 0:
            for offer in prod_data['supplyOffers']:
                offer['supplierId'] = partner_id
        store.upsert_entity('products', prod_data)
        _dl_update('portal_products', {'id': product_id}, {'status': 'approved'})
        log_audit('APPROVE', 'portal', f"Admin approved custom product configuration '{prod_data.get('name')}'", is_suspicious=False)
    else:
        _dl_update('portal_products', {'id': product_id}, {'status': 'rejected'})
        log_audit('REJECT', 'portal', f"Admin rejected product suggestion '{prod_data.get('name')}'", is_suspicious=False)

    return jsonify({"status": "success", "message": "Operation processed successfully"})


# ==========================================================================
#  PORTAL FILE UPLOAD
# ==========================================================================

PORTAL_MAX_FILE_SIZE = 15 * 1024 * 1024  # 15 MB po fajlu
PORTAL_MAX_FILES_PER_REQUEST = 10


@portal_bp.route('/api/portal/upload/<token>', methods=['POST'])
@rate_limit(max_per_minute=20, key='portal_upload')
def portal_upload(token):
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_auth(token, auth_header):
        abort(401)

    files = request.files.getlist('file')
    if len(files) > PORTAL_MAX_FILES_PER_REQUEST:
        log_audit('SECURITY', 'portal_actions', f'Portal upload blocked: too many files ({len(files)})', is_suspicious=True)
        return jsonify({"error": "TOO_MANY_FILES"}), 400

    # partner_id iz TOKEN-a — za Supabase Storage bucket putanju
    partner_id, _p = _find_partner_by_token(token, enforce_active=True)

    # Lazy import — utils_storage povlaci supabase-py koji je optional
    _storage_mod = None
    try:
        import utils_storage as _storage_mod
    except Exception:
        _storage_mod = None

    urls = []
    for file in files:
        if not file or file.filename == '':
            continue

        file.stream.seek(0, os.SEEK_END)
        size = file.stream.tell()
        file.stream.seek(0)
        if size > PORTAL_MAX_FILE_SIZE:
            log_audit('SECURITY', 'portal_actions',
                      f'Portal upload blocked: file {file.filename} ({size} B) exceeds {PORTAL_MAX_FILE_SIZE} B',
                      is_suspicious=True)
            continue

        if '.' not in file.filename or file.filename.rsplit('.', 1)[1].lower() not in ALLOWED_EXTENSIONS:
            log_audit('SECURITY', 'portal_actions', f'Blocked disallowed extension: {file.filename}', is_suspicious=True)
            continue

        if not is_safe_file_content(file, file.filename):
            log_audit('SECURITY', 'portal_actions', f'Blocked file with suspicious magic bytes: {file.filename}', is_suspicious=True)
            continue

        ext = file.filename.rsplit('.', 1)[1].lower()
        new_filename = f"doc_{uuid.uuid4().hex}.{ext}"
        save_path = os.path.join(PORTAL_UPLOAD_FOLDER, secure_filename(new_filename))
        file.save(save_path)
        urls.append(f"/portal_uploads/{new_filename}")

        # DUAL-WRITE — mirror u Supabase Storage (best-effort, ne kvari request).
        if _storage_mod and _storage_mod.use_supabase_storage() and partner_id:
            try:
                bucket_path = _storage_mod.path_for_partner_doc(
                    partner_id, file.filename, subdir='portal-uploads'
                )
                with open(save_path, 'rb') as _rf:
                    content = _rf.read()
                _storage_mod.upload_bytes(
                    _storage_mod.BUCKET_PARTNER_DOCS,
                    bucket_path,
                    content,
                )
            except Exception as _e:
                try:
                    from routes.supabase_admin import record_error as _rec
                    _rec('portal_upload_storage_mirror', _e,
                         meta={'partner_id': partner_id, 'file': file.filename})
                except Exception:
                    pass

    if not urls:
        return jsonify({"error": "No valid or safe files uploaded."}), 400

    # OCR extraction u background thread-u — ne blokira response.
    # Rezultat se cesuje u file_text tabeli u Supabase.
    try:
        import threading
        def _extract_bg(pairs, pid):
            try:
                from utils_ocr import extract_text as _extr, summarize_text as _summ
                for local_path, url in pairs:
                    try:
                        text = _extr(local_path)
                        if not text:
                            continue
                        preview = _summ(text, max_len=500)
                        # Upsert po file_url (UNIQUE) — koristi data_layer.upsert
                        _dl_upsert('file_text', {
                            'id': str(uuid.uuid4()),
                            'file_url': url,
                            'partner_id': pid,
                            'filename': os.path.basename(local_path),
                            'text_preview': preview,
                            'full_text': text,
                            'char_count': len(text),
                            'extracted_at': _iso_now(),
                        }, on_conflict='file_url')
                    except Exception:
                        pass
            except Exception:
                pass
        pairs = []
        for u in urls:
            fname = u.rsplit('/', 1)[-1]
            local = os.path.join(PORTAL_UPLOAD_FOLDER, fname)
            if os.path.exists(local):
                pairs.append((local, u))
        if pairs:
            threading.Thread(target=_extract_bg, args=(pairs, partner_id), daemon=True).start()
    except Exception:
        pass

    return jsonify({"status": "success", "urls": urls})


# ==========================================================================
#  KYC SUBMIT + ADMIN REVIEW
# ==========================================================================

@portal_bp.route('/api/portal/kyc/submit/<token>', methods=['POST'])
@rate_limit(max_per_minute=5, key='portal_kyc_submit')
def submit_kyc(token):
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_auth(token, auth_header):
        abort(401)

    kyc_data = request.json or {}

    # BEZBEDNOST: partner_id se izvodi iz TOKENA (autoritativno), a ne iz payload-a.
    partner_id, _partner = _find_partner_by_token(token, enforce_active=True)
    if not partner_id:
        abort(403)

    # PREMIUM klijenti su izuzeti od svih hard-block validacija.
    # Osiguraj da `isPremium` postoji u dict-u (top-level kolona je `is_premium`).
    if 'isPremium' not in (_partner or {}):
        _partner['isPremium'] = _partner.get('is_premium', False) if _partner else False
    _is_premium = is_partner_premium(_partner or {})

    entity_type = str(kyc_data.get('entityType', 'company')).strip().lower()
    if entity_type not in ('company', 'individual'):
        entity_type = 'company'
    if entity_type == 'individual' and not _is_premium:
        files_dict = kyc_data.get('files') or {}
        if not (isinstance(files_dict, dict) and files_dict.get('proofOfAddress')):
            return jsonify({"error": "PROOF_OF_ADDRESS_REQUIRED",
                            "message": "Individuals must upload proof of home address (utility bill or bank statement)."}), 400

    _iban_in = str(kyc_data.get('bankIban', '')).strip()
    _bic_in = str(kyc_data.get('bankSwift', '')).strip()

    if not _is_premium:
        if not _bic_in:
            return jsonify({"error": "BIC_REQUIRED",
                            "message": "SWIFT/BIC is required for KYC submission."}), 400
        _iban_prefix = _iban_in.replace(' ', '').upper()[:2]
        _expected_country = _iban_prefix if _re_bv.match(r'^[A-Z]{2}$', _iban_prefix) else None
        _bic_res = validate_bic(_bic_in, _expected_country)
        if not _bic_res['valid']:
            return jsonify({"error": "BIC_INVALID", "reason": _bic_res.get('reason'),
                            "message": _bic_res.get('message', 'Invalid BIC/SWIFT')}), 400
        if _expected_country:
            _iban_res = validate_iban(_iban_in)
            if not _iban_res['valid']:
                return jsonify({"error": "IBAN_INVALID", "reason": _iban_res.get('reason'),
                                "message": _iban_res.get('message', 'Invalid IBAN')}), 400

    clean_data = {
        "entityType": entity_type,
        "companyName": str(kyc_data.get('companyName', '')).strip()[:150],
        "regNo": str(kyc_data.get('regNo', '')).strip()[:50],
        "taxId": str(kyc_data.get('taxId', '')).strip()[:50],
        "website": str(kyc_data.get('website', '')).strip()[:100],
        "industry": str(kyc_data.get('industry', '')).strip()[:100],
        "regAddr": str(kyc_data.get('regAddr', '')).strip()[:200],
        "opAddr": str(kyc_data.get('opAddr', '')).strip()[:200],
        "bankName": str(kyc_data.get('bankName', '')).strip()[:100],
        "bankIban": _iban_in[:50],
        "bankSwift": _bic_in[:20],
        "bankAddr": str(kyc_data.get('bankAddr', '')).strip()[:200],
        "corrBank": str(kyc_data.get('corrBank', '')).strip()[:100],
        "turnover": str(kyc_data.get('turnover', '')).strip()[:50],
        "sourceOfFunds": str(kyc_data.get('sourceOfFunds', '')).strip()[:150],
        "directors": _sanitize_persons(kyc_data.get('directors', [])),
        "ubos": _sanitize_persons(kyc_data.get('ubos', [])),
        "aml": kyc_data.get('aml', {}),
        "submitterName": str(kyc_data.get('submitterName', '')).strip()[:100],
        "submitterTitle": str(kyc_data.get('submitterTitle', '')).strip()[:100],
        "consent": bool(kyc_data.get('consent', False)),
        "files": kyc_data.get('files', {})
    }

    if not clean_data['consent']:
        return jsonify({"error": "Explicit consent is legally required."}), 400

    # AUTOMATSKO OPENSANCTIONS SCREENING pri submit-u KYC-a.
    sanctions_results = None
    try:
        # Napomena: sanctions_screen_batch je stub u originalu i bacice NameError —
        # cuvamo isto ponasanje (try/except).
        names_to_check = [clean_data.get('companyName')]
        for d in (clean_data.get('directors') or []):
            n = d.get('name') if isinstance(d, dict) else str(d)
            if n: names_to_check.append(n)
        for u in (clean_data.get('ubos') or []):
            n = u.get('name') if isinstance(u, dict) else str(u)
            if n: names_to_check.append(n)
        sanctions_results = sanctions_screen_batch([n for n in names_to_check if n])
        clean_data['_sanctionsScreening'] = {
            'ranAt': _iso_now(),
            'anyMatch': any(len(r.get('matches') or []) > 0 for r in (sanctions_results or [])),
            'results': sanctions_results,
        }
    except Exception:
        pass

    # Open Ownership PEP register lookup
    try:
        from security_ext import open_ownership_search
        oo_hits = []
        for nm in {clean_data.get('companyName')} | \
                 {(d.get('name') if isinstance(d, dict) else str(d)) for d in (clean_data.get('directors') or [])} | \
                 {(u.get('name') if isinstance(u, dict) else str(u)) for u in (clean_data.get('ubos') or [])}:
            if not nm: continue
            res = open_ownership_search(nm)
            if res: oo_hits.append({'name': nm, 'entries': res})
        if oo_hits:
            clean_data['_openOwnershipHits'] = {
                'ranAt': _iso_now(),
                'hits': oo_hits,
            }
    except Exception:
        pass

    # Snimi KYC submission — data je sifrovana Fernet-om pre upisa
    sub_id = str(uuid.uuid4())
    now_iso = _iso_now()
    _dl_insert('kyc_submissions', {
        'id': sub_id,
        'partner_id': partner_id,
        'data': encrypt_data(clean_data),  # JSONB TEXT — Fernet ciphertext
        'status': 'pending',
        'submitted_at': now_iso,
    })
    log_audit('EDIT', 'portal', f"Partner {clean_data.get('companyName')} payload securely encrypted inside air-gapped vault", is_suspicious=False)
    if sanctions_results and any(len(r.get('matches') or []) > 0 for r in sanctions_results):
        log_audit('WARNING', 'sanctions',
                  f"KYC submission for {clean_data.get('companyName')} produced sanctions matches — REQUIRES ADMIN REVIEW",
                  is_suspicious=True)
        try:
            from webhooks import notify as _notify
            _notify('sanctions_flag', {
                'Company': clean_data.get('companyName'),
                'Partner ID': partner_id,
                'Matches': sum(len(r.get('matches') or []) for r in (sanctions_results or [])),
                'Action': 'Review before approval',
            })
        except Exception: pass
    log_portal_activity(partner_id, 'KYC_SUBMIT', f'KYC submission by {clean_data.get("companyName")}')
    try:
        from webhooks import notify as _notify
        _notify('kyc_submitted', {
            'Company': clean_data.get('companyName'),
            'Type': entity_type,
            'Country': clean_data.get('regAddr', '')[:80],
        })
    except Exception: pass
    return jsonify({"status": "success", "message": "KYC Data securely submitted to Vault."})


def _decrypt_kyc_payload(raw):
    """Helper — ocekujemo Fernet ciphertext (TEXT u JSONB koloni) i vracamo dict."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        # Vec desifrovan/JSONB dict — ne treba decrypt
        return raw
    try:
        return decrypt_data(raw)
    except Exception:
        try:
            return json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            return {}


@portal_bp.route('/api/portal/admin/submissions/<partner_id>', methods=['GET'])
@login_required
def get_kyc_submissions_by_partner(partner_id):
    """Vraca sve KYC prijave za konkretnog partnera (najnovija prva)."""
    denied = require_portal_admin()
    if denied: return denied

    try:
        rows = _dl_select('kyc_submissions',
                          filters={'partner_id': partner_id},
                          order='-submitted_at', limit=500) or []
    except Exception as _e:
        logger.info(f'get_kyc_submissions_by_partner failed: {_e}')
        rows = []

    subs = []
    for r in rows:
        data = _decrypt_kyc_payload(r.get('data'))
        subs.append({
            "id": r.get('id'),
            "partner_id": r.get('partner_id'),
            "data": data if isinstance(data, dict) else {},
            "submitted_at": r.get('submitted_at')
        })
    return jsonify(subs)


@portal_bp.route('/api/portal/admin/submissions/all', methods=['GET'])
@login_required
def get_all_kyc_submissions():
    denied = require_portal_admin()
    if denied: return denied
    try:
        rows = _dl_select('kyc_submissions', order='-submitted_at', limit=5000) or []
    except Exception as _e:
        logger.info(f'get_all_kyc_submissions failed: {_e}')
        rows = []
    partners_map = _partner_name_map()

    subs = [{
        "id": r.get('id'),
        "partner_id": r.get('partner_id'),
        "partner_name": partners_map.get(r.get('partner_id'), 'Unknown'),
        "data": _decrypt_kyc_payload(r.get('data')),
        "submitted_at": r.get('submitted_at')
    } for r in rows]
    return jsonify(subs)


def _load_kyc_submission_for_review(sub_id):
    """Helper — ucitava KYC submission za admin review (approve/reject/update)."""
    row = _dl_select_one('kyc_submissions', {'id': sub_id}) or {}
    if not row:
        return None, None, None
    partner_id = row.get('partner_id')
    raw = row.get('data')
    kyc_data = _decrypt_kyc_payload(raw)
    if not isinstance(kyc_data, dict):
        kyc_data = {}
    return row, partner_id, kyc_data


def _save_kyc_submission(sub_id, kyc_data, status=None, reviewed_by=None, reviewed_at=None):
    """Helper — snima back KYC submission (data je Fernet ciphertext)."""
    patch = {'data': encrypt_data(kyc_data)}
    if status:
        patch['status'] = status
    if reviewed_by is not None:
        patch['reviewed_by'] = reviewed_by
    if reviewed_at is not None:
        patch['reviewed_at'] = reviewed_at
    _dl_update('kyc_submissions', {'id': sub_id}, patch)


@portal_bp.route('/api/portal/admin/submissions/approve/<sub_id>', methods=['POST'])
@login_required
def approve_kyc_submission(sub_id):
    """Odobrava KYC podnesak i MERGE-uje sve podatke u partner profil (banking,
    directors, UBOs, AML, fajlovi, tax/reg brojevi, adresa). Dodatno prihvata
    riskLevel i notes iz forme i beleži ih u partner.kyc + partner.activities.
    Šalje email potvrdu klijentu (profesionalni šablon)."""
    denied = require_portal_admin()
    if denied: return denied

    payload = request.get_json(silent=True) or {}
    risk_level = str(payload.get('riskLevel', 'medium')).strip()
    notes = str(payload.get('notes', '')).strip()[:2000]
    sanctions_ack = bool(payload.get('sanctionsAck', False))
    sanctions_ack_note = str(payload.get('sanctionsAckNote', '')).strip()[:1000]

    row, partner_id, kyc_data = _load_kyc_submission_for_review(sub_id)
    if not row:
        return jsonify({"error": "Submission not found"}), 404

    # HARD GATE: ako je OpenSanctions screening zabeležio matcheve, admin MORA
    # eksplicitno da prizna odgovornost pre nego što odobri partnera.
    _screening = kyc_data.get('_sanctionsScreening') or {}
    if _screening.get('anyMatch') and not sanctions_ack:
        return jsonify({
            "error": "SANCTIONS_ACK_REQUIRED",
            "message": ("This KYC submission has OpenSanctions matches. You must acknowledge "
                        "responsibility for approving this partner before the system will proceed."),
            "matches_count": sum(len(r.get('matches') or []) for r in (_screening.get('results') or [])),
        }), 400
    if _screening.get('anyMatch') and sanctions_ack:
        log_audit('SECURITY', 'sanctions',
                  f"Admin {session.get('username','?')} ACKNOWLEDGED sanctions match and approved partner {partner_id}. "
                  f"Note: {sanctions_ack_note[:200]}",
                  is_suspicious=True)
        kyc_data['_sanctionsAck'] = {
            'ackAt': _iso_now(),
            'ackBy': session.get('username', 'admin'),
            'note': sanctions_ack_note,
        }

    # Označi kao odobreno u portal bazi (za istoriju)
    now_iso = _iso_now()
    if 'status' not in kyc_data:
        kyc_data['status'] = 'approved'
    kyc_data['reviewedAt'] = now_iso
    kyc_data['reviewedBy'] = session.get('username', 'admin')
    _save_kyc_submission(sub_id, kyc_data, status='approved',
                         reviewed_by=kyc_data['reviewedBy'],
                         reviewed_at=kyc_data['reviewedAt'])

    partner = store.get_entity('partners', partner_id)
    if not partner:
        return jsonify({"error": "Partner not found"}), 404

    partner['companyName'] = kyc_data.get('companyName') or partner.get('companyName')
    partner['taxId'] = kyc_data.get('taxId') or partner.get('taxId')
    partner['regNumber'] = kyc_data.get('regNo') or partner.get('regNumber')
    partner['industry'] = kyc_data.get('industry') or partner.get('industry')

    if 'contact' not in partner or not isinstance(partner['contact'], dict): partner['contact'] = {}
    if kyc_data.get('website'): partner['contact']['website'] = kyc_data['website']
    if kyc_data.get('contactPhone'): partner['contact']['phone'] = kyc_data['contactPhone']

    if 'address' not in partner or not isinstance(partner['address'], dict): partner['address'] = {}
    if kyc_data.get('regAddr'): partner['address']['street'] = kyc_data['regAddr']
    if kyc_data.get('city'): partner['address']['city'] = kyc_data['city']
    if kyc_data.get('country'): partner['address']['country'] = kyc_data['country']
    if kyc_data.get('zip'): partner['address']['zip'] = kyc_data['zip']
    if kyc_data.get('opAddr'): partner['address']['operationalAddress'] = kyc_data['opAddr']

    if 'bank' not in partner or not isinstance(partner['bank'], dict): partner['bank'] = {}
    if kyc_data.get('bankName'): partner['bank']['name'] = kyc_data['bankName']
    if kyc_data.get('bankIban'): partner['bank']['accountNumber'] = kyc_data['bankIban']
    if kyc_data.get('bankSwift'): partner['bank']['swift'] = kyc_data['bankSwift']
    if kyc_data.get('bankAddr'): partner['bank']['bankAddress'] = kyc_data['bankAddr']
    if kyc_data.get('corrBank'): partner['bank']['correspondentBank'] = kyc_data['corrBank']

    if 'kyc' not in partner or not isinstance(partner['kyc'], dict): partner['kyc'] = {}
    partner['kyc']['status'] = 'approved'
    partner['kyc']['riskLevel'] = risk_level
    partner['kyc']['notes'] = notes
    partner['kyc']['reviewedAt'] = kyc_data['reviewedAt']
    partner['kyc']['reviewedBy'] = kyc_data['reviewedBy']
    partner['kyc']['directors'] = kyc_data.get('directors', [])
    partner['kyc']['ubos'] = kyc_data.get('ubos', [])
    partner['kyc']['aml'] = kyc_data.get('aml', {})
    partner['kyc']['files'] = kyc_data.get('files', {})
    partner['kyc']['turnover'] = kyc_data.get('turnover')
    partner['kyc']['sourceOfFunds'] = kyc_data.get('sourceOfFunds')
    partner['kyc']['submitterName'] = kyc_data.get('submitterName')
    partner['kyc']['submitterTitle'] = kyc_data.get('submitterTitle')

    # Aktivnost za audit trag u CRM-u
    if 'activities' not in partner or not isinstance(partner['activities'], list): partner['activities'] = []
    partner['activities'].insert(0, {
        'id': uuid.uuid4().hex,
        'date': kyc_data['reviewedAt'],
        'type': 'KYC Approved',
        'note': f"KYC odobren by {kyc_data['reviewedBy']}. Risk: {risk_level}." + (f" Notes: {notes}" if notes else "")
    })

    store.upsert_entity('partners', partner)
    log_audit('APPROVE', 'kyc', f"KYC merged into CRM for {partner.get('companyName')} (risk: {risk_level})", is_suspicious=False)

    # Profesionalan email klijentu
    client_email = partner.get('contact', {}).get('email') or partner.get('email')
    if client_email:
        try:
            from utils_email import send_kyc_approved
            token = partner.get('portalToken', '')
            portal_url = request.url_root.rstrip('/') + f"/portal/{token}" if token else request.url_root
            send_kyc_approved(client_email, partner.get('companyName', ''), portal_url)
        except Exception as e:
            log_audit('ERROR', 'kyc', f"Failed to send KYC approval email: {e}", is_suspicious=False)

    return jsonify({"status": "success", "message": "KYC data merged to CRM profile.", "kyc": partner.get('kyc', {})})


@portal_bp.route('/api/portal/admin/submissions/request_update/<sub_id>', methods=['POST'])
@login_required
def request_kyc_update(sub_id):
    """Označava KYC kao 'update_requested' — klijent u portalu vidi banner sa
    porukom da admin traži dopunu podataka. Šalje email sa razlogom."""
    denied = require_portal_admin()
    if denied: return denied

    payload = request.get_json(silent=True) or {}
    note = str(payload.get('notes', '')).strip()[:2000]
    risk_level = str(payload.get('riskLevel', 'medium')).strip()

    row, partner_id, kyc_data = _load_kyc_submission_for_review(sub_id)
    if not row:
        return jsonify({"error": "Submission not found"}), 404
    now_iso = _iso_now()
    kyc_data['status'] = 'update_requested'
    kyc_data['reviewNote'] = note
    kyc_data['reviewedAt'] = now_iso
    kyc_data['reviewedBy'] = session.get('username', 'admin')
    _save_kyc_submission(sub_id, kyc_data, status='update_requested',
                         reviewed_by=kyc_data['reviewedBy'],
                         reviewed_at=now_iso)

    # Update partner.kycStatus za portal banner
    partner = None
    if partner_id:
        partner = store.get_entity('partners', partner_id)
    if partner:
        if 'kyc' not in partner or not isinstance(partner['kyc'], dict): partner['kyc'] = {}
        partner['kyc']['status'] = 'update_requested'
        partner['kyc']['reviewNote'] = note
        partner['kyc']['riskLevel'] = risk_level
        if 'activities' not in partner or not isinstance(partner['activities'], list): partner['activities'] = []
        partner['activities'].insert(0, {
            'id': uuid.uuid4().hex,
            'date': kyc_data['reviewedAt'],
            'type': 'KYC Update Requested',
            'note': note or 'Additional information required.'
        })
        store.upsert_entity('partners', partner)

    log_audit('EDIT', 'kyc', f"KYC update requested for {(partner or {}).get('companyName', partner_id)}: {note[:100]}", is_suspicious=False)

    client_email = (partner or {}).get('contact', {}).get('email') or (partner or {}).get('email') if partner else None
    if client_email:
        try:
            from utils_email import send_kyc_update_requested
            token = (partner or {}).get('portalToken', '') if partner else ''
            portal_url = request.url_root.rstrip('/') + f"/portal/{token}" if token else request.url_root
            send_kyc_update_requested(client_email, (partner or {}).get('companyName', ''), portal_url, note)
        except Exception as e:
            log_audit('ERROR', 'kyc', f"Failed to send KYC update-requested email: {e}", is_suspicious=False)

    return jsonify({"status": "success", "message": "Client notified — additional information requested."})


@portal_bp.route('/api/portal/admin/submissions/reject/<sub_id>', methods=['POST'])
@login_required
def reject_kyc_submission(sub_id):
    """Odbija KYC. Ne merge-uje podatke; partner.kycStatus = 'rejected'."""
    denied = require_portal_admin()
    if denied: return denied

    payload = request.get_json(silent=True) or {}
    note = str(payload.get('notes', '')).strip()[:2000]

    row, partner_id, kyc_data = _load_kyc_submission_for_review(sub_id)
    if not row:
        return jsonify({"error": "Submission not found"}), 404
    now_iso = _iso_now()
    kyc_data['status'] = 'rejected'
    kyc_data['reviewNote'] = note
    kyc_data['reviewedAt'] = now_iso
    kyc_data['reviewedBy'] = session.get('username', 'admin')
    _save_kyc_submission(sub_id, kyc_data, status='rejected',
                         reviewed_by=kyc_data['reviewedBy'],
                         reviewed_at=now_iso)

    partner = None
    if partner_id:
        partner = store.get_entity('partners', partner_id)
    if partner:
        if 'kyc' not in partner or not isinstance(partner['kyc'], dict): partner['kyc'] = {}
        partner['kyc']['status'] = 'rejected'
        partner['kyc']['reviewNote'] = note
        if 'activities' not in partner or not isinstance(partner['activities'], list): partner['activities'] = []
        partner['activities'].insert(0, {
            'id': uuid.uuid4().hex,
            'date': kyc_data['reviewedAt'],
            'type': 'KYC Rejected',
            'note': note or 'KYC rejected.'
        })
        store.upsert_entity('partners', partner)

    log_audit('REJECT', 'kyc', f"KYC rejected for {(partner or {}).get('companyName', partner_id)}", is_suspicious=True)
    return jsonify({"status": "success", "message": "KYC submission rejected."})


@portal_bp.route('/portal_uploads/<filename>')
@login_required
def serve_portal_uploads(filename):
    denied = require_partner_view()
    if denied: return denied
    log_audit('DOWNLOAD', 'portal', f'KYC/portal document downloaded: {secure_filename(filename)}', is_suspicious=False)
    return send_from_directory(current_app.config['PORTAL_UPLOAD_FOLDER'], secure_filename(filename))


# ==========================================================
#  PROFILE CHANGE REQUESTS — admin lista / odobrenje / odbijanje
# ==========================================================

@portal_bp.route('/api/portal/admin/profile_requests', methods=['GET'])
@login_required
def admin_list_profile_requests():
    """Vraća sve pending zahteve za izmenu partnerskog profila (email, telefon, adresa)."""
    denied = require_portal_admin()
    if denied: return denied
    status_filter = request.args.get('status')

    filters = {}
    if status_filter:
        filters['status'] = status_filter
    try:
        rows = _dl_select('profile_change_requests',
                          filters=filters or None,
                          order='-submitted_at', limit=5000) or []
    except Exception as _e:
        logger.info(f'admin_list_profile_requests failed: {_e}')
        rows = []

    # Mapa partnera sa trenutnim podacima (za diff prikaz u UI)
    partners_map = {}
    try:
        for p in store.list_entities('partners'):
            pid = p.get('id')
            if pid:
                partners_map[pid] = {
                    'name': p.get('companyName', 'Unknown'),
                    'currentEmail': (p.get('contact', {}) or {}).get('email') or p.get('email', ''),
                    'currentPhone': (p.get('contact', {}) or {}).get('phone') or p.get('phone', ''),
                    'currentPerson': (p.get('contact', {}) or {}).get('person', ''),
                    'currentStreet': (p.get('address', {}) or {}).get('street', ''),
                    'currentCity': (p.get('address', {}) or {}).get('city', ''),
                    'currentCountry': (p.get('address', {}) or {}).get('country', ''),
                }
    except Exception as _e:
        logger.info(f'admin_list_profile_requests partners load failed: {_e}')

    result = []
    for r in rows:
        pinfo = partners_map.get(r.get('partner_id'), {'name': 'Unknown'})
        raw = r.get('data')
        changes = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) and raw else {})
        result.append({
            "id": r.get('id'),
            "partner_id": r.get('partner_id'),
            "partner_name": pinfo.get('name'),
            "current": {k: v for k, v in pinfo.items() if k.startswith('current')},
            "changes": changes,
            "status": r.get('status'),
            "submitted_at": r.get('submitted_at'),
            "reviewed_at": r.get('reviewed_at')
        })
    return jsonify(result)


@portal_bp.route('/api/portal/admin/profile_requests/<req_id>/review', methods=['POST'])
@login_required
def admin_review_profile_request(req_id):
    """Odobrava ili odbija zahtev za izmenu profila. Na odobrenje primenjuje
    tražene izmene na partnerski profil u CRM bazi i beleži audit trag."""
    denied = require_portal_admin()
    if denied: return denied
    action = (request.get_json(silent=True) or {}).get('action', '').lower()
    if action not in ('approve', 'reject'):
        return jsonify({"error": "INVALID_ACTION"}), 400

    row = _dl_select_one('profile_change_requests', {'id': req_id}) or {}
    if not row:
        return jsonify({"error": "REQUEST_NOT_FOUND"}), 404
    if row.get('status') != 'pending':
        return jsonify({"error": "ALREADY_REVIEWED"}), 400

    partner_id = row.get('partner_id')
    raw = row.get('data')
    changes = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) and raw else {})
    now_iso = _iso_now()
    reviewer = session.get('username', 'admin')

    if action == 'approve':
        partner = store.get_entity('partners', partner_id) if partner_id else None
        if not partner:
            return jsonify({"error": "PARTNER_NOT_FOUND"}), 404
        if 'contact' not in partner: partner['contact'] = {}
        if 'address' not in partner: partner['address'] = {}
        summary = []
        if 'email' in changes:
            old = partner.get('contact', {}).get('email') or partner.get('email', '')
            partner['contact']['email'] = changes['email']; partner['email'] = changes['email']
            summary.append(f"email: {old} → {changes['email']}")
        if 'phone' in changes:
            old = partner.get('contact', {}).get('phone') or partner.get('phone', '')
            partner['contact']['phone'] = changes['phone']; partner['phone'] = changes['phone']
            summary.append(f"phone: {old} → {changes['phone']}")
        if 'contactPerson' in changes:
            old = partner.get('contact', {}).get('person', '')
            partner['contact']['person'] = changes['contactPerson']
            summary.append(f"person: {old} → {changes['contactPerson']}")
        if 'street' in changes:
            partner['address']['street'] = changes['street']
            summary.append(f"street → {changes['street']}")
        if 'city' in changes:
            partner['address']['city'] = changes['city']
            summary.append(f"city → {changes['city']}")
        if 'country' in changes:
            partner['address']['country'] = changes['country']
            summary.append(f"country → {changes['country']}")
        store.upsert_entity('partners', partner)
        _dl_update('profile_change_requests', {'id': req_id},
                   {'status': 'approved', 'reviewed_at': now_iso, 'reviewed_by': reviewer})
        log_audit('APPROVE', 'portal', f"Approved profile change for partner {partner_id}: {', '.join(summary)}", is_suspicious=False)
        return jsonify({"status": "success", "message": "Zahtev odobren i primenjen.", "applied": changes})
    else:
        _dl_update('profile_change_requests', {'id': req_id},
                   {'status': 'rejected', 'reviewed_at': now_iso, 'reviewed_by': reviewer})
        log_audit('REJECT', 'portal', f"Rejected profile change request {req_id} for partner {partner_id}", is_suspicious=False)
        return jsonify({"status": "success", "message": "Zahtev odbijen."})


# ==========================================================
#  PDF DOWNLOAD IZ PORTALA — sa obaveznim audit tragom
# ==========================================================

@portal_bp.route('/api/portal/document/<token>/<doc_id>', methods=['GET'])
def portal_download_document(token, doc_id):
    """Klijent portala otvara ili preuzima svoj dokument.

    Podržava dva režima preko query stringa:
      - ?inline=1  → Content-Disposition: inline (za preview u iframe-u)
      - default    → attachment (klasičan download)

    Radi sa dva izvora dokumenta (backward-compat):
      1) Nova ponuda: doc.sourceType == 'OFFER' + sourceOfferId — PDF se
         regeneriše iz aktuelne ponude u bazi (nema pisanja na disk).
      2) Legacy fajlovi: doc.fileUrl je '/uploads/...' ili '/portal_uploads/...'
         — služi se sa diska.

    Svaki view/download se beleži u audit trag."""
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_session(token, auth_header):
        return jsonify({"error": "UNAUTHORIZED"}), 401

    inline = request.args.get('inline') in ('1', 'true', 'yes')

    partner_id, partner = _find_partner_by_token(token, enforce_active=True)
    if not partner_id:
        return jsonify({"error": "FORBIDDEN"}), 403

    # Dokument mora pripadati OVOM partneru (sprečava enumeraciju tuđih doc_id-eva).
    doc_row = _dl_select_one('shared_documents', {'id': doc_id}) or {}
    if not doc_row:
        return jsonify({"error": "DOCUMENT_NOT_FOUND"}), 404
    doc = store._entity_join(doc_row) if hasattr(store, '_entity_join') else dict(doc_row)
    if doc.get('partnerId') != partner_id:
        log_audit('SECURITY', 'portal', f'Blocked cross-partner document access attempt: doc {doc_id} by {partner_id}', is_suspicious=True)
        return jsonify({"error": "FORBIDDEN"}), 403

    company = partner.get('companyName', 'Unknown')
    file_name = doc.get('fileName') or 'Document.pdf'
    action_kind = 'PREVIEW' if inline else 'DOWNLOAD'

    # PUT #1: OFFER referenca — regeneriši u memoriji, ne pisati na disk.
    if doc.get('sourceType') == 'OFFER' and doc.get('sourceOfferId'):
        from pdf_generator import regenerate_offer_pdf_by_id
        pdf_bytes = regenerate_offer_pdf_by_id(doc['sourceOfferId'])
        if not pdf_bytes:
            log_audit('ERROR', 'portal',
                      f"Client '{company}' tried to access offer PDF {doc_id} but source offer missing", is_suspicious=True)
            return jsonify({"error": "SOURCE_MISSING"}), 410
        log_audit('DOWNLOAD', 'portal',
                  f"Client '{company}' {action_kind.lower()}ed document '{file_name}' (type: {doc.get('docType', 'OFFER')}, on-demand) via portal",
                  is_suspicious=False)
        log_portal_activity(partner_id, f'DOCUMENT_{action_kind}', f"{file_name} (offer regen)")
        from flask import Response
        return Response(
            pdf_bytes,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'{"inline" if inline else "attachment"}; filename="{file_name}"',
                'Cache-Control': 'private, no-store',
            }
        )

    file_url = doc.get('fileUrl') or ''
    if not file_url:
        return jsonify({"error": "DOCUMENT_NOT_FOUND"}), 404

    # PUT #2: INLINE data URI (legacy admin jsPDF flow).
    if file_url.startswith('data:'):
        try:
            import base64 as _b64
            header, _, payload = file_url.partition(',')
            if not payload or ';base64' not in header:
                return jsonify({"error": "INVALID_FILE"}), 400
            pdf_bytes = _b64.b64decode(payload)
        except Exception as e:
            log_audit('ERROR', 'portal', f'Failed to decode inline PDF for doc {doc_id}: {e}', is_suspicious=True)
            return jsonify({"error": "INVALID_FILE"}), 400
        log_audit('DOWNLOAD', 'portal',
                  f"Client '{company}' {action_kind.lower()}ed document '{file_name}' (type: {doc.get('docType', 'Document')}, inline) via portal",
                  is_suspicious=False)
        log_portal_activity(partner_id, f'DOCUMENT_{action_kind}', f"{file_name} (inline)")
        from flask import Response
        return Response(
            pdf_bytes, mimetype='application/pdf',
            headers={
                'Content-Disposition': f'{"inline" if inline else "attachment"}; filename="{file_name}"',
                'Cache-Control': 'private, no-store',
            }
        )

    # PUT #3: legacy file na disku
    filename = os.path.basename(file_url)
    safe_name = secure_filename(filename)
    if not safe_name:
        return jsonify({"error": "INVALID_FILE"}), 400
    if file_url.startswith('/portal_uploads/'):
        folder = current_app.config['PORTAL_UPLOAD_FOLDER']
    else:
        folder = current_app.config['UPLOAD_FOLDER']

    disk_path = os.path.join(folder, safe_name)
    if not os.path.exists(disk_path) and doc.get('sourceOfferId'):
        from pdf_generator import regenerate_offer_pdf_by_id
        pdf_bytes = regenerate_offer_pdf_by_id(doc['sourceOfferId'])
        if pdf_bytes:
            log_audit('DOWNLOAD', 'portal',
                      f"Client '{company}' {action_kind.lower()}ed document '{file_name}' (fallback regen) via portal",
                      is_suspicious=False)
            log_portal_activity(partner_id, f'DOCUMENT_{action_kind}', f"{file_name} (fallback regen)")
            from flask import Response
            return Response(
                pdf_bytes, mimetype='application/pdf',
                headers={
                    'Content-Disposition': f'{"inline" if inline else "attachment"}; filename="{file_name}"',
                    'Cache-Control': 'private, no-store',
                }
            )

    if not os.path.exists(disk_path):
        log_audit('ERROR', 'portal',
                  f"Client '{company}' tried to access doc {doc_id} but file missing: {file_url}",
                  is_suspicious=True)
        return jsonify({"error": "DOCUMENT_NOT_FOUND"}), 404

    log_audit('DOWNLOAD', 'portal',
              f"Client '{company}' {action_kind.lower()}ed document '{file_name}' (type: {doc.get('docType', 'Document')}) via portal",
              is_suspicious=False)
    log_portal_activity(partner_id, f'DOCUMENT_{action_kind}', f"{file_name}")
    return send_from_directory(folder, safe_name, as_attachment=(not inline), download_name=file_name)


# ==========================================================
#  PORTAL: prihvatanje ponude od strane klijenta
# ==========================================================

@portal_bp.route('/api/portal/offers/accept/<token>/<offer_id>', methods=['POST'])
@rate_limit(max_per_minute=20, key='portal_offer_response')
def portal_accept_offer(token, offer_id):
    """Klijent u portalu potvrđuje ili odbija ponudu. Server:
      1. čuva clientStatus + timestamp + clientNote (razlog odbijanja)
      2. postavlja adminReviewedByClient = False → CRM notifikacija se pojavi
         adminu na dashboard-u dok je ne pregleda ('Client responded')
      3. šalje SMTP obaveštenje adminu (best-effort)
      4. loguje u portal_activity_log sa razlogom odbijanja"""
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_session(token, auth_header):
        return jsonify({"error": "UNAUTHORIZED"}), 401

    payload = request.get_json(silent=True) or {}
    action = str(payload.get('action', 'accept')).lower()
    note = str(payload.get('note', '')).strip()[:1000]
    signature = payload.get('signature')

    if action == 'decline' and len(note) < 3:
        return jsonify({"error": "DECLINE_REASON_REQUIRED",
                        "message": "Please provide a short reason for declining."}), 400

    signature_ok = None
    if action == 'accept' and isinstance(signature, dict):
        du = signature.get('dataUrl') or ''
        sn = str(signature.get('signerName', '')).strip()[:200]
        if (isinstance(du, str) and du.startswith('data:image/png;base64,')
                and len(du) <= 200_000 and sn):
            signature_ok = {
                'dataUrl': du,
                'signerName': sn,
                'signedAt': str(signature.get('signedAt', ''))[:64],
                'userAgent': str(signature.get('userAgent', ''))[:500],
                'ipAddress': (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()[:64],
            }

    partner_id, partner = _find_partner_by_token(token, enforce_active=True)
    if not partner_id:
        return jsonify({"error": "FORBIDDEN"}), 403

    offer_row = _dl_select_one('offers', {'id': offer_id}) or {}
    if not offer_row:
        return jsonify({"error": "OFFER_NOT_FOUND"}), 404
    offer = store._entity_join(offer_row) if hasattr(store, '_entity_join') else dict(offer_row)
    if offer.get('customerId') != partner_id:
        log_audit('SECURITY', 'portal', f'Blocked cross-partner offer accept attempt: offer {offer_id} by {partner_id}', is_suspicious=True)
        return jsonify({"error": "FORBIDDEN"}), 403

    # OFFER VERSIONING: pre snimanja novog stanja, zapamti stari snapshot.
    import copy as _copy
    try:
        _old_offer_ver = _copy.deepcopy(offer)
    except Exception:
        _old_offer_ver = None

    now_iso = _iso_now()
    if action == 'accept':
        offer['clientStatus'] = 'accepted'
        offer['clientAcceptedAt'] = now_iso
        offer['clientNote'] = note
        if signature_ok:
            offer['clientSignature'] = signature_ok
        log_action = 'APPROVE'
    elif action == 'decline':
        offer['clientStatus'] = 'declined'
        offer['clientDeclinedAt'] = now_iso
        offer['clientNote'] = note
        log_action = 'REJECT'
    else:
        return jsonify({"error": "INVALID_ACTION"}), 400

    offer['adminReviewedByClient'] = False
    offer['clientResponseAt'] = now_iso

    # Snapshot pre upisa (best-effort — ne rusi accept flow ako pukne)
    if _old_offer_ver:
        _portal_offer_snapshot(
            offer_id, _old_offer_ver, offer,
            changed_by=partner_id,
            change_reason=(f'Client {action}' + (f': {note[:200]}' if note else '')),
        )

    # Snimi offer nazad
    store.upsert_entity('offers', offer)

    # V23.1 #7 — PORTAL ACCEPT TRACEABILITY: registruj u document_revisions
    # da je klijent prihvatio ponudu. Ne pravi novu reviziju, samo audit
    # zapis vezan za docNumber ponude (ako je finalizovana).
    try:
        if action == 'accept':
            _doc_num = offer.get('docNumber') or offer.get('offerNo')
            if _doc_num:
                _dl_insert('document_revisions', {
                    'id': str(uuid.uuid4()),
                    'doc_number': _doc_num,
                    'revision': int(offer.get('revision', 0)),
                    'entity_id': partner_id,
                    'snapshot': {
                        'event': 'portal_accept',
                        'partner_id': partner_id,
                        'accepted_at': now_iso,
                        'signed': bool(signature_ok),
                    },
                    'change_reason': (
                        f'Portal ACCEPT by {partner.get("companyName")}'
                        + (' with signature' if signature_ok else '')
                    ),
                    'changed_by': partner.get('companyName', 'client'),
                    'changed_at': now_iso,
                })
    except Exception as _ev_err:
        logger.warning(f'portal-accept register trace failed: {_ev_err}')

    log_audit(log_action, 'portal',
              f"Client '{partner.get('companyName')}' {action}ed offer {offer.get('offerNo', offer_id)}"
              + (f" — Reason: {note[:200]}" if action == 'decline' else '')
              + (f" — Signed by {signature_ok['signerName']} @ {signature_ok['signedAt']} from IP {signature_ok['ipAddress']}"
                 if signature_ok else ''),
              is_suspicious=False)
    log_portal_activity(partner_id, f'OFFER_{action.upper()}',
                        f"Offer {offer.get('offerNo', offer_id)}"
                        + (f" — {note[:200]}" if note else ''))
    try:
        from webhooks import notify as _notify
        _notify('offer_accepted' if action == 'accept' else 'offer_declined', {
            'Client': partner.get('companyName'),
            'Offer': offer.get('offerNo', offer_id),
            'Signed': 'yes' if signature_ok else 'no',
            'Note': (note[:200] if note else '(none)'),
        })
    except Exception: pass

    try:
        from utils_email import _send_smtp
        subject = ('✅ Offer ACCEPTED' if action == 'accept' else '❌ Offer DECLINED') + f" — {partner.get('companyName')} · {offer.get('offerNo', offer_id)}"
        body = (
            f"Client:       {partner.get('companyName')}\n"
            f"Offer:        {offer.get('offerNo', offer_id)}\n"
            f"Response:     {action.upper()} at {now_iso}\n"
            f"Client note:  {note or '(none)'}\n"
        )
        try: _send_smtp(subject=subject, body=body)
        except Exception: pass
    except Exception:
        pass

    return jsonify({"status": "success", "clientStatus": offer.get('clientStatus'),
                    "at": offer.get('clientAcceptedAt') or offer.get('clientDeclinedAt')})


@portal_bp.route('/api/portal/admin/offers/mark_seen/<offer_id>', methods=['POST'])
@login_required
def admin_mark_offer_response_seen(offer_id):
    """Admin klikne 'Mark seen' na notifikaciji da klijent odgovorio na ponudu.
    Skida offer.adminReviewedByClient flag → notifikacija nestaje sa dashboard-a."""
    denied = require_portal_admin()
    if denied: return denied
    offer_row = _dl_select_one('offers', {'id': offer_id}) or {}
    if not offer_row:
        return jsonify({"error": "OFFER_NOT_FOUND"}), 404
    offer = store._entity_join(offer_row) if hasattr(store, '_entity_join') else dict(offer_row)
    offer['adminReviewedByClient'] = True
    offer['clientResponseReviewedAt'] = _iso_now()
    offer['clientResponseReviewedBy'] = session.get('username', 'admin')
    store.upsert_entity('offers', offer)
    log_audit('EDIT', 'portal', f'Admin acknowledged client response on offer {offer_id}', is_suspicious=False)
    return jsonify({"status": "success"})


# ==========================================================
#  CRM DASHBOARD: brojači pending stavki iz portala (za notifikacije)
# ==========================================================

@portal_bp.route('/api/portal/admin/activity', methods=['GET'])
@login_required
def admin_portal_activity():
    """Vraća listu događaja iz portala (client login-i, KYC, upload-i, prihvatanja
    ponuda, preuzimanje dokumenata, izmene profila) — RAZDVOJENO od CRM audit-a.
    Filteri: partner_id, action, start/end (ISO), limit (default 200, max 1000)."""
    denied = require_portal_admin()
    if denied: return denied

    partner_filter = request.args.get('partner_id') or None
    action_filter = request.args.get('action') or None
    start = request.args.get('start') or None
    end = request.args.get('end') or None
    try:
        limit = max(1, min(int(request.args.get('limit', 200)), 1000))
    except (TypeError, ValueError):
        limit = 200

    filters = {}
    if partner_filter:
        filters['partner_id'] = partner_filter
    if action_filter:
        filters['action'] = action_filter
    if start:
        filters['timestamp'] = ('gte', start)

    try:
        rows = _dl_select('portal_activity_log',
                          filters=filters or None,
                          order='-timestamp', limit=limit) or []
    except Exception as _e:
        logger.info(f'admin_portal_activity query failed: {_e}')
        rows = []
    # PostgREST prima jedan op po koloni preko tuple-a — drugi uslov filtriramo lokalno.
    if end:
        rows = [r for r in rows if str(r.get('timestamp') or '') <= end]

    partners_map = _partner_name_map()
    # S obzirom da `partners_map` sadrzi samo {id: companyName}, ali admin endpoint
    # treba i email + country — prosirimo mapu ručno.
    partner_details = {}
    try:
        for p in store.list_entities('partners'):
            pid = p.get('id')
            if pid:
                partner_details[pid] = {
                    'name': p.get('companyName', 'Unknown'),
                    'email': (p.get('contact', {}) or {}).get('email') or p.get('email', ''),
                    'country': (p.get('address', {}) or {}).get('country', ''),
                }
    except Exception:
        pass

    distinct_actions = sorted({r.get('action') for r in rows if r.get('action')})
    result_rows = []
    for r in rows:
        pid = r.get('partner_id') or ''
        pinfo = partner_details.get(pid, {})
        result_rows.append({
            "id": r.get('id'),
            "partner_id": pid,
            "partner_name": pinfo.get('name', partners_map.get(pid, 'Unknown')),
            "partner_email": pinfo.get('email', ''),
            "partner_country": pinfo.get('country', ''),
            "action": r.get('action'),
            "details": r.get('details'),
            "ip_address": r.get('ip_address'),
            "user_agent": r.get('user_agent'),
            "location": r.get('location') or 'N/A',
            "timestamp": r.get('timestamp')
        })

    return jsonify({
        "rows": result_rows,
        "meta": {
            "total_returned": len(result_rows),
            "limit": limit,
            "distinct_actions": distinct_actions
        }
    })


@portal_bp.route('/api/portal/admin/activity/stats', methods=['GET'])
@login_required
def admin_portal_activity_stats():
    """Agregat: broj login-a, KYC, upload-a, preuzetih dokumenata, RFQ-a
    po partneru u zadnjih 30 dana. Za dashboard tab."""
    denied = require_portal_admin()
    if denied: return denied

    from datetime import timedelta as _td
    cutoff = (datetime.now(timezone.utc) - _td(days=30)).isoformat().replace('+00:00', 'Z')

    try:
        rows = _dl_select('portal_activity_log',
                          filters={'timestamp': ('gte', cutoff)},
                          columns='partner_id,action',
                          limit=100000) or []
    except Exception as _e:
        logger.info(f'admin_portal_activity_stats query failed: {_e}')
        rows = []

    agg = {}
    for r in rows:
        pid = r.get('partner_id') or 'UNKNOWN'
        action = r.get('action') or ''
        key = pid
        agg.setdefault(key, {'logins': 0, 'kyc': 0, 'uploads': 0, 'downloads': 0, 'rfq': 0, 'offers_accepted': 0, 'total': 0})
        agg[key]['total'] += 1
        if action == 'LOGIN_SUCCESS': agg[key]['logins'] += 1
        elif action == 'KYC_SUBMIT': agg[key]['kyc'] += 1
        elif action == 'RFQ_SUBMIT': agg[key]['rfq'] += 1
        elif action in ('DOCUMENT_DOWNLOAD', 'DOCUMENT_PREVIEW'): agg[key]['downloads'] += 1
        elif action == 'PRODUCT_SUBMIT': agg[key]['uploads'] += 1
        elif action == 'OFFER_ACCEPT': agg[key]['offers_accepted'] += 1

    names = _partner_name_map()

    return jsonify([
        {'partner_id': pid, 'partner_name': names.get(pid, 'Unknown'), **v}
        for pid, v in sorted(agg.items(), key=lambda x: -x[1]['total'])
    ])


@portal_bp.route('/api/portal/admin/pending_counts', methods=['GET'])
@login_required
def admin_portal_pending_counts():
    """Vraća brojeve pending stavki iz portala (KYC, roba, izmene profila, RFQ)
    kako bi CRM dashboard prikazao admin badge/upozorenja."""
    denied = require_portal_admin()
    if denied: return denied

    counts = {"kyc": 0, "products": 0, "profile_requests": 0, "rfqs": 0, "offer_responses": 0, "offer_responses_detail": []}
    try:
        counts["kyc"] = int(_dl_count('kyc_submissions') or 0)
        counts["products"] = int(_dl_count('portal_products', filters={'status': 'pending'}) or 0)
        try:
            counts["profile_requests"] = int(_dl_count('profile_change_requests', filters={'status': 'pending'}) or 0)
        except Exception:
            counts["profile_requests"] = 0
    except Exception as _e:
        logger.info(f'admin_portal_pending_counts portal tables failed: {_e}')

    # RFQ (potraživnje) iz portala + neviđeni odgovori na ponude
    try:
        demands = store.list_entities('demands')
        rfq_pending = 0
        for d in demands:
            if isinstance(d, dict) and (d.get('source') or '').startswith('B2B Portal') and d.get('status') == 'pending':
                rfq_pending += 1
        counts["rfqs"] = rfq_pending

        # Client offer response feed — svaki accept/decline za koji admin nije
        # kliknuo 'Mark seen' pojavljuje se kao stavka u obaveštenjima.
        partner_names = _partner_name_map()
        for o in store.list_entities('offers'):
            if not isinstance(o, dict): continue
            if o.get('clientStatus') in ('accepted', 'declined') and o.get('adminReviewedByClient') is False:
                counts["offer_responses_detail"].append({
                    "offer_id": o.get('id'),
                    "offer_no": o.get('offerNo', ''),
                    "client_name": partner_names.get(o.get('customerId'), 'Unknown'),
                    "status": o.get('clientStatus'),
                    "note": (o.get('clientNote') or '')[:400],
                    "at": o.get('clientResponseAt') or o.get('clientDeclinedAt') or o.get('clientAcceptedAt')
                })
        counts["offer_responses"] = len(counts["offer_responses_detail"])
    except Exception as _e:
        logger.info(f'admin_portal_pending_counts CRM tables failed: {_e}')

    counts["total"] = (counts["kyc"] + counts["products"] + counts["profile_requests"]
                       + counts["rfqs"] + counts["offer_responses"])
    return jsonify(counts)


# ==========================================================
#  PORTAL HIDE/UNHIDE — client-side "brisanje" iz njegovog view-a
# ==========================================================
# Klijent može da ukloni sa svog portal view-a: (a) starije ponude koje su
# akceptovane/deklinirane, (b) dokumente koje više ne treba da vidi kada
# se posao završi. Zapisi ostaju u CRM-u (admin ih uvek vidi + audit log
# ima šta se dogodilo — ko je i kada sakrio). NE brišemo iz baze — samo
# beležimo per-partner "hidden" listu u portal_hidden_items tabeli.
#
# Supabase vec ima `portal_hidden_items` tabelu (vidi schemas/supabase_schema.sql)
# sa UNIQUE(partner_id, entity_type, entity_id), tako da `_ensure_hidden_items_schema`
# više nije potreban — uklonjen.

@portal_bp.route('/api/portal/hide/<token>', methods=['POST'])
def hide_portal_item(token):
    """Klijent sakriva jedan zapis (ponudu ili dokument) iz svog view-a.
    Payload: {entity_type: 'offer'|'document', entity_id: str}.
    NIJE hard delete — CRM strana i dalje vidi zapis + audit log beleži
    ko je i kada sakrio."""
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_session(token, auth_header):
        return jsonify({"error": "AUTH_REQUIRED"}), 401

    payload = request.get_json(silent=True) or {}
    entity_type = str(payload.get('entity_type', '')).strip().lower()
    entity_id = str(payload.get('entity_id', '')).strip()

    if entity_type not in ('offer', 'document'):
        return jsonify({"error": "INVALID_ENTITY_TYPE",
                        "message": "entity_type mora biti 'offer' ili 'document'"}), 400
    if not entity_id or len(entity_id) > 200:
        return jsonify({"error": "INVALID_ENTITY_ID"}), 400

    partner_id, _partner = _find_partner_by_token(token, enforce_active=True)
    if not partner_id:
        return jsonify({"error": "ACCESS_DENIED"}), 403

    now_iso = _iso_now()
    try:
        # UNIQUE(partner_id, entity_type, entity_id) — obrisemo prethodni unos
        # (ako postoji) pa insert-ujemo novi. Cisto i portabilno.
        _dl_delete('portal_hidden_items', {
            'partner_id': partner_id,
            'entity_type': entity_type,
            'entity_id': entity_id,
        })
        _dl_insert('portal_hidden_items', {
            'id': uuid.uuid4().hex,
            'partner_id': partner_id,
            'entity_type': entity_type,
            'entity_id': entity_id,
            'hidden_at': now_iso,
        })
    except Exception as e:
        return jsonify({"error": "SAVE_FAILED", "message": str(e)}), 500

    log_audit('INFO', 'portal',
              f'Client hid {entity_type} {entity_id} from portal view (partner {partner_id})',
              is_suspicious=False)
    log_portal_activity(partner_id, 'HIDE',
                        f'Hid {entity_type} {entity_id} from client portal view')
    return jsonify({"status": "success", "entity_type": entity_type, "entity_id": entity_id})


@portal_bp.route('/api/portal/unhide/<token>', methods=['POST'])
def unhide_portal_item(token):
    """Klijent vraća prethodno sakrivenu ponudu/dokument u view.
    Payload: {entity_type, entity_id}. Ako zapisa nema u hidden listi, no-op."""
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_session(token, auth_header):
        return jsonify({"error": "AUTH_REQUIRED"}), 401

    payload = request.get_json(silent=True) or {}
    entity_type = str(payload.get('entity_type', '')).strip().lower()
    entity_id = str(payload.get('entity_id', '')).strip()

    if entity_type not in ('offer', 'document') or not entity_id:
        return jsonify({"error": "INVALID_PAYLOAD"}), 400

    partner_id, _ = _find_partner_by_token(token, enforce_active=True)
    if not partner_id:
        return jsonify({"error": "ACCESS_DENIED"}), 403

    try:
        _dl_delete('portal_hidden_items', {
            'partner_id': partner_id,
            'entity_type': entity_type,
            'entity_id': entity_id,
        })
    except Exception as e:
        return jsonify({"error": "DELETE_FAILED", "message": str(e)}), 500

    log_portal_activity(partner_id, 'UNHIDE',
                        f'Restored {entity_type} {entity_id} to portal view')
    return jsonify({"status": "success"})


@portal_bp.route('/api/portal/hidden/<token>', methods=['GET'])
def list_hidden_items(token):
    """Vrati listu sakrivenih zapisa za trenutnog klijenta (koristi ga UI
    za dugme 'View hidden items')."""
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_session(token, auth_header):
        return jsonify({"error": "AUTH_REQUIRED"}), 401

    partner_id, _ = _find_partner_by_token(token, enforce_active=True)
    if not partner_id:
        return jsonify({"error": "ACCESS_DENIED"}), 403

    hidden = []
    try:
        rows = _dl_select('portal_hidden_items',
                          filters={'partner_id': partner_id},
                          order='-hidden_at', limit=500) or []
        for r in rows:
            hidden.append({
                "entity_type": r.get('entity_type'),
                "entity_id": r.get('entity_id'),
                "hidden_at": r.get('hidden_at'),
            })
    except Exception as _e:
        logger.info(f'list_hidden_items failed: {_e}')
    return jsonify({"hidden": hidden})


def _load_hidden_ids_for_partner(partner_id):
    """Interni helper — vraća set (entity_type, entity_id) tuple-ova koje je
    ovaj partner sakrio. Koristi ga get_portal_data u data.py da filtrira."""
    if not partner_id:
        return set()
    try:
        rows = _dl_select('portal_hidden_items',
                          filters={'partner_id': partner_id},
                          columns='entity_type,entity_id',
                          limit=5000) or []
        return {(r.get('entity_type'), r.get('entity_id')) for r in rows}
    except Exception as _e:
        logger.info(f'_load_hidden_ids_for_partner failed: {_e}')
        return set()


@portal_bp.route('/api/portal/admin/hidden_items', methods=['GET'])
@login_required
def admin_list_hidden_items():
    """Admin pregled — svi zapisi koje su klijenti sakrili sa svojih portal
    view-ova. Uz svaki: partner_id, entity_type, entity_id, hidden_at, i
    ime partnera (ako ga uspemo dobiti iz partners tabele).

    Namena: admin ima potpunu vidljivost — čak i "sakrivene" ponude klijent
    može da vidi kroz CRM ako mu admin pošalje ponovo email/notifikaciju."""
    if session.get('role') != 'admin':
        return jsonify({"error": "UNAUTHORIZED"}), 403

    partner_names = _partner_name_map()

    items = []
    try:
        rows = _dl_select('portal_hidden_items',
                          order='-hidden_at', limit=500) or []
        for r in rows:
            items.append({
                "partner_id": r.get('partner_id'),
                "partner_name": partner_names.get(r.get('partner_id'), 'Unknown'),
                "entity_type": r.get('entity_type'),
                "entity_id": r.get('entity_id'),
                "hidden_at": r.get('hidden_at'),
            })
    except Exception as _e:
        logger.info(f'admin_list_hidden_items failed: {_e}')

    return jsonify({"count": len(items), "items": items})


@portal_bp.route('/api/portal/admin/hidden_items/restore', methods=['POST'])
@login_required
def admin_restore_hidden_item():
    """Admin može da vrati ponudu/dokument u klijentov view (npr. ako je
    klijent slučajno sakrio nešto važno). Payload: {partner_id, entity_type,
    entity_id}."""
    if session.get('role') != 'admin':
        return jsonify({"error": "UNAUTHORIZED"}), 403
    payload = request.get_json(silent=True) or {}
    partner_id = str(payload.get('partner_id', '')).strip()
    entity_type = str(payload.get('entity_type', '')).strip().lower()
    entity_id = str(payload.get('entity_id', '')).strip()
    if not (partner_id and entity_type in ('offer', 'document') and entity_id):
        return jsonify({"error": "INVALID_PAYLOAD"}), 400
    try:
        _dl_delete('portal_hidden_items', {
            'partner_id': partner_id,
            'entity_type': entity_type,
            'entity_id': entity_id,
        })
    except Exception as e:
        return jsonify({"error": "DELETE_FAILED", "message": str(e)}), 500
    log_audit('INFO', 'portal',
              f'Admin restored hidden {entity_type} {entity_id} for partner {partner_id}',
              is_suspicious=False)
    return jsonify({"status": "success"})


# ==========================================================
#  BATCH D — NEW: Signed URL for portal files (Faza D prep)
# ==========================================================

@portal_bp.route('/api/portal/file/signed-url', methods=['GET'])
def portal_file_signed_url():
    """Za dati file_url (koji je vec autorizovan drugde), vrati privremeni
    Supabase Storage signed URL da klijent moze direktno da download-uje
    bez prolaska kroz Flask (skida load u velikoj kolicini).

    Ako je USE_SUPABASE_STORAGE=false → vraca original path (fallback na
    Flask serving).

    Query params:
      file_url — original URL (`/portal_uploads/<file>` ili slicno)
      ttl      — sekunde (default 300, max 3600)
    """
    file_url = (request.args.get('file_url') or '').strip()
    try:
        ttl = min(int(request.args.get('ttl') or 300), 3600)
    except ValueError:
        ttl = 300
    if not file_url:
        return jsonify({'error': 'file_url_required'}), 400
    if not (file_url.startswith('/portal_uploads/') or file_url.startswith('/uploads/')):
        return jsonify({'error': 'unsupported_url_prefix'}), 400

    try:
        import utils_storage as _st
    except Exception:
        return jsonify({'ok': False, 'reason': 'storage_module_missing', 'fallback_url': file_url})

    if not _st.use_supabase_storage():
        return jsonify({'ok': False, 'reason': 'storage_disabled', 'fallback_url': file_url})

    return jsonify({
        'ok': False,
        'reason': 'signed_url_mapping_not_available_yet',
        'hint': 'Storage mirror is best-effort; original URL is authoritative.',
        'fallback_url': file_url
    })
