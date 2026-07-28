"""
V23.1 admin blueprints — sve u jednoj datoteci radi lakseg wire-a:

  #3 Granular internal permissions (matrica po korisniku + backend gate)
  #4 Portal permissions per-partner UI
  #6 Document Register enhanced page
  #7 Conversion Offer→Invoice/Proforma helper endpoint

Sve rute su admin-only osim gde je jasno drukcije oznaceno.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, session, render_template

from config import DB_FILE
from utils import login_required, log_audit

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
    with sqlite3.connect(DB_FILE, timeout=10.0) as conn:
        rows = conn.execute(
            "SELECT id, username, role, permissions, full_name, email "
            "FROM users ORDER BY role='admin' DESC, username ASC"
        ).fetchall()
    out = []
    for r in rows:
        try:
            perms = json.loads(r[3]) if r[3] else {}
        except Exception:
            perms = {}
        # Admin ima sve, ne prikazuje se checkbox
        out.append({
            'id': r[0], 'username': r[1], 'role': r[2],
            'full_name': r[4] or '', 'email': r[5] or '',
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

    with sqlite3.connect(DB_FILE, timeout=10.0) as conn:
        conn.execute('PRAGMA busy_timeout=10000')
        conn.execute("UPDATE users SET permissions=? WHERE id=?",
                     (json.dumps(cleaned), uid))

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
        with sqlite3.connect(DB_FILE, timeout=5.0) as conn:
            r = conn.execute("SELECT role, permissions FROM users WHERE id=?", (uid,)).fetchone()
        if not r:
            return False
        if r[0] == 'admin':
            return True
        try:
            perms = json.loads(r[1]) if r[1] else {}
        except Exception:
            perms = {}
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
    with sqlite3.connect(DB_FILE, timeout=10.0) as conn:
        r = conn.execute("SELECT data FROM partners WHERE id=?", (pid,)).fetchone()
    if not r:
        return jsonify({'error': 'partner_not_found'}), 404
    try:
        data = json.loads(r[0]) if r[0] else {}
    except Exception:
        data = {}
    enabled = data.get('portalPermissions') or list(PORTAL_MODULES.keys())
    return jsonify({
        'partner_id': pid,
        'partner_name': data.get('name', '?'),
        'enabled_modules': enabled,
        'is_portal_active': data.get('isPortalActive', True),
        'is_premium': bool(data.get('isPremium')),
        'view_only_own_docs': bool(data.get('viewOnlyOwnDocs', True)),
    })


@v23_admin_bp.route('/api/admin/portal/permissions/<pid>', methods=['POST'])
@login_required
def portal_perm_set(pid):
    """Postavlja portalPermissions listu + druge portal flag-ove za partnera."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin_only'}), 403
    body = request.get_json(silent=True) or {}

    with sqlite3.connect(DB_FILE, timeout=10.0) as conn:
        conn.execute('PRAGMA busy_timeout=10000')
        r = conn.execute("SELECT data FROM partners WHERE id=?", (pid,)).fetchone()
        if not r:
            return jsonify({'error': 'partner_not_found'}), 404
        try:
            data = json.loads(r[0]) if r[0] else {}
        except Exception:
            data = {}

        # Modules — filtriraj samo poznate
        if 'enabled_modules' in body and isinstance(body['enabled_modules'], list):
            valid = [m for m in body['enabled_modules'] if m in PORTAL_MODULES]
            data['portalPermissions'] = valid

        if 'is_portal_active' in body:
            data['isPortalActive'] = bool(body['is_portal_active'])
        if 'is_premium' in body:
            data['isPremium'] = bool(body['is_premium'])
        if 'view_only_own_docs' in body:
            data['viewOnlyOwnDocs'] = bool(body['view_only_own_docs'])

        conn.execute("UPDATE partners SET data=? WHERE id=?",
                     (json.dumps(data), pid))

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

