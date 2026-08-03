"""
V23.1 admin blueprints — sve u jednoj datoteci radi lakseg wire-a:

  #3 Granular internal permissions (matrica po korisniku + backend gate)
  #4 Portal permissions per-partner UI
  #6 Document Register enhanced page
  #7 Conversion Offer→Invoice/Proforma helper endpoint

Sve rute su admin-only osim gde je jasno drukcije oznaceno.

V25 SUPABASE-ONLY — svi DB pozivi idu kroz `data_layer` facade ili
`supabase_store` helper. Nema vise `sqlite3.connect(...)` poziva — podaci
prezive PythonAnywhere redeploy jer zive u Supabase Postgres-u.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, session, render_template

from utils import login_required, log_audit
import supabase_store as store
from data_layer import (select as _dl_select, select_one as _dl_select_one,
                        insert as _dl_insert, update as _dl_update,
                        upsert as _dl_upsert, delete as _dl_delete,
                        count as _dl_count)

logger = logging.getLogger(__name__)

v23_admin_bp = Blueprint('v23_admin_bp', __name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _admin_only():
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    return None


# =========================================================================
#  V23.1 #3 — GRANULARNE PERMISIJE (permissions matrix)
# =========================================================================
#
# Sve "feature keys" u sistemu — objasnjeno frontend-u da admin vidi lep opis.
# NOVE OPCIJE dodavaj OVDE i one ce automatski biti dostupne u matrici.
PERMISSION_CATALOG = {
    # Partneri
    "partners.view":    "Vidi listu partnera i njihove osnovne informacije",
    "partners.create":  "Dodaje nove partnere",
    "partners.edit":    "Menja podatke o postojećim partnerima",
    "partners.delete":  "Briše partnere iz sistema",
    "partners.kyc_review": "Pregleda i odobrava KYC dokumenta",
    "partners.portal_manage": "Kreira i deaktivira portalne linkove za partnere",
    # Proizvodi
    "products.view":    "Vidi katalog proizvoda",
    "products.create":  "Dodaje nove proizvode",
    "products.edit":    "Menja specifikacije proizvoda",
    "products.delete":  "Briše proizvode",
    "products.price_edit": "Menja cene i uslove plaćanja",
    # Deals / Sales
    "deals.view":       "Vidi listu deals",
    "deals.create":     "Kreira nove deals",
    "deals.edit":       "Menja postojeće deals",
    "deals.delete":     "Briše deals",
    "deals.pipeline":   "Vidi Kanban pipeline sve prodajnog tima",
    # Ponude / Offers
    "offers.view":      "Vidi ponude",
    "offers.create":    "Kreira nove ponude",
    "offers.edit":      "Menja ponude (pravi novu verziju)",
    "offers.send":      "Šalje ponudu klijentu (email + portal)",
    "offers.accept":    "Prihvata ponude u ime klijenta (admin override)",
    # Invoices / Proforme
    "invoices.view":    "Vidi izdate fakture",
    "invoices.create":  "Izdaje nove fakture",
    "invoices.void":    "Poništava izdate fakture",
    "proformas.view":   "Vidi proforme",
    "proformas.create": "Izdaje proforme",
    # Finansije
    "finance.accounts": "Vidi i menja bankovne račune firme",
    "finance.transactions": "Beleži uplate/isplate",
    "finance.recurring": "Podešava mesečne troškove",
    # Reporting
    "reports.view":     "Otvara Custom Reports (kome je share-ovano)",
    "reports.create":   "Kreira nove Custom Reports",
    "reports.sql":      "Piše sirov SQL u Report Builder-u",
    # Logistika
    "logistics.view":   "Otvara logistički kalkulator",
    "logistics.export": "Izvozi logističke planove kao PDF/XLSX",
    # Admin
    "admin.users":      "Kreira i menja korisnike CRM-a",
    "admin.permissions": "Menja permisije drugih korisnika (nemoj bez razloga davati)",
    "admin.settings":   "Menja globalne postavke firme i integracije",
    "admin.audit":      "Otvara Audit Log i pregled sigurnosnih incidenata",
    "admin.supabase":   "Otvara Operations Center (Supabase migracija, sync-back)",
    "admin.errors":     "Vidi Error Log servera",
    "admin.mail_queue": "Upravlja email queue (retry/purge)",
    "admin.backups":    "Backup Center — pokreće i vraća backup-e",
    # Portal management
    "portal.manage":    "Upravlja portalnim pristupom klijenata (kreiranje/opoziv)",
    "portal.impersonate": "Vidi portal onako kako ga klijent vidi (view-as)",
}


def _normalize_perms(v):
    """permissions polje je sada JSONB — normalizuje string/dict/None u dict."""
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            d = json.loads(v)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


@v23_admin_bp.route('/api/admin/permissions/catalog', methods=['GET'])
@login_required
def perm_catalog():
    """Vraca ceo katalog permisija za matricu UI."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    # Group by prefix
    groups = {}
    for k, desc in PERMISSION_CATALOG.items():
        prefix = k.split('.')[0]
        groups.setdefault(prefix, []).append({'key': k, 'description': desc})
    return jsonify({'groups': groups, 'total': len(PERMISSION_CATALOG)})


