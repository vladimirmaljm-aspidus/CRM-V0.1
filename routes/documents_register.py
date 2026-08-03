"""
Registar dokumenata — atomsko izdavanje brojeva, prevencija duplikata,
istorija revizija.

Filozofija:
  * Broj dokumenta je resurs koji se REZERVIŠE atomično. Nikad ne
    postoje dva izdata dokumenta sa istim brojem u istoj godini.
  * Kada admin promeni već izdat dokument (npr. korekcija cene posle
    slanja klijentu), SISTEM AUTOMATSKI dodaje suffix -R1, -R2, itd
    i snima punu snapshot kopiju u document_revisions. Original ostaje
    nedirnut u registru sa status='superseded'.
  * Endpointi:
      GET  /api/documents/next_number?docType=offer[&year=YYYY]
      POST /api/documents/issue                — rezerviši broj
      POST /api/documents/revise               — dodaj -Rn reviziju
      GET  /api/documents/history/<docNumber>  — vraća sve revizije
      GET  /api/documents/register?type=offer&year=YYYY
      POST /api/deals/<deal_id>/issue-document
      GET  /api/deals/<deal_id>/documents

V24.0 SUPABASE-ONLY: atomicity se oslanja na Postgres UNIQUE INDEX
`(doc_type, year, seq)` — ako dva zahteva istovremeno pokušaju isti seq,
drugi pukne sa 23505 unique_violation i retry-uje sa sledećim seq.
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, session

from utils import login_required, log_audit
from data_layer import (
    select as _dl_select,
    select_one as _dl_select_one,
    insert as _dl_insert,
    update as _dl_update,
)

logger = logging.getLogger(__name__)

documents_register_bp = Blueprint('documents_register_bp', __name__)


DOC_TYPE_PREFIX = {
    'offer': 'OFF',
    'invoice': 'INV',
    'proforma': 'PRO',
    'contract': 'CNT',
    'delivery_note': 'DN',
    'credit_note': 'CN',
}


def _current_year():
    return datetime.now(timezone.utc).year


def _iso_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _format_number(prefix, seq, year, revision=0):
    """OFF-042/2026 ili OFF-042/2026-R1 za reviziju."""
    core = f"{prefix}-{seq:03d}/{year}"
    return core if revision <= 0 else f"{core}-R{revision}"


def _row_to_register(r: dict) -> dict:
    """Normalizuje Supabase snake_case red u frontend-expected camelCase."""
    return {
        'docType': r.get('doc_type'),
        'year': r.get('year'),
        'seq': r.get('seq'),
        'docNumber': r.get('doc_number'),
        'entityId': r.get('entity_id'),
        'revision': r.get('revision'),
        'status': r.get('status'),
        'issuedAt': r.get('issued_at'),
        'issuedBy': r.get('issued_by'),
    }


def _next_seq(doc_type: str, year: int) -> int:
    """Pronalazi sledeći seq broj u datoj godini — max(seq) + 1.
    Atomicity osigurava UNIQUE INDEX (doc_type, year, seq) na Supabase.
    Ako dva zahteva istovremeno pokušaju isti seq, drugi pukne sa 23505
    i caller radi retry."""
    try:
        rows = _dl_select(
            'document_register',
            filters={'doc_type': doc_type, 'year': year},
            columns='seq',
            order='-seq',
            limit=1,
        ) or []
        if rows and rows[0].get('seq') is not None:
            return int(rows[0]['seq']) + 1
    except Exception as e:
        logger.warning('_next_seq select failed: %s', e)
    return 1


def _insert_register(doc_type, year, seq, number, entity_id, revision, status, username):
    """Wrapper za insert sa unique_violation signalizacijom."""
    return _dl_insert('document_register', {
        'id': str(uuid.uuid4()),
        'doc_type': doc_type,
        'year': int(year),
        'seq': int(seq),
        'doc_number': number,
        'entity_id': entity_id,
        'revision': int(revision),
        'status': status,
        'issued_at': _iso_now(),
        'issued_by': username,
    })


def _is_unique_violation(exc: Exception) -> bool:
    msg = str(exc).lower()
    return '23505' in msg or 'unique' in msg or 'duplicate' in msg


# ==========================================================
#  ENDPOINT-i
# ==========================================================

@documents_register_bp.route('/api/documents/next_number', methods=['GET'])
@login_required
def next_number():
    doc_type = (request.args.get('docType') or 'offer').strip().lower()
    if doc_type not in DOC_TYPE_PREFIX:
        return jsonify({'error': 'INVALID_DOC_TYPE',
                        'allowed': list(DOC_TYPE_PREFIX.keys())}), 400
    try:
        year = int(request.args.get('year') or _current_year())
    except ValueError:
        return jsonify({'error': 'INVALID_YEAR'}), 400

    seq = _next_seq(doc_type, year)
    number = _format_number(DOC_TYPE_PREFIX[doc_type], seq, year)
    return jsonify({
        'docType': doc_type,
        'year': year,
        'seq': seq,
        'preview': number,
        'note': 'This is a preview only. Number is reserved via POST /api/documents/issue.'
    })


@documents_register_bp.route('/api/documents/issue', methods=['POST'])
@login_required
def issue_number():
    """Atomsko rezervisanje broja. Ako je entityId prosleđen i već ima
    izdat broj za isti (docType, entityId), vraća postojeći umesto novog
    — idempotentno je bezbedno pozvati više puta."""
    p = request.get_json(silent=True) or {}
    doc_type = (p.get('docType') or 'offer').strip().lower()
    if doc_type not in DOC_TYPE_PREFIX:
        return jsonify({'error': 'INVALID_DOC_TYPE'}), 400
    entity_id = p.get('entityId') or None
    try:
        year = int(p.get('year') or _current_year())
    except (TypeError, ValueError):
        return jsonify({'error': 'INVALID_YEAR'}), 400

    # Idempotentno: već izdato za istu entity
    if entity_id:
        try:
            existing = _dl_select_one(
                'document_register',
                {'doc_type': doc_type, 'entity_id': entity_id, 'revision': 0},
            )
            if existing:
                return jsonify({
                    'docNumber': existing.get('doc_number'),
                    'seq': existing.get('seq'),
                    'revision': existing.get('revision'),
                    'year': year, 'status': 'existing'
                })
        except Exception as e:
            logger.warning('issue_number idempotency check failed: %s', e)

    username = session.get('username') or 'system'
    seq = _next_seq(doc_type, year)
    number = _format_number(DOC_TYPE_PREFIX[doc_type], seq, year)
    try:
        _insert_register(doc_type, year, seq, number, entity_id, 0, 'active', username)
    except Exception as e:
        if _is_unique_violation(e):
            # Race na UNIQUE(doc_number, doc_type, year, seq) → retry sa novim seq
            logger.warning('Duplicate docNumber, retrying: %s', e)
            seq = _next_seq(doc_type, year)
            number = _format_number(DOC_TYPE_PREFIX[doc_type], seq, year)
            try:
                _insert_register(doc_type, year, seq, number, entity_id, 0, 'active', username)
            except Exception as e2:
                logger.error('issue_number retry failed: %s', e2)
                return jsonify({'error': 'ISSUE_FAILED_RETRY',
                                'detail': str(e2)[:200]}), 500
        else:
            logger.error('issue_number insert failed: %s', e)
            return jsonify({'error': 'ISSUE_FAILED',
                            'detail': str(e)[:200]}), 500

    log_audit('CREATE', 'documents',
              f'Document number issued: {number} (entity {entity_id})')
    return jsonify({
        'docNumber': number, 'seq': seq, 'revision': 0,
        'year': year, 'status': 'newly_issued'
    })


@documents_register_bp.route('/api/documents/revise', methods=['POST'])
@login_required
def revise():
    """Kreira novu reviziju (R1, R2, ...) za već izdat broj. Prosleđuje
    se snapshot celokupnih podataka koji definišu novi sadržaj + reason."""
    p = request.get_json(silent=True) or {}
    base_number = (p.get('docNumber') or '').strip()
    snapshot = p.get('snapshot')
    change_reason = (p.get('changeReason') or '').strip()
    if not base_number or not isinstance(snapshot, dict):
        return jsonify({'error': 'DOC_NUMBER_AND_SNAPSHOT_REQUIRED'}), 400
    if not change_reason:
        return jsonify({'error': 'CHANGE_REASON_REQUIRED',
                        'message': 'Legal requirement — every revision must have a reason.'}), 400

    # Nadji originalnu row
    row = _dl_select_one('document_register', {'doc_number': base_number})
    if not row:
        return jsonify({'error': 'DOC_NUMBER_NOT_FOUND'}), 404
    doc_type = row.get('doc_type')
    year = row.get('year')
    seq = row.get('seq')
    entity_id = row.get('entity_id')

    # Najveća postojeća revizija za taj docType/year/seq
    try:
        rev_rows = _dl_select(
            'document_register',
            filters={'doc_type': doc_type, 'year': year, 'seq': seq},
            columns='revision',
            order='-revision',
            limit=1,
        ) or []
        max_rev = int(rev_rows[0]['revision']) if rev_rows else 0
    except Exception:
        max_rev = 0
    new_rev = max_rev + 1
    new_number = _format_number(DOC_TYPE_PREFIX[doc_type], seq, year, revision=new_rev)
    now = _iso_now()
    username = session.get('username') or 'system'

    # Snimi u register
    try:
        _insert_register(doc_type, year, seq, new_number, entity_id,
                         new_rev, 'active', username)
    except Exception as e:
        logger.error('revise insert failed: %s', e)
        return jsonify({'error': 'REVISE_FAILED',
                        'detail': str(e)[:200]}), 500

    # Označi sve prethodne kao superseded
    try:
        prev_rows = _dl_select(
            'document_register',
            filters={'doc_type': doc_type, 'year': year, 'seq': seq},
            columns='id,revision',
        ) or []
        for pr in prev_rows:
            if int(pr.get('revision', 0)) < new_rev:
                _dl_update('document_register', {'id': pr.get('id')},
                           {'status': 'superseded'})
    except Exception as e:
        logger.warning('revise supersede update failed: %s', e)

    # Snimi puno snapshot podataka u revisions tabelu
    binding_seed = json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode('utf-8')
    binding_hash = hashlib.sha256(binding_seed).hexdigest().upper()
    try:
        _dl_insert('document_revisions', {
            'id': str(uuid.uuid4()),
            'doc_number': new_number,
            'revision': new_rev,
            'entity_id': entity_id,
            'snapshot': snapshot,  # JSONB — dict direktno
            'binding_hash': binding_hash,
            'change_reason': change_reason,
            'changed_by': username,
            'changed_at': now,
        })
    except Exception as e:
        logger.warning('revise revision snapshot insert failed: %s', e)

    log_audit('UPDATE', 'documents',
              f'Document revised: {base_number} → {new_number} '
              f'(reason: {change_reason[:80]})')
    return jsonify({
        'docNumber': new_number,
        'previousDocNumber': base_number,
        'revision': new_rev,
        'bindingHash': binding_hash,
        'issuedAt': now,
    })


@documents_register_bp.route('/api/documents/history/<path:doc_number>', methods=['GET'])
@login_required
def document_history(doc_number):
    """Vraća sve revizije za dati broj (ili osnovni broj bez -R suffiksa)."""
    # Skini eventualni -Rn suffix da bi dobili base
    base = doc_number.split('-R')[0] if '-R' in doc_number else doc_number
    # Nadji tip/god/seq iz base
    core = _dl_select_one('document_register', {'doc_number': base})
    if not core:
        return jsonify({'error': 'DOC_NUMBER_NOT_FOUND'}), 404
    doc_type = core.get('doc_type')
    year = core.get('year')
    seq = core.get('seq')

    rows = _dl_select(
        'document_register',
        filters={'doc_type': doc_type, 'year': year, 'seq': seq},
        order='revision',
    ) or []
    register_rows = [_row_to_register(r) for r in rows]

    # Document revisions for these doc numbers
    revisions = []
    if register_rows:
        for rr in register_rows:
            rev_rows = _dl_select(
                'document_revisions',
                filters={'doc_number': rr['docNumber']},
                order='revision',
            ) or []
            for r in rev_rows:
                snap = r.get('snapshot')
                if isinstance(snap, str):
                    try: snap = json.loads(snap)
                    except Exception: pass
                revisions.append({
                    'docNumber': r.get('doc_number'),
                    'revision': r.get('revision'),
                    'snapshot': snap,
                    'bindingHash': r.get('binding_hash'),
                    'contentHash': r.get('content_hash'),
                    'changeReason': r.get('change_reason'),
                    'changedBy': r.get('changed_by'),
                    'changedAt': r.get('changed_at'),
                })

    return jsonify({
        'baseDocNumber': base,
        'docType': doc_type, 'year': year, 'seq': seq,
        'register': register_rows,
        'revisions': revisions,
        'currentActive': next((r['docNumber'] for r in register_rows
                               if r['status'] == 'active'), base),
    })


@documents_register_bp.route('/api/documents/register', methods=['GET'])
@login_required
def register_list():
    doc_type = (request.args.get('docType') or '').strip().lower()
    year = request.args.get('year')
    limit = min(int(request.args.get('limit') or 200), 1000)

    filters = {}
    if doc_type:
        filters['doc_type'] = doc_type
    if year:
        try: filters['year'] = int(year)
        except ValueError: pass

    try:
        rows = _dl_select(
            'document_register',
            filters=filters or None,
            order='-year,seq,revision' if False else '-seq',
            limit=limit,
        ) or []
    except Exception as e:
        logger.warning('register_list select failed: %s', e)
        rows = []
    return jsonify({
        'items': [_row_to_register(r) for r in rows]
    })


# ==========================================================
#  FAZA 4: Per-deal quick-issue endpoint
# ==========================================================
@documents_register_bp.route('/api/deals/<deal_id>/issue-document', methods=['POST'])
@login_required
def issue_document_for_deal(deal_id):
    if not deal_id:
        return jsonify({'error': 'DEAL_ID_REQUIRED'}), 400
    p = request.get_json(silent=True) or {}
    doc_type = (p.get('docType') or '').strip().lower()
    if doc_type not in DOC_TYPE_PREFIX:
        return jsonify({'error': 'INVALID_DOC_TYPE',
                        'allowed': list(DOC_TYPE_PREFIX.keys())}), 400

    year = _current_year()
    # 1) Verifikuj da deal stvarno postoji (spreci pravljenje broja za tudju entity)
    from data_layer import select_one as _dl_select_one_deal
    deal = _dl_select_one_deal('deals', {'id': deal_id})
    if not deal:
        return jsonify({'error': 'DEAL_NOT_FOUND'}), 404

    # 2) Idempotentno: postojeci aktivan broj za (docType, dealId)?
    existing = _dl_select_one(
        'document_register',
        {'doc_type': doc_type, 'entity_id': deal_id, 'revision': 0},
    )
    if existing:
        return jsonify({
            'docNumber': existing.get('doc_number'),
            'seq': existing.get('seq'),
            'revision': existing.get('revision'),
            'issuedAt': existing.get('issued_at'),
            'issuedBy': existing.get('issued_by'),
            'year': year,
            'status': 'existing'
        })

    # 3) Rezervisi novi seq atomicno
    seq = _next_seq(doc_type, year)
    number = _format_number(DOC_TYPE_PREFIX[doc_type], seq, year)
    now = _iso_now()
    username = session.get('username') or 'system'
    try:
        _insert_register(doc_type, year, seq, number, deal_id, 0, 'active', username)
    except Exception as e:
        if _is_unique_violation(e):
            logger.warning('Duplicate docNumber for deal %s %s: %s', deal_id, doc_type, e)
            seq = _next_seq(doc_type, year)
            number = _format_number(DOC_TYPE_PREFIX[doc_type], seq, year)
            try:
                _insert_register(doc_type, year, seq, number, deal_id, 0, 'active', username)
            except Exception as e2:
                logger.error('issue_document_for_deal retry failed: %s', e2)
                return jsonify({'error': 'ISSUE_FAILED_RETRY',
                                'detail': str(e2)[:200]}), 500
        else:
            logger.error('issue_document_for_deal insert failed: %s', e)
            return jsonify({'error': 'ISSUE_FAILED',
                            'detail': str(e)[:200]}), 500

    log_audit('CREATE', 'documents',
              f'Deal {deal_id}: issued {doc_type.upper()} → {number}')
    return jsonify({
        'docNumber': number, 'seq': seq, 'revision': 0,
        'year': year, 'issuedAt': now, 'issuedBy': username,
        'status': 'newly_issued'
    })


@documents_register_bp.route('/api/deals/<deal_id>/documents', methods=['GET'])
@login_required
def list_deal_documents(deal_id):
    """Vrati sve dokumente izdate za dati deal, sortirano po issuedAt DESC."""
    if not deal_id:
        return jsonify({'error': 'DEAL_ID_REQUIRED'}), 400
    rows = _dl_select(
        'document_register',
        filters={'entity_id': deal_id},
        order='-issued_at',
    ) or []
    return jsonify({
        'dealId': deal_id,
        'total': len(rows),
        'documents': [{
            'docType': r.get('doc_type'),
            'docNumber': r.get('doc_number'),
            'revision': r.get('revision'),
            'status': r.get('status'),
            'issuedAt': r.get('issued_at'),
            'issuedBy': r.get('issued_by'),
        } for r in rows]
    })
