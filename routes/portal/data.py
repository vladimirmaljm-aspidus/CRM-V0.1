"""V24.2 SUPABASE-ONLY — portal client data view (offers/deals/demands/...).

Sve čitanje/pipe ide kroz `data_layer` facade i `supabase_store` helper.
Nema `sqlite3.connect` poziva — portal vraća sveže podatke čim se CRM
upiše u Supabase (PythonAnywhere redeploy problem rešen).
"""
import json
import traceback
import logging
import uuid as _uuid
import re as _re
from datetime import datetime as _dt, timezone as _tz

from flask import request, jsonify, abort, render_template

from utils import decrypt_data, log_audit
from . import (portal_bp, safe_parse, check_portal_rate_limit,
               verify_portal_session, find_partner_by_token, log_portal_activity)

# V24.2 — Supabase-only pristup
from data_layer import select as _dl_select, insert as _dl_insert
import supabase_store as store

logger = logging.getLogger(__name__)

_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ==========================================================
#  HELPER: Supabase row → flat frontend dict
# ==========================================================
def _join(row):
    """Spoji top-level kolone + `data` JSONB u flat dict (defanzivno)."""
    return store._entity_join(row) if isinstance(row, dict) else {}


@portal_bp.route('/portal/login', methods=['GET'])
@portal_bp.route('/portal/', methods=['GET'])
def portal_login_page():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip: ip = ip.split(',')[0].strip()
    if not check_portal_rate_limit(ip):
        abort(429, description="DDoS Protection: Rate limit exceeded.")
    try:
        encrypted = store.get_setting('company')
        company = decrypt_data(encrypted) if encrypted else {}
        if not isinstance(company, dict): company = {}
    except Exception:
        company = {}
    return render_template('portal_login.html',
                           company_name=company.get('name', 'Aspidus'),
                           company_logo=company.get('logoUrl') or company.get('logoDataUrl', ''),
                           brand_color=company.get('brandColor', '#2563eb'))


@portal_bp.route('/portal/<token>', methods=['GET'])
def view_portal(token):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip: ip = ip.split(',')[0].strip()
    if not check_portal_rate_limit(ip):
        abort(429, description="DDoS Protection: Rate limit exceeded.")

    # Provera da li token uopste postoji u sistemu — ako ne, umesto praznog
    # portal.html-a koji potom radi 401, pokazi jasnu poruku.
    # NEW signature: find_partner_by_token(token, enforce_active=False) — bez cursor-a.
    partner_id, partner = find_partner_by_token(token, enforce_active=False)

    if not partner_id:
        log_audit('SECURITY', 'portal',
                  f'Invalid portal token access attempt from {ip}: {token[:12]}…',
                  is_suspicious=True)
        return render_template('portal_error.html',
                               title='Link Not Recognized',
                               reason='invalid_token',
                               message='This portal link is not recognized. It may have been mistyped, expired, or revoked. Please request a new link from your account manager.',
                               contact='Contact your Aspidus account manager for a fresh invitation.'), 404

    if partner.get('isPortalActive') is False:
        log_audit('SECURITY', 'portal',
                  f'Access attempt to revoked portal (partner {partner_id}) from {ip}',
                  is_suspicious=True)
        return render_template('portal_error.html',
                               title='Portal Access Suspended',
                               reason='revoked',
                               message='This account has been temporarily suspended. Please contact your account manager to restore access.',
                               contact=f'If you believe this is an error, reach out to compliance@aspidus.co.'), 403

    return render_template('portal.html', token=token)