@v23_admin_bp.route('/api/admin/permissions/users', methods=['GET'])
@login_required
def perm_users_list():
    """Lista svih user-a sa role i njihovim permission mapom."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    try:
        rows = _dl_select('users',
                         columns='id,username,role,permissions,full_name,email') or []
    except Exception as _e:
        logger.warning(f'perm_users_list: select failed: {_e}')
        rows = []
    # Sort: admins first, then by username ASC
    rows_sorted = sorted(rows,
                         key=lambda r: (str(r.get('role', '')).lower() != 'admin',
                                        str(r.get('username', '')).lower()))
    out = []
    for r in rows_sorted:
        perms = _normalize_perms(r.get('permissions'))
        # Admin ima sve, ne prikazuje se checkbox
        out.append({
            'id': r.get('id'), 'username': r.get('username'),
            'role': r.get('role'),
            'full_name': r.get('full_name') or '',
            'email': r.get('email') or '',
            'permissions': perms,
        })
    return jsonify({'users': out})


@v23_admin_bp.route('/api/admin/permissions/user/<uid>', methods=['POST'])
@login_required
def perm_user_update(uid):
    """Atomicki apdejtuje permissions dict za jednog user-a.
    Prihvata { "permissions": { "partners.view": true, "products.edit": false, ... } }
    Kljucevi koji NISU u katalogu se ignorisu (sprecava zagadjenje)."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    body = request.get_json(silent=True) or {}
    incoming = body.get('permissions') or {}
    if not isinstance(incoming, dict):
        return jsonify({'error': 'permissions_must_be_object'}), 400

    # Filtriraj samo poznate kljuceve
    cleaned = {k: bool(v) for k, v in incoming.items() if k in PERMISSION_CATALOG}

    try:
        # users.permissions je JSONB kolona — prosledjujemo dict direktno
        _dl_update('users', {'id': uid}, {'permissions': cleaned})
    except Exception as _e:
        logger.error(f'perm_user_update({uid}) failed: {_e}')
        return jsonify({'error': 'update_failed', 'message': str(_e)}), 500

    # Invalidiraj sve sesije tog user-a (nove permisije stupe na snagu odmah)
    try:
        from utils import bump_user_token_version
        bump_user_token_version(uid)
    except Exception:
        pass

    log_audit('EDIT', 'permissions',
              f'Admin updated permissions for user {uid}: {len(cleaned)} keys',
              is_suspicious=False)
    return jsonify({'updated': True, 'saved_keys': len(cleaned)})


@v23_admin_bp.route('/admin/permissions', methods=['GET'])
@login_required
def perm_admin_page():
    if session.get('role') != 'admin':
        return "Admin only.", 403
    return render_template('admin_permissions.html')


def has_permission(uid: str, key: str) -> bool:
    """Server-side helper — proveri da li user ima dati kljuc.
    Admin uvek True. Ako je user u users tabeli, gledamo permissions polje."""
    try:
        user = store.get_user_by_id(uid)
        if not user:
            return False
        if user.get('role') == 'admin':
            return True
        perms = _normalize_perms(user.get('permissions'))
        return bool(perms.get(key))
    except Exception:
        return False