@v23_admin_bp.route('/api/documents/register', methods=['GET'])
@login_required
def doc_register_list():
    """Lista dokumenata sa JOIN-om na partnera. Filter po docType, godini, klijentu."""
    doc_type = request.args.get('type', '').strip().upper()  # OFFER | INVOICE | PROFORMA
    year = request.args.get('year', '').strip()
    partner_id = request.args.get('partner_id', '').strip()
    q = request.args.get('q', '').strip()

    where = []
    params = []
    if doc_type:
        where.append('docType=?'); params.append(doc_type)
    if year:
        try:
            where.append('year=?'); params.append(int(year))
        except ValueError:
            pass
    if partner_id:
        where.append('entityId=?'); params.append(partner_id)
    if q:
        where.append('docNumber LIKE ?'); params.append(f'%{q}%')

    where_sql = ' WHERE ' + ' AND '.join(where) if where else ''

    with sqlite3.connect(DB_FILE, timeout=10.0) as conn:
        rows = conn.execute(
            f"SELECT docNumber, docType, year, seq, revision, status, "
            f"issuedAt, issuedBy, entityId FROM document_register "
            f"{where_sql} ORDER BY issuedAt DESC LIMIT 500",
            params
        ).fetchall()

        # Napuni imena partnera (samo one koji se pominju)
        entity_ids = list({r[8] for r in rows if r[8]})
        partner_names = {}
        for eid in entity_ids:
            try:
                pr = conn.execute("SELECT data FROM partners WHERE id=?", (eid,)).fetchone()
                if pr:
                    pd = json.loads(pr[0]) if pr[0] else {}
                    partner_names[eid] = pd.get('name', '?')
            except Exception:
                partner_names[eid] = '?'

    def _link(doc_type, doc_number, entity_id, revision):
        """Vraca client-side hash za brz jump."""
        if doc_type == 'OFFER':
            return f'#offers/{doc_number}'
        if doc_type == 'INVOICE':
            return f'#invoices/{doc_number}'
        if doc_type == 'PROFORMA':
            return f'#proformas/{doc_number}'
        return f'#partners/{entity_id or ""}'

    out = []
    for r in rows:
        version_label = f'V{r[4]+1}' if r[4] and r[4] > 0 else 'V1'
        out.append({
            'docNumber': r[0],
            'docType': r[1],
            'year': r[2],
            'seq': r[3],
            'revision': r[4] or 0,
            'version_label': version_label,
            'status': r[5],
            'issuedAt': r[6],
            'issuedBy': r[7],
            'entityId': r[8],
            'partner_name': partner_names.get(r[8], '—'),
            'link': _link(r[1], r[0], r[8], r[4]),
        })
    return jsonify({'items': out, 'count': len(out)})


@v23_admin_bp.route('/api/documents/register/<doc_number>/revisions', methods=['GET'])
@login_required
def doc_revisions(doc_number):
    """Sve revizije za dati broj dokumenta, sortirano od najnovije."""
    with sqlite3.connect(DB_FILE, timeout=10.0) as conn:
        rows = conn.execute(
            "SELECT id, revision, entityId, contentHash, bindingHash, "
            "changeReason, changedBy, changedAt FROM document_revisions "
            "WHERE docNumber=? ORDER BY revision DESC",
            (doc_number,)
        ).fetchall()
    return jsonify({
        'revisions': [{
            'id': r[0], 'revision': r[1], 'entityId': r[2],
            'contentHash': r[3], 'bindingHash': r[4],
            'changeReason': r[5], 'changedBy': r[6], 'changedAt': r[7],
        } for r in rows]
    })


@v23_admin_bp.route('/documents/register', methods=['GET'])
@login_required
def doc_register_page():
    return render_template('document_register.html')


# =========================================================================
#  V23.1 #7 — CONVERSION Offer→Invoice/Proforma (1/1 transfer)
# =========================================================================

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

    with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
        conn.execute('PRAGMA busy_timeout=15000')
        # Ucitaj ponudu
        r = conn.execute("SELECT data FROM offers WHERE id=?", (src_id,)).fetchone()
        if not r:
            return jsonify({'error': 'offer_not_found'}), 404
        try:
            offer = json.loads(r[0]) if r[0] else {}
        except Exception:
            offer = {}

        # Kopiraj SVA polja 1/1
        new_id = str(uuid.uuid4())
        target_data = dict(offer)  # shallow copy
        target_data['sourceOfferId'] = src_id
        target_data['sourceOfferNumber'] = offer.get('offerNumber')
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
        # Sledeci seq
        seq_row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM document_register WHERE docType=? AND year=?",
            (doc_type, year)
        ).fetchone()
        seq = seq_row[0] if seq_row else 1
        doc_number = f"{doc_type[:3]}-{year}-{seq:05d}"
        target_data['docNumber'] = doc_number

        # Snimi u ciljnu tabelu (odgovarajuca — invoices ili proforma_docs?)
        # Aplikacija koristi 'offers' generic tabelu; koristimo dedikovane ako postoje
        # inace fallback na 'offers' sa type='invoice' / 'proforma'.
        target_table = 'invoices' if target == 'invoice' else 'proformas'
        # Napravi tabelu ako ne postoji (idempotent — vec u database.py, ali osiguranje)
        conn.execute(f'CREATE TABLE IF NOT EXISTS {target_table} (id TEXT PRIMARY KEY, data TEXT)')
        conn.execute(f"INSERT INTO {target_table} (id, data) VALUES (?, ?)",
                     (new_id, json.dumps(target_data)))

        # Register u document_register
        conn.execute(
            "INSERT INTO document_register (docType, year, seq, docNumber, entityId, "
            "revision, status, issuedAt, issuedBy) "
            "VALUES (?, ?, ?, ?, ?, 0, 'active', ?, ?)",
            (doc_type, year, seq, doc_number,
             offer.get('partnerId') or offer.get('customerId'),
             _now(), session.get('username'))
        )

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
    """Puna stranica za uredjivanje ponude/fakture/proforme.
    doc_type u {offer, invoice, proforma}."""
    if doc_type not in ('offer', 'invoice', 'proforma'):
        return "Invalid document type.", 400
    return render_template('document_editor.html', doc_type=doc_type, doc_id=doc_id)