@portal_bp.route('/api/portal/data/<token>', methods=['GET'])
def get_portal_data(token):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip: ip = ip.split(',')[0].strip()
    if not check_portal_rate_limit(ip): abort(429)

    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_session(token, auth_header):
        return jsonify({"error": "Authentication required", "require_otp": True}), 401

    # Ceo handler u outer try/except — bilo koja neuhvaćena greška se
    # loguje sa traceback-om umesto da klijent portala vidi šturu
    # 500 grešku bez razloga.
    try:
        # find_partner_by_token sprovodi Kill Switch (opozvan portal -> None)
        # NEW signature — bez cursor argumenta.
        partner_id, partner = find_partner_by_token(token, enforce_active=True)
        if not partner:
            log_audit('SECURITY', 'portal', f'Blocked data access (invalid or revoked portal token)', is_suspicious=True)
            return jsonify({"error": "Access Denied"}), 403

        # SYSTEM PERMISSIONS: Dozvoljavamo pristup i novom tabu za dokumente
        permissions = partner.get("portalPermissions", ["shipments", "offers", "kyc", "goods", "profile", "rfq", "documents"])

        # V23.1 #4 — VIEW ONLY OWN DOCS enforcement: admin je u
        # /admin/portal-permissions postavio flag; ako je True, sve liste
        # (offers/deals/documents/invoices/proformas) STROGO filtriramo po
        # partnerId. Default je True (bezbedno).
        _view_only_own = partner.get('viewOnlyOwnDocs', True)

        # Odbrambeni normalizatori: 'address' i 'contact' su nekad string, nekad dict.
        _addr = partner.get("address")
        _contact = partner.get("contact")
        _kyc = partner.get("kyc")
        _addr_dict = _addr if isinstance(_addr, dict) else {}
        _addr_str = _addr if isinstance(_addr, str) else _addr_dict.get("street", "")
        _contact_dict = _contact if isinstance(_contact, dict) else {}
        _kyc_dict = _kyc if isinstance(_kyc, dict) else {}

        # PREMIUM klijent — auto-approve KYC gate (frontend ne blokira feature-e).
        _is_premium = bool(partner.get('isPremium'))
        _real_kyc_status = _kyc_dict.get("status", "pending")
        _shown_kyc_status = 'approved' if _is_premium else _real_kyc_status

        safe_partner = {
            "id": partner_id,
            "companyName": partner.get("companyName") or partner.get("name") or "",
            "contactPerson": _contact_dict.get("person", "") or partner.get("contactPerson", ""),
            "kycStatus": _shown_kyc_status,
            "kycStatusReal": _real_kyc_status,
            "kycReviewNote": _kyc_dict.get("reviewNote", ""),
            "kycReviewedAt": _kyc_dict.get("reviewedAt", ""),
            "email": _contact_dict.get("email") or partner.get("email", "") or "",
            "phone": _contact_dict.get("phone") or partner.get("phone", "") or "",
            "address": {
                "street": _addr_str,
                "city": _addr_dict.get("city", "") or partner.get("city", "") or "",
                "country": _addr_dict.get("country", "") or partner.get("country", "") or "",
            },
            "permissions": permissions,
            "isPremium": _is_premium,
        }

        # ---- PRODUCT CATALOG (id -> name + full map za enrichment) ----
        # V24.2: data iz Supabase JSONB-a je već dict — NE pozivati safe_parse().
        products_map = {}
        products_full_map = {}
        try:
            for prow in (_dl_select('products') or []):
                pd = _join(prow)
                if isinstance(pd, dict) and pd.get('id'):
                    products_map[pd['id']] = pd.get('name')
                    products_full_map[pd['id']] = pd
        except Exception as e:
            logger.warning(f'get_portal_data: products load failed: {e}')

        # ---- COMPANY SETTINGS ----
        try:
            encrypted_company = store.get_setting('company')
            company_data = decrypt_data(encrypted_company) if encrypted_company else {}
            company = company_data if isinstance(company_data, dict) else {}
        except Exception:
            company = {}
        safe_company = {
            "name": company.get("name", "Aspidus"),
            "logoUrl": company.get("logoUrl") or company.get("logoDataUrl") or "",
            "brandColor": company.get("brandColor", "#2563eb"),
            "address": company.get("address", ""),
            "taxId": company.get("taxId", ""),
            "website": company.get("website", "")
        }

        # ---- HIDDEN ITEMS (klijent sakrio ponudu/dokument) ----
        try:
            from .actions import _load_hidden_ids_for_partner
            _hidden = _load_hidden_ids_for_partner(partner_id)
        except Exception:
            _hidden = set()

        # ---- DEALS ----
        # Filtriramo u Python-u da bismo podržali i buyerId i customerId
        # (stari zapisi možda imaju samo customerId u data JSONB-u jer
        # backfill buyer_id top-level kolone nije izvršen).
        safe_deals = []
        try:
            for d_row in (_dl_select('deals') or []):
                deal = _join(d_row)
                if deal.get('buyerId') == partner_id or deal.get('customerId') == partner_id:
                    safe_deals.append({
                        "id": deal.get("id"),
                        "contractId": deal.get("contractId"),
                        "productName": products_map.get(deal.get("productId"), "Commodity"),
                        "quantity": deal.get("quantity"),
                        "unit": deal.get("unit"),
                        "status": deal.get("status"),
                        "createdAt": deal.get("createdAt"),
                        "logistics": {
                            "pol": deal.get("logistics", {}).get("pol", "TBA"),
                            "pod": deal.get("logistics", {}).get("pod", "TBA"),
                            "vessel": deal.get("logistics", {}).get("vessel", "TBA"),
                            "blNumber": deal.get("logistics", {}).get("blNumber", "TBA"),
                            "shipmentDate": deal.get("logistics", {}).get("shipmentDate", "TBA")
                        }
                    })
        except Exception as e:
            logger.warning(f'get_portal_data: deals load failed: {e}')

        # ---- OFFERS ----
        # Filtriramo po customerId u Python-u (backward-compat sa starim
        # zapisima gde top-level customer_id kolona možda nije popunjena).
        safe_offers = []
        try:
            for o_row in (_dl_select('offers') or []):
                off = _join(o_row)
                if off.get('customerId') == partner_id and ('offer', str(off.get('id') or '')) not in _hidden:
                    pid = off.get("productId")
                    prod = products_full_map.get(pid, {}) if pid else {}
                    supply = (prod.get('supplyOffers') or [{}])[0] if prod else {}
                    safe_offers.append({
                        "id": off.get("id"),
                        "offerNo": off.get("offerNo"),
                        "date": off.get("date"),
                        "validUntil": off.get("validUntil"),
                        "productName": products_map.get(pid, "Commodity"),
                        "quantity": off.get("quantity"),
                        "unit": off.get("unit"),
                        "price": off.get("sellingPrice") or off.get("price"),
                        "currency": off.get("currency"),
                        "incoterm": off.get("incoterm"),
                        "hsCode": off.get("hsCode") or prod.get('hsCode'),
                        "productSpec": prod.get('detailedSpec') or prod.get('shortDescription'),
                        "detailedSpec": off.get('detailedSpec') or prod.get('detailedSpec'),
                        "packaging": off.get("packaging") or prod.get('packaging'),
                        "paymentTerms": off.get("paymentTerms"),
                        "pol": off.get("pol"), "pod": off.get("pod"),
                        "vessel": off.get("vessel"),
                        "leadTime": off.get("leadTime") or supply.get('leadTime') if supply else None,
                        "origin": off.get("origin") or supply.get('country') if supply else None,
                        "productOrigin": supply.get('country') if supply else None,
                        "notes": off.get("notes"),
                        "documentId": off.get("documentId") or off.get("pdfDocumentId"),
                        "clientStatus": off.get("clientStatus"),
                        "clientAcceptedAt": off.get("clientAcceptedAt"),
                        "convertedDealId": off.get("convertedDealId")
                    })
        except Exception as e:
            logger.warning(f'get_portal_data: offers load failed: {e}')

        # ---- DEMANDS ----
        my_demands = []
        try:
            for dem_row in (_dl_select('demands') or []):
                dem = _join(dem_row)
                if dem.get('customerId') == partner_id:
                    my_demands.append({
                        "id": dem.get("id"),
                        "productName": dem.get("productName", "Commodity"),
                        "quantity": dem.get("quantity"),
                        "targetPrice": dem.get("targetPrice"),
                        "status": dem.get("status", "pending"),
                        "date": dem.get("date")
                    })
        except Exception as e:
            logger.warning(f'get_portal_data: demands load failed: {e}')

        # =========================================================
        # FAZA 2: INTEGRACIJA TREZORA DOKUMENATA (DOCUMENT VAULT)
        # =========================================================
        documents = []
        try:
            for doc_row in (_dl_select('shared_documents') or []):
                d = _join(doc_row)
                if d.get('partnerId') == partner_id and ('document', str(d.get('id') or '')) not in _hidden:
                    documents.append(d)
            documents.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
        except Exception as e:
            logger.warning(f'get_portal_data: shared_documents load failed: {e}')

        # V23.1 #4 + #5 — invoices i proforme izdate ovom partneru.
        # STROGO filtrirano po partnerId; view_only_own_docs je uvek primenjen.
        safe_invoices = []
        safe_proformas = []
        try:
            for i_row in (_dl_select('invoices') or []):
                inv = _join(i_row)
                if inv.get('partnerId') == partner_id or inv.get('customerId') == partner_id:
                    safe_invoices.append({
                        'id': inv.get('id'),
                        'docNumber': inv.get('docNumber'),
                        'versionLabel': inv.get('versionLabel', 'V1'),
                        'issueDate': inv.get('issueDate'),
                        'dueDate': inv.get('dueDate'),
                        'currency': inv.get('currency'),
                        'items': inv.get('items') or [],
                        'notes': inv.get('notes'),
                        'status': inv.get('status', 'draft'),
                    })
        except Exception as e:
            logger.warning(f'get_portal_data: invoices load failed: {e}')
        try:
            for p_row in (_dl_select('proformas') or []):
                pf = _join(p_row)
                if pf.get('partnerId') == partner_id or pf.get('customerId') == partner_id:
                    safe_proformas.append({
                        'id': pf.get('id'),
                        'docNumber': pf.get('docNumber'),
                        'versionLabel': pf.get('versionLabel', 'V1'),
                        'issueDate': pf.get('issueDate'),
                        'dueDate': pf.get('dueDate'),
                        'currency': pf.get('currency'),
                        'items': pf.get('items') or [],
                        'notes': pf.get('notes'),
                        'status': pf.get('status', 'draft'),
                    })
        except Exception as e:
            logger.warning(f'get_portal_data: proformas load failed: {e}')

        _ = _view_only_own  # (semantika: ovaj flag je vec efektivan gore)

        # ---- KYC SUBMISSIONS (latest) ----
        # Napomena: `data` kolona je JSONB u Supabase-u, ali se stare
        # kriptovane vrednosti (Fernet string) skladište kao JSON string
        # vrednost. Zato: ako je dict → koristi direktno; ako je str → decrypt.
        latest_kyc = None
        try:
            kyc_rows = _dl_select('kyc_submissions',
                                 filters={'partner_id': ('eq', partner_id)},
                                 order='-submitted_at', limit=1) or []
            if kyc_rows:
                kyc_data = kyc_rows[0].get('data')
                if isinstance(kyc_data, str):
                    latest_kyc = decrypt_data(kyc_data)
                elif isinstance(kyc_data, dict):
                    latest_kyc = kyc_data
                elif kyc_data is not None:
                    latest_kyc = kyc_data
        except Exception as e:
            logger.warning(f'get_portal_data: kyc_submissions load failed: {e}')

        # ---- PORTAL PRODUCTS (klijentov katalog) ----
        my_products = []
        try:
            for p_row in (_dl_select('portal_products',
                                     filters={'partner_id': ('eq', partner_id)},
                                     order='-created_at') or []):
                pp_data = p_row.get('data')
                # `data` je JSONB u Supabase; ako dodje kao str (stari zapis),
                # probaj safe_parse.
                if not isinstance(pp_data, dict):
                    pp_data = safe_parse(pp_data) if pp_data else {}
                my_products.append({
                    "id": p_row.get('id'),
                    "data": pp_data,
                    "status": p_row.get('status'),
                    "created_at": p_row.get('created_at')
                })
        except Exception as e:
            logger.warning(f'get_portal_data: portal_products load failed: {e}')

        # ---- PROFILE CHANGE REQUESTS (moji zahtevi za izmenu profila) ----
        my_profile_requests = []
        try:
            for r in (_dl_select('profile_change_requests',
                                 filters={'partner_id': ('eq', partner_id)},
                                 order='-submitted_at', limit=10) or []):
                rdata = r.get('data')
                if not isinstance(rdata, dict):
                    rdata = safe_parse(rdata) if rdata else {}
                my_profile_requests.append({
                    "id": r.get('id'),
                    "changes": rdata,
                    "status": r.get('status'),
                    "submitted_at": r.get('submitted_at'),
                    "reviewed_at": r.get('reviewed_at')
                })
        except Exception as e:
            logger.warning(f'get_portal_data: profile_change_requests load failed: {e}')

        return jsonify({
            "partner": safe_partner,
            "company": safe_company,
            "deals": sorted(safe_deals, key=lambda x: x.get('createdAt') or '', reverse=True),
            "offers": sorted(safe_offers, key=lambda x: x.get('date') or '', reverse=True),
            "my_demands": sorted(my_demands, key=lambda x: x.get('date') or '', reverse=True),
            "documents": documents,
            "invoices": safe_invoices,       # V23.1 #4/#5
            "proformas": safe_proformas,     # V23.1 #4/#5
            "latest_kyc": latest_kyc,
            "my_products": my_products,
            "my_profile_requests": my_profile_requests
        })
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"get_portal_data failed for token {token[:8]}...: {e}\n{tb}")
        log_audit('ERROR', 'portal', f'get_portal_data crashed: {e}', is_suspicious=False)
        return jsonify({"error": "PORTAL_DATA_ERROR", "message": str(e)}), 500