# =========================================================================
#  V23.1 #4 — PORTAL PERMISSIONS per-partner
# =========================================================================
#
# Portal shema podržava portalPermissions listu u partner data JSON-u
# (koristi je routes/portal/data.py). Ovo je admin UI + endpoint da se
# lako podesava po partneru.

PORTAL_MODULES = {
    'overview':   'Pregled naloga (dashboard portal)',
    'shipments':  'Praćenje pošiljki',
    'offers':     'Ponude i cene',
    'invoices':   'Fakture i proforme',
    'kyc':        'KYC forma i status',
    'goods':      'Katalog robe',
    'documents':  'Zajednički dokumenti (kontratkti itd.)',
    'profile':    'Uređivanje profila firme',
    'rfq':        'Slanje potražnji (RFQ)',
    'notifications': 'Notifikacije u portalu',
}


@v23_admin_bp.route('/api/admin/portal/modules', methods=['GET'])
@login_required
def portal_modules_catalog():
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    return jsonify({'modules': PORTAL_MODULES})


@v23_admin_bp.route('/api/admin/portal/permissions/<pid>', methods=['GET'])
@login_required
def portal_perm_get(pid):
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    partner = store.get_entity('partners', pid)
    if not partner:
        return jsonify({'error': 'partner_not_found'}), 404
    enabled = partner.get('portalPermissions') or list(PORTAL_MODULES.keys())
    return jsonify({
        'partner_id': pid,
        'partner_name': partner.get('name') or partner.get('companyName') or '?',
        'enabled_modules': enabled,
        'is_portal_active': partner.get('isPortalActive',
                                        partner.get('is_portal_active', True)),
        'is_premium': bool(partner.get('isPremium',
                                       partner.get('is_premium', False))),
        'view_only_own_docs': bool(partner.get('viewOnlyOwnDocs', True)),
    })