@v23_admin_bp.route('/documents/new/<doc_type>', methods=['GET'])
@login_required
def doc_editor_new_page(doc_type):
    """Prazan editor za nov dokument."""
    if doc_type not in ('offer', 'invoice', 'proforma'):
        return "Invalid document type.", 400
    return render_template('document_editor.html', doc_type=doc_type, doc_id='')


@v23_admin_bp.route('/api/documents/<doc_type>/<doc_id>', methods=['GET'])
@login_required
def doc_get(doc_type, doc_id):
    table = {'offer': 'offers', 'invoice': 'invoices', 'proforma': 'proformas'}.get(doc_type)
    if not table:
        return jsonify({'error': 'invalid_type'}), 400
    with sqlite3.connect(DB_FILE, timeout=10.0) as conn:
        conn.execute(f'CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, data TEXT)')
        r = conn.execute(f"SELECT data FROM {table} WHERE id=?", (doc_id,)).fetchone()
    if not r:
        return jsonify({'error': 'not_found'}), 404
    try:
        return jsonify({'doc': json.loads(r[0]) if r[0] else {}})
    except Exception:
        return jsonify({'doc': {}})


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

    with sqlite3.connect(DB_FILE, timeout=15.0) as conn:
        conn.execute('PRAGMA busy_timeout=15000')
        conn.execute(f'CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, data TEXT)')

        current_doc_number = payload.get('docNumber')

        if finalize:
            doc_type_upper = {'offer': 'OFFER', 'invoice': 'INVOICE', 'proforma': 'PROFORMA'}[doc_type]
            year = datetime.now(timezone.utc).year

            if current_doc_number:
                # Vec je izdat — nova revizija (V+1)
                r = conn.execute(
                    "SELECT MAX(revision) FROM document_register WHERE docNumber=?",
                    (current_doc_number,)
                ).fetchone()
                new_rev = (r[0] or 0) + 1
                conn.execute(
                    "INSERT INTO document_register (docType, year, seq, docNumber, entityId, "
                    "revision, status, issuedAt, issuedBy) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                    (doc_type_upper, year,
                     int(current_doc_number.split('-')[-1]) if '-' in current_doc_number else 0,
                     current_doc_number,
                     payload.get('partnerId') or payload.get('customerId'),
                     new_rev, _now(), session.get('username'))
                )
                # Snapshot revizije
                conn.execute(
                    "INSERT INTO document_revisions (id, docNumber, revision, entityId, "
                    "snapshot, changeReason, changedBy, changedAt) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), current_doc_number, new_rev,
                     payload.get('partnerId') or payload.get('customerId'),
                     json.dumps(payload), reason or 'Draft finalized',
                     session.get('username'), _now())
                )
                payload['revision'] = new_rev
                payload['versionLabel'] = f'V{new_rev + 1}'
            else:
                # Prva finalizacija — dodeli broj
                seq_row = conn.execute(
                    "SELECT COALESCE(MAX(seq),0)+1 FROM document_register WHERE docType=? AND year=?",
                    (doc_type_upper, year)
                ).fetchone()
                seq = seq_row[0]
                doc_number = f"{doc_type_upper[:3]}-{year}-{seq:05d}"
                payload['docNumber'] = doc_number
                payload['issueDate'] = payload.get('issueDate') or _now()[:10]
                payload['revision'] = 0
                payload['versionLabel'] = 'V1'
                conn.execute(
                    "INSERT INTO document_register (docType, year, seq, docNumber, entityId, "
                    "revision, status, issuedAt, issuedBy) VALUES (?, ?, ?, ?, ?, 0, 'active', ?, ?)",
                    (doc_type_upper, year, seq, doc_number,
                     payload.get('partnerId') or payload.get('customerId'),
                     _now(), session.get('username'))
                )
            payload['status'] = 'final'

        conn.execute(f"INSERT OR REPLACE INTO {table} (id, data) VALUES (?, ?)",
                     (doc_id, json.dumps(payload)))

    log_audit('EDIT' if not finalize else 'CREATE', table,
              f'{"Draft saved" if not finalize else "Finalized"}: {payload.get("docNumber", doc_id[:8])}')

    return jsonify({
        'id': doc_id,
        'docNumber': payload.get('docNumber'),
        'versionLabel': payload.get('versionLabel'),
        'status': payload.get('status'),
        'finalized': finalize,
    })