@portal_bp.route('/api/portal/profile/update/<token>', methods=['POST'])
def submit_profile_change_request(token):
    """Klijent portala predlaže izmenu svojih kontakt podataka (email, telefon,
    kontakt osoba, adresa). Izmena se NE primenjuje odmah — čuva se kao 'pending'
    zahtev; admin je odobrava iz CRM-a, tek onda se preslikava u partner profil.
    Time se sprečava da klijent bez nadzora promeni komunikacione kanale."""
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_session(token, auth_header): abort(401)

    payload = request.get_json(silent=True) or {}

    # Sanitizacija i validacija — samo dozvoljena polja
    email = str(payload.get('email', '')).strip()[:150]
    phone = str(payload.get('phone', '')).strip()[:50]
    person = str(payload.get('contactPerson', '')).strip()[:150]
    street = str(payload.get('street', '')).strip()[:200]
    city = str(payload.get('city', '')).strip()[:100]
    country = str(payload.get('country', '')).strip()[:100]
    note = str(payload.get('note', '')).strip()[:500]

    if email and not _EMAIL_RE.match(email):
        return jsonify({"error": "INVALID_EMAIL_FORMAT"}), 400
    if not any([email, phone, person, street, city, country]):
        return jsonify({"error": "NO_CHANGES_PROVIDED"}), 400

    # NEW signature — bez cursor argumenta.
    partner_id, partner_record = find_partner_by_token(token, enforce_active=True)
    if not partner_record:
        return jsonify({"error": "Partner not found"}), 404

    changes = {}
    if email: changes['email'] = email
    if phone: changes['phone'] = phone
    if person: changes['contactPerson'] = person
    if street: changes['street'] = street
    if city: changes['city'] = city
    if country: changes['country'] = country
    if note: changes['note'] = note

    req_id = _uuid.uuid4().hex
    now_iso = _dt.now(_tz.utc).isoformat().replace('+00:00', 'Z')

    # V24.2: direktno upisujemo u Supabase preko data_layer facade.
    # `data` kolona je JSONB — šaljemo dict direktno (ne json.dumps).
    _dl_insert('profile_change_requests', {
        'id': req_id,
        'partner_id': partner_id,
        'data': changes,
        'status': 'pending',
        'submitted_at': now_iso,
    })

    log_audit('CREATE', 'portal', f'Profile change request submitted by partner {partner_id}: {list(changes.keys())}', is_suspicious=False)
    return jsonify({"status": "success", "message": "Zahtev je poslat administratoru na odobrenje.", "request_id": req_id})