@v23_admin_bp.route('/api/admin/portal/permissions/<pid>', methods=['POST'])
@login_required
def portal_perm_set(pid):
    """Postavlja portalPermissions listu + druge portal flag-ove za partnera."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    body = request.get_json(silent=True) or {}

    partner = store.get_entity('partners', pid)
    if not partner:
        return jsonify({'error': 'partner_not_found'}), 404

    # Modules — filtriraj samo poznate
    if 'enabled_modules' in body and isinstance(body['enabled_modules'], list):
        valid = [m for m in body['enabled_modules'] if m in PORTAL_MODULES]
        partner['portalPermissions'] = valid

    if 'is_portal_active' in body:
        partner['isPortalActive'] = bool(body['is_portal_active'])
        # Sinhronizuj i snake_case top-level kolonu
        partner['is_portal_active'] = bool(body['is_portal_active'])
    if 'is_premium' in body:
        partner['isPremium'] = bool(body['is_premium'])
        partner['is_premium'] = bool(body['is_premium'])
    if 'view_only_own_docs' in body:
        partner['viewOnlyOwnDocs'] = bool(body['view_only_own_docs'])

    try:
        store.upsert_entity('partners', partner)
    except Exception as _e:
        logger.error(f'portal_perm_set({pid}) upsert failed: {_e}')
        return jsonify({'error': 'save_failed', 'message': str(_e)}), 500

    log_audit('EDIT', 'portal_perms',
              f'Admin updated portal permissions for partner {pid}: {list(body.keys())}')
    return jsonify({'updated': True})


@v23_admin_bp.route('/admin/portal-permissions', methods=['GET'])
@login_required
def portal_perm_page():
    if session.get('role') != 'admin':
        return "Admin only.", 403
    return render_template('admin_portal_permissions.html')


# =========================================================================
#  V23.1 #6 — DOCUMENT REGISTER (Knjiga izdatih dokumenata)
# =========================================================================
#
# Tabela document_register vec postoji. Ovaj endpoint dodaje pregled sa:
#   docNumber, klijent, datum izdavanja, ko je izdao, verzija (revision),
#   status, quick-link ka samom dokumentu.

def _next_seq(doc_type_upper, year):
    """Vraca sledeci seq broj za dati doc_type i godinu.
    PostgREST ne podrzava MAX() direktno, ali sortiramo DESC po seq i uzimamo 1."""
    try:
        rows = _dl_select('document_register',
                          filters={'doc_type': doc_type_upper, 'year': year},
                          columns='seq', order='-seq', limit=1) or []
        if rows:
            return int(rows[0].get('seq', 0)) + 1
    except Exception as _e:
        logger.info(f'_next_seq({doc_type_upper},{year}) failed: {_e}')
    return 1


def _latest_revision(doc_number):
    """Vraca (revision, content_hash) za najnoviju reviziju tog broja, ili (None, None)."""
    try:
        rows = _dl_select('document_revisions',
                          filters={'doc_number': doc_number},
                          columns='revision,content_hash',
                          order='-revision', limit=1) or []
        if rows:
            return int(rows[0].get('revision', 0) or 0), rows[0].get('content_hash')
    except Exception as _e:
        logger.info(f'_latest_revision({doc_number}) failed: {_e}')
    return None, None


def _max_revision(doc_number):
    """Vraca max(revision) za dati doc_number, ili None ako nema redova."""
    rev, _ = _latest_revision(doc_number)
    return rev


@v23_admin_bp.route('/api/documents/register', methods=['GET'])
@login_required
def doc_register_list():
    """Lista dokumenata sa JOIN-om na partnera. Filter po docType, godini, klijentu."""
    doc_type = request.args.get('type', '').strip().upper()  # OFFER | INVOICE | PROFORMA
    year = request.args.get('year', '').strip()
    partner_id = request.args.get('partner_id', '').strip()
    q = (request.args.get('q') or '').strip()

    filters = {}
    if doc_type:
        filters['doc_type'] = doc_type
    if year:
        try:
            filters['year'] = int(year)
        except ValueError:
            pass
    if partner_id:
        filters['entity_id'] = partner_id

    try:
        rows = _dl_select('document_register',
                          filters=filters or None,
                          order='-issued_at', limit=500) or []
    except Exception as _e:
        logger.warning(f'doc_register_list select failed: {_e}')
        rows = []

    # Napuni imena partnera (samo one koji se pominju)
    entity_ids = list({r.get('entity_id') for r in rows if r.get('entity_id')})
    partner_names = {}
    for eid in entity_ids:
        try:
            p = store.get_entity('partners', eid) or {}
            partner_names[eid] = p.get('name') or p.get('companyName') or '?'
        except Exception:
            partner_names[eid] = '?'

    # Free-text search po doc_number (PostgREST ne podrzava LIKE preko tuple-a
    # na isti nacin kao SQLite — koristimo 'ilike' ako postoji)
    if q:
        try:
            search_rows = _dl_select('document_register',
                                     filters={'doc_number': ('ilike', f'%{q}%')},
                                     order='-issued_at', limit=500) or []
            # Presek sa postojećim filterima (ako su postavljeni)
            if filters:
                existing_ids = {(r.get('doc_type'), r.get('year'), r.get('entity_id'),
                                r.get('doc_number')) for r in rows}
                rows = [r for r in search_rows
                        if (r.get('doc_type'), r.get('year'), r.get('entity_id'),
                            r.get('doc_number')) in existing_ids]
            else:
                rows = search_rows
        except Exception as _e:
            logger.info(f'doc_register_list search failed: {_e}')

    def _link(doc_type, doc_number, entity_id, revision):
        """V23.1: link ide u glavni CRM sa ?goto= query param-om koji ui.js hvata
        i otvara POSTOJECI modul (customer_offers/invoice/proforma), umesto novog
        prozora. Podržavamo lookup po docNumber → id preko byNumber query hint-a."""
        t = (doc_type or '').lower()
        if t in ('offer', 'invoice', 'proforma'):
            return f'/?goto={t}:number={doc_number}'
        return f'/?goto=partner:{entity_id or ""}'

    out = []
    for r in rows:
        rev = r.get('revision') or 0
        version_label = f'V{rev+1}' if rev > 0 else 'V1'
        out.append({
            'docNumber': r.get('doc_number'),
            'docType': r.get('doc_type'),
            'year': r.get('year'),
            'seq': r.get('seq'),
            'revision': rev,
            'version_label': version_label,
            'status': r.get('status') or 'active',
            'issuedAt': r.get('issued_at'),
            'issuedBy': r.get('issued_by'),
            'entityId': r.get('entity_id'),
            'partner_name': partner_names.get(r.get('entity_id'), '—'),
            'link': _link(r.get('doc_type'), r.get('doc_number'),
                          r.get('entity_id'), rev),
        })
    return jsonify({'items': out, 'count': len(out)})


@v23_admin_bp.route('/api/documents/register/<doc_number>/revisions', methods=['GET'])
@login_required
def doc_revisions(doc_number):
    """Sve revizije za dati broj dokumenta, sortirano od najnovije."""
    try:
        rows = _dl_select('document_revisions',
                          filters={'doc_number': doc_number},
                          order='-revision') or []
    except Exception as _e:
        logger.warning(f'doc_revisions({doc_number}) select failed: {_e}')
        rows = []
    return jsonify({
        'revisions': [{
            'id': r.get('id'), 'revision': r.get('revision'),
            'entityId': r.get('entity_id'),
            'contentHash': r.get('content_hash'),
            'bindingHash': r.get('binding_hash'),
            'changeReason': r.get('change_reason'),
            'changedBy': r.get('changed_by'),
            'changedAt': r.get('changed_at'),
        } for r in rows]
    })


@v23_admin_bp.route('/documents/register', methods=['GET'])
@login_required
def doc_register_page():
    return render_template('document_register.html')


# =========================================================================
#  V23.1 #7 — CONVERSION Offer→Invoice/Proforma (1/1 transfer)
# =========================================================================

@v23_admin_bp.route('/api/documents/register-existing/<doc_type>/<doc_id>', methods=['POST'])
@login_required
def register_existing_document(doc_type, doc_id):
    """V23.1 #6 — hook koji EXISTING offer/invoice/proforma modul poziva posle save-a.
    Namena: upisati taj dokument u Knjigu izdatih dokumenata (document_register)
    sa V-suffix logikom, bez pravljenja novog UI-a.

    Pravila:
      1) Ako dokument već ima docNumber → provera hash-a payload-a; ako je promenjen,
         inkrementiraj revision (V2, V3, ...) i upiši snapshot u document_revisions.
      2) Ako nema docNumber → generisi novi (OFF-YYYY-NNNNN / INV / PRO), upiši V1.

    Vraca: docNumber, versionLabel, revision.
    """
    if doc_type not in ('offer', 'invoice', 'proforma'):
        return jsonify({'error': 'invalid_type'}), 400
    table = {'offer': 'offers', 'invoice': 'invoices', 'proforma': 'proformas'}[doc_type]
    doc_type_upper = {'offer': 'OFFER', 'invoice': 'INVOICE', 'proforma': 'PROFORMA'}[doc_type]

    body = request.get_json(silent=True) or {}
    reason = str(body.get('change_reason') or '').strip()[:500]

    # Ucitaj dokument iz Supabase
    data = store.get_entity(table, doc_id)
    if not data:
        return jsonify({'error': 'not_found'}), 404

    current_doc_number = data.get('docNumber')
    entity_id = (data.get('customerId') or data.get('partnerId') or
                 data.get('buyerId') or None)
    # Stable content hash — items + terms — ignorise timestamp-ove
    _content_for_hash = {
        'items': data.get('items') or [],
        'services': data.get('services') or [],
        'sellingPrice': data.get('sellingPrice'),
        'quantity': data.get('quantity'),
        'currency': data.get('currency'),
        'incoterm': data.get('incoterm'),
        'paymentTerms': data.get('paymentTerms'),
        'validUntil': data.get('validUntil'),
    }
    content_hash = hashlib.sha256(json.dumps(_content_for_hash, sort_keys=True,
                                              default=str).encode('utf-8')).hexdigest()[:32]

    year = datetime.now(timezone.utc).year

    if current_doc_number:
        # Vec je registrovan — proveri da li se sadrzaj promenio
        last_rev, prev_hash = _latest_revision(current_doc_number)

        if prev_hash and prev_hash == content_hash:
            # Nista se ne menja — vrati current bez bump-a
            return jsonify({
                'docNumber': current_doc_number,
                'versionLabel': data.get('versionLabel') or 'V1',
                'revision': data.get('revision', 0),
                'changed': False,
            })

        # V23.1C bugfix: document_register ima UNIQUE (doc_number). Znaci
        # register drži JEDAN red po broju dokumenta. Revizije žive u
        # document_revisions (append-only). Ne insertujemo dupli register red.
        new_rev = (int(last_rev) if last_rev is not None else 0) + 1
        try:
            _dl_insert('document_revisions', {
                'id': str(uuid.uuid4()),
                'doc_number': current_doc_number,
                'revision': new_rev,
                'entity_id': entity_id,
                'snapshot': data,  # JSONB — dict direktno
                'content_hash': content_hash,
                'change_reason': reason or 'Edit via existing form',
                'changed_by': session.get('username'),
                'changed_at': _now(),
            })
            # Update register row-a sa najnovijom revizijom (za lookup u Registeru)
            _dl_update('document_register',
                       {'doc_number': current_doc_number},
                       {'revision': new_rev, 'issued_at': _now(),
                        'issued_by': session.get('username')})
        except Exception as _e:
            logger.error(f'register_existing_document(rev bump) failed: {_e}')
            return jsonify({'error': 'revision_save_failed',
                            'message': str(_e)}), 500

        data['revision'] = new_rev
        data['versionLabel'] = f'V{new_rev + 1}'
        try:
            store.upsert_entity(table, data)
        except Exception as _e:
            logger.error(f'register_existing_document(upsert {table}) failed: {_e}')
        log_audit('EDIT', table, f'Revision {data["versionLabel"]} of {current_doc_number}')
        return jsonify({
            'docNumber': current_doc_number,
            'versionLabel': data['versionLabel'],
            'revision': new_rev,
            'changed': True,
        })
    else:
        # Prva registracija — dodeli broj
        seq = _next_seq(doc_type_upper, year)
        doc_number = f"{doc_type_upper[:3]}-{year}-{seq:05d}"
        data['docNumber'] = doc_number
        data['issueDate'] = data.get('issueDate') or _now()[:10]
        data['revision'] = 0
        data['versionLabel'] = 'V1'
        try:
            _dl_insert('document_register', {
                'id': str(uuid.uuid4()),
                'doc_type': doc_type_upper,
                'year': year,
                'seq': seq,
                'doc_number': doc_number,
                'entity_id': entity_id,
                'revision': 0,
                'issued_at': _now(),
                'issued_by': session.get('username'),
            })
            _dl_insert('document_revisions', {
                'id': str(uuid.uuid4()),
                'doc_number': doc_number,
                'revision': 0,
                'entity_id': entity_id,
                'snapshot': data,  # JSONB
                'content_hash': content_hash,
                'change_reason': reason or 'Initial issue',
                'changed_by': session.get('username'),
                'changed_at': _now(),
            })
            store.upsert_entity(table, data)
        except Exception as _e:
            logger.error(f'register_existing_document(initial) failed: {_e}')
            return jsonify({'error': 'register_failed',
                            'message': str(_e)}), 500
        log_audit('CREATE', table, f'Registered {doc_number} (V1)')
        return jsonify({
            'docNumber': doc_number,
            'versionLabel': 'V1',
            'revision': 0,
            'changed': True,
        })


@v23_admin_bp.route('/api/documents/convert', methods=['POST'])
@login_required
def convert_document():
    """Konvertuje ponudu u fakturu ili proformu, 1:1 prenos svih polja.
    Payload:
      {
        "source_type": "offer",       # samo offer za sada
        "source_id":  "<offer id>",
        "target_type": "invoice" | "proforma",
        "issue_date":  "YYYY-MM-DD",  # opciono, default today
        "due_date":    "YYYY-MM-DD"   # opciono
      }
    Vraca id novog dokumenta + assigned docNumber (Vn suffix ako je edit).
    """
    body = request.get_json(silent=True) or {}
    src_type = body.get('source_type', 'offer')
    src_id = body.get('source_id')
    target = body.get('target_type')
    if src_type != 'offer' or not src_id or target not in ('invoice', 'proforma'):
        return jsonify({'error': 'invalid_payload'}), 400

    # Ucitaj ponudu
    offer = store.get_entity('offers', src_id)
    if not offer:
        return jsonify({'error': 'offer_not_found'}), 404

    # Kopiraj SVA polja 1/1
    new_id = str(uuid.uuid4())
    target_data = dict(offer)  # shallow copy
    target_data['sourceOfferId'] = src_id
    # V23.1C: offer schema uses either 'offerNo' (legacy 1/2026 format)
    # or 'docNumber' (new OFF-YYYY-NNNNN format) or 'offerNumber'; support all.
    target_data['sourceOfferNumber'] = (offer.get('offerNumber')
                                        or offer.get('offerNo')
                                        or offer.get('docNumber'))
    target_data['createdAt'] = _now()
    target_data['createdBy'] = session.get('username')
    target_data['convertedFrom'] = 'offer'
    target_data['status'] = 'draft'
    target_data['id'] = new_id
    if body.get('issue_date'):
        target_data['issueDate'] = body['issue_date']
    if body.get('due_date'):
        target_data['dueDate'] = body['due_date']

    # Numeracija — koristi document_register
    doc_type = 'INVOICE' if target == 'invoice' else 'PROFORMA'
    year = datetime.now(timezone.utc).year
    seq = _next_seq(doc_type, year)
    doc_number = f"{doc_type[:3]}-{year}-{seq:05d}"
    target_data['docNumber'] = doc_number

    # Snimi u ciljnu tabelu (invoices / proformas) — obe postoje u Supabase-u
    target_table = 'invoices' if target == 'invoice' else 'proformas'
    try:
        store.upsert_entity(target_table, target_data)
        # Register u document_register
        _dl_insert('document_register', {
            'id': str(uuid.uuid4()),
            'doc_type': doc_type,
            'year': year,
            'seq': seq,
            'doc_number': doc_number,
            'entity_id': offer.get('partnerId') or offer.get('customerId'),
            'revision': 0,
            'issued_at': _now(),
            'issued_by': session.get('username'),
        })
    except Exception as _e:
        logger.error(f'convert_document failed: {_e}')
        return jsonify({'error': 'convert_failed',
                        'message': str(_e)}), 500

    log_audit('CREATE', target_table,
              f'Converted offer {offer.get("offerNumber","?")} → {doc_number}')

    return jsonify({
        'new_id': new_id,
        'target_type': target,
        'docNumber': doc_number,
        'redirect': f'/documents/edit/{target}/{new_id}',
    })


# =========================================================================
#  V23.1 #5 — DOCUMENT EDITOR (page-based, not modal)
# =========================================================================

@v23_admin_bp.route('/documents/edit/<doc_type>/<doc_id>', methods=['GET'])
@login_required
def doc_editor_page(doc_type, doc_id):
    """V23.1 revision — redirect u POSTOJECI editor modal umesto duplog UI.
    Existing offer/invoice/proforma moduli imaju sve funkcionalnosti (partner
    dropdown, product autocomplete, bank auto-fill…). Ova ruta samo instruira
    frontend da otvori odgovarajuci modul preko ?goto= query param-a.
    """
    if doc_type not in ('offer', 'invoice', 'proforma'):
        return "Invalid document type.", 400
    from flask import redirect
    return redirect(f'/?goto={doc_type}:{doc_id}')


@v23_admin_bp.route('/documents/new/<doc_type>', methods=['GET'])
@login_required
def doc_editor_new_page(doc_type):
    """Redirect na postojeci flow za kreiranje. Za offer, otvara customer_offers.js
    modal; za invoice, deal invoice flow."""
    if doc_type not in ('offer', 'invoice', 'proforma'):
        return "Invalid document type.", 400
    from flask import redirect
    return redirect(f'/?new={doc_type}')


@v23_admin_bp.route('/api/documents/<doc_type>/<doc_id>', methods=['GET'])
@login_required
def doc_get(doc_type, doc_id):
    table = {'offer': 'offers', 'invoice': 'invoices', 'proforma': 'proformas'}.get(doc_type)
    if not table:
        return jsonify({'error': 'invalid_type'}), 400
    data = store.get_entity(table, doc_id)
    if not data:
        return jsonify({'error': 'not_found'}), 404
    return jsonify({'doc': data})


@v23_admin_bp.route('/api/documents/<doc_type>/<doc_id>', methods=['POST'])
@login_required
def doc_save(doc_type, doc_id):
    """Save ide u dva moda:
       - Save as Draft (default): samo update-uje row bez register/revision.
       - Save Final (finalize=true): rezervise docNumber i registruje, ili
         ako je vec finalizovan pravi novu reviziju sa V+1 suffix.
    """
    table = {'offer': 'offers', 'invoice': 'invoices', 'proforma': 'proformas'}.get(doc_type)
    if not table:
        return jsonify({'error': 'invalid_type'}), 400
    body = request.get_json(silent=True) or {}
    payload = body.get('doc') or {}
    finalize = bool(body.get('finalize'))
    reason = str(body.get('change_reason') or '').strip()[:500]

    if not doc_id:
        doc_id = str(uuid.uuid4())
        payload['id'] = doc_id
        payload['createdAt'] = _now()
        payload['createdBy'] = session.get('username')

    payload['updatedAt'] = _now()
    payload['updatedBy'] = session.get('username')

    current_doc_number = payload.get('docNumber')

    if finalize:
        doc_type_upper = {'offer': 'OFFER', 'invoice': 'INVOICE', 'proforma': 'PROFORMA'}[doc_type]
        year = datetime.now(timezone.utc).year

        try:
            if current_doc_number:
                # Vec je izdat — nova revizija (V+1)
                last_max = _max_revision(current_doc_number)
                new_rev = (int(last_max) if last_max is not None else 0) + 1
                # Snapshot revizije — content_hash se ovde ne racuna u originalu
                _dl_insert('document_revisions', {
                    'id': str(uuid.uuid4()),
                    'doc_number': current_doc_number,
                    'revision': new_rev,
                    'entity_id': payload.get('partnerId') or payload.get('customerId'),
                    'snapshot': payload,  # JSONB
                    'change_reason': reason or 'Draft finalized',
                    'changed_by': session.get('username'),
                    'changed_at': _now(),
                })
                # document_register ima UNIQUE(doc_number) — ne insertujemo novi
                # red; samo apdejtujemo revision/issued_at/issued_by na postojećem.
                _dl_update('document_register',
                           {'doc_number': current_doc_number},
                           {'revision': new_rev, 'issued_at': _now(),
                            'issued_by': session.get('username')})
                payload['revision'] = new_rev
                payload['versionLabel'] = f'V{new_rev + 1}'
            else:
                # Prva finalizacija — dodeli broj
                seq = _next_seq(doc_type_upper, year)
                doc_number = f"{doc_type_upper[:3]}-{year}-{seq:05d}"
                payload['docNumber'] = doc_number
                payload['issueDate'] = payload.get('issueDate') or _now()[:10]
                payload['revision'] = 0
                payload['versionLabel'] = 'V1'
                _dl_insert('document_register', {
                    'id': str(uuid.uuid4()),
                    'doc_type': doc_type_upper,
                    'year': year,
                    'seq': seq,
                    'doc_number': doc_number,
                    'entity_id': payload.get('partnerId') or payload.get('customerId'),
                    'revision': 0,
                    'issued_at': _now(),
                    'issued_by': session.get('username'),
                })
            payload['status'] = 'final'
        except Exception as _e:
            logger.error(f'doc_save finalize failed: {_e}')
            return jsonify({'error': 'finalize_failed',
                            'message': str(_e)}), 500

    try:
        store.upsert_entity(table, payload)
    except Exception as _e:
        logger.error(f'doc_save upsert {table} failed: {_e}')
        return jsonify({'error': 'save_failed', 'message': str(_e)}), 500

    log_audit('EDIT' if not finalize else 'CREATE', table,
              f'{"Draft saved" if not finalize else "Finalized"}: {payload.get("docNumber", doc_id[:8])}')

    return jsonify({
        'id': doc_id,
        'docNumber': payload.get('docNumber'),
        'versionLabel': payload.get('versionLabel'),
        'status': payload.get('status'),
        'finalized': finalize,
    })
