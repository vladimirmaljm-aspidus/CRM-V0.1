import json
import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, session
from utils import login_required, log_audit, safe_parse, decrypt_data
import supabase_store as store

vault_bp = Blueprint('vault', __name__)

def _get_role_and_perms():
    """Ucitava rolu i permisije trenutno ulogovanog korisnika (Supabase read).

    V24.0: `permissions` se rehidrira iz top-level kolone (ili JSONB `data`)
    i već je dict ako je tako sačuvan; ako je string (legacy JSON), pokušavamo
    deserijalizaciju i fallback-ujemo na decrypt_data za starije Fernet ciphertext."""
    user = store.get_user_by_id(session.get('user_id')) or {}
    if not user:
        return None, {}
    role = user.get('role')
    perms = user.get('permissions') or {}
    if isinstance(perms, str):
        try:
            perms = json.loads(perms)
        except Exception:
            try: perms = decrypt_data(perms) or {}
            except Exception: perms = {}
    if not isinstance(perms, dict):
        perms = {}
    return role, perms

@vault_bp.route('/api/vault/save', methods=['POST'])
@login_required
def save_document_to_vault():
    payload = request.json
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"

    doc_data = {
        "id": doc_id,
        "partnerId": payload.get('partnerId'),
        "productId": payload.get('productId'),
        "docType": payload.get('docType', 'Document'),
        "fileName": payload.get('fileName', 'Document.pdf'),
        "fileUrl": payload.get('fileUrl'),
        "createdAt": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    }

    if not doc_data['partnerId'] or not doc_data['fileUrl']:
        return jsonify({"error": "Partner ID and File URL are mandatory."}), 400

    # ISPRAVKA: ova ruta je ranije potpuno zaobilazila permission model koji
    # postoji za shared_documents u routes/data.py (perm_map -> shared_documents_edit).
    # Bilo je moguce cuvati dokumente u trezoru bez ikakve dozvole.
    role, perms = _get_role_and_perms()
    if role is None:
        return jsonify({"error": "User not found"}), 401
    if role != 'admin' and not perms.get('shared_documents_edit', False):
        log_audit('SECURITY', 'vault', 'Prevented unauthorized write to document vault', is_suspicious=True)
        return jsonify({"error": "Unauthorized"}), 403

    # V24.0 SUPABASE-ONLY: insert kroz supabase_store.upsert_entity.
    # _entity_split stavlja camelCase ključeve (partnerId, productId, docType,
    # fileName, fileUrl, createdAt) u `data` JSONB kolonu; `id` ide u top-level.
    # Pri čitanju, list_entities/get_entity rehidriraju dict nazad u flat format.
    try:
        store.upsert_entity('shared_documents', doc_data)
    except Exception as e:
        log_audit('ERROR', 'vault', f'Failed to save document to vault: {str(e)[:120]}',
                  is_suspicious=False)
        return jsonify({"error": "Failed to save document.", "detail": str(e)[:200]}), 500

    log_audit('CREATE', 'vault', f"Saved {doc_data['docType']} for partner {doc_data['partnerId']}", is_suspicious=False)
    return jsonify({"status": "success", "message": "Document secured in vault.", "document": doc_data}), 200

@vault_bp.route('/api/vault/documents', methods=['GET'])
@login_required
def get_vault_documents():
    partner_id = request.args.get('partnerId')
    product_id = request.args.get('productId')

    # ISPRAVKA: ista permisija kao gore, sada i za citanje dokumenata.
    role, perms = _get_role_and_perms()
    if role is None:
        return jsonify({"error": "User not found"}), 401
    can_view = role == 'admin' or perms.get('shared_documents_view_all', False) or \
               perms.get('shared_documents_view', False) or perms.get('shared_documents_edit', False)
    if not can_view:
        log_audit('SECURITY', 'vault', 'Prevented unauthorized read of document vault', is_suspicious=True)
        return jsonify([]), 403

    # V24.0 SUPABASE-ONLY: čita shared_documents preko store.list_entities
    # (rehidrira top-level kolone + JSONB data → flat dict).
    rows = store.list_entities('shared_documents') or []

    docs = []
    for d in rows:
        if not isinstance(d, dict):
            continue
        # defensive: ako data nije rehidriran (stari zapis sa string `data`)
        d = safe_parse(d) if isinstance(d, str) else d
        if partner_id and d.get('partnerId') != partner_id:
            continue
        if product_id and d.get('productId') != product_id:
            continue
        docs.append(d)

    docs.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
    return jsonify(docs)