# ==========================================================
#  HEARTBEAT — vraća koliko još sesija traje
# ==========================================================

@portal_bp.route('/api/portal/heartbeat/<token>', methods=['GET'])
def portal_heartbeat(token):
    """Vraća koliko sekundi je preostalo do isteka portal sesije.

    Klijent poziva ovo svakih 60s da bi:
      1) resetovao `last_active` (ova ruta prolazi kroz verify_portal_session
         koji stampuje `last_active = now`, pa produžava inactivity prozor);
      2) dobio `remaining_seconds` — koristi ga za warning modal 90s pre isteka.

    Ne otkriva ništa što auth_key već ne omogućava — samo TTL brojač.
    """
    import time as _time
    from . import portal_auth_sessions, _fw_ttl, PORTAL_INACTIVITY_TTL

    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_session(token, auth_header):
        return jsonify({"error": "session_invalid"}), 401

    sess = portal_auth_sessions.get(token) or {}
    now = _time.time()
    exp_abs = float(sess.get('expires', 0))
    inactivity_ttl = _fw_ttl('portal_inactivity', PORTAL_INACTIVITY_TTL)
    last_active = float(sess.get('last_active', now))

    remaining_abs = max(0, int(exp_abs - now))
    remaining_inactivity = max(0, int(inactivity_ttl - (now - last_active)))
    # Warning treba da ide na osnovu one kraće koja će je stvarno oboriti
    remaining = min(remaining_abs, remaining_inactivity) if exp_abs else remaining_inactivity

    return jsonify({
        "ok": True,
        "remaining_seconds": remaining,
        "remaining_absolute": remaining_abs,
        "remaining_inactivity": remaining_inactivity,
        "inactivity_ttl": inactivity_ttl,
    })


# ==========================================================
#  TASK #41: BIDIRECTIONAL KYC SYNC — PREFILL FROM CRM
# ==========================================================
# Portal klijent kad otvori KYC formu, vidi trenutno poznata polja
# iz CRM-a (ako je admin nesto vec upisao ili je klijent ranije poslao
# KYC koji je odobren). Klijent moze da menja/dopunjuje pre submita.
#
# Ovo je "single source of truth" — CRM je autoritativan; portal je view.
# Novi KYC submit pravi profile_change_request za polja koja su promenjena.

@portal_bp.route('/api/portal/kyc/prefill/<token>', methods=['GET'])
def portal_kyc_prefill(token):
    """Vrati trenutno poznata partner polja u obliku pogodnom za KYC formu.
    Sluzi za pre-fill — klijent onda edituje samo sta treba."""
    auth_header = request.headers.get('X-Portal-Auth')
    if not verify_portal_session(token, auth_header):
        return jsonify({"error": "session_invalid"}), 401

    # NEW signature — bez cursor argumenta.
    partner_id, partner = find_partner_by_token(token, enforce_active=True)
    if not partner:
        return jsonify({"error": "not_found"}), 404

    kyc = partner.get('kyc') or {}
    contact = partner.get('contact') if isinstance(partner.get('contact'), dict) else {}
    addr = partner.get('address') if isinstance(partner.get('address'), dict) else {}

    prefill = {
        'companyName':   partner.get('companyName') or '',
        'regNo':         kyc.get('regNo')   or partner.get('regNo')   or '',
        'taxId':         kyc.get('taxId')   or partner.get('taxId')   or '',
        'website':       kyc.get('website') or partner.get('website') or '',
        'industry':      kyc.get('industry') or '',
        'contactPhone':  contact.get('phone') or partner.get('phone') or '',
        'contactEmail':  contact.get('email') or partner.get('email') or '',
        'contactPerson': contact.get('person') or '',
        'address':       addr.get('street') if addr.get('street') else (partner.get('address') if isinstance(partner.get('address'), str) else ''),
        'city':          addr.get('city') or '',
        'country':       addr.get('country') or '',
        'bankName':      (kyc.get('bank') or {}).get('name') or partner.get('bankName') or '',
        'bankIban':      (kyc.get('bank') or {}).get('iban') or partner.get('accountNumber') or '',
        'bankSwift':     (kyc.get('bank') or {}).get('swift') or partner.get('swift') or '',
        'kycStatus':     kyc.get('status') or 'pending',
        'entityType':    kyc.get('entityType') or 'company',
        'directors':     kyc.get('directors') or [],
        'ubos':          kyc.get('ubos') or [],
        'files':         kyc.get('files') or {},
    }

    return jsonify({
        "ok": True,
        "prefill": prefill,
        "readonly_kyc_status": prefill['kycStatus'] == 'approved',
        "hint": "These fields are pre-filled from your CRM record. Edit and re-submit to request an update.",
    })
