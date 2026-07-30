"""V24.1 SUPABASE-ONLY: per-partner inventory (partner_inventory + inventory_movements)."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, session

from utils import login_required, log_audit
import supabase_store as store

logger = logging.getLogger(__name__)

inventory_bp = Blueprint('inventory_bp', __name__)


VALID_KINDS = {"IN", "OUT", "ADJUST", "RESERVE", "RELEASE"}


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _apply_movement(partner_id, product_id, kind, qty, unit):
    """Idempotentno update-uj partner_inventory za (partner, product) preko Supabase.
    Vraca (qty_on_hand, qty_reserved)."""
    from data_layer import select as _dl_select, update as _dl_update, insert as _dl_insert
    rows = _dl_select('partner_inventory',
                      filters={'partner_id': partner_id, 'product_id': product_id},
                      limit=1) or []
    now = _now_iso()

    if rows:
        row = rows[0]
        on_hand = float(row.get('qty_on_hand') or 0)
        reserved = float(row.get('qty_reserved') or 0)
    else:
        row = None
        on_hand, reserved = 0.0, 0.0

    if kind == "IN":
        on_hand += float(qty)
    elif kind == "OUT":
        on_hand -= float(qty)
    elif kind == "ADJUST":
        on_hand = float(qty)
    elif kind == "RESERVE":
        reserved += float(qty)
    elif kind == "RELEASE":
        reserved -= float(qty)
    else:
        raise ValueError(f"Unknown kind: {kind}")

    if row is None:
        _dl_insert('partner_inventory', {
            'id': str(uuid.uuid4()), 'partner_id': partner_id, 'product_id': product_id,
            'qty_on_hand': on_hand, 'qty_reserved': reserved,
            'unit': unit or '', 'last_movement_at': now,
        })
    else:
        patch = {'qty_on_hand': on_hand, 'qty_reserved': reserved,
                 'last_movement_at': now}
        if unit:
            patch['unit'] = unit
        _dl_update('partner_inventory', {'id': row.get('id')}, patch)
    return on_hand, reserved


@inventory_bp.route('/api/partners/<partner_id>/inventory', methods=['GET'])
@login_required
def list_partner_inventory(partner_id):
    """V24.1 SUPABASE-ONLY."""
    if not partner_id:
        return jsonify({'error': 'PARTNER_ID_REQUIRED'}), 400
    from data_layer import select as _dl_select
    rows = _dl_select('partner_inventory', filters={'partner_id': partner_id},
                      order='-last_movement_at') or []
    return jsonify({
        'partnerId': partner_id,
        'total': len(rows),
        'items': [{
            'id': r.get('id'), 'productId': r.get('product_id'),
            'qtyOnHand': float(r.get('qty_on_hand') or 0),
            'qtyReserved': float(r.get('qty_reserved') or 0),
            'qtyFree': float(r.get('qty_on_hand') or 0) - float(r.get('qty_reserved') or 0),
            'unit': r.get('unit') or '',
            'lastMovementAt': r.get('last_movement_at'),
        } for r in rows]
    })


@inventory_bp.route('/api/partners/<partner_id>/inventory/movements', methods=['POST'])
@login_required
def post_movement(partner_id):
    """V24.1 SUPABASE-ONLY."""
    if not partner_id:
        return jsonify({'error': 'PARTNER_ID_REQUIRED'}), 400
    p = request.get_json(silent=True) or {}
    product_id = str(p.get('product_id') or '').strip()
    kind = str(p.get('kind') or '').strip().upper()
    try:
        qty = float(p.get('qty', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'INVALID_QTY'}), 400

    if not product_id:
        return jsonify({'error': 'PRODUCT_ID_REQUIRED'}), 400
    if kind not in VALID_KINDS:
        return jsonify({'error': 'INVALID_KIND', 'allowed': sorted(VALID_KINDS)}), 400
    if kind != 'ADJUST' and qty <= 0:
        return jsonify({'error': 'QTY_MUST_BE_POSITIVE'}), 400
    if kind == 'ADJUST' and qty < 0:
        return jsonify({'error': 'ADJUST_QTY_MUST_BE_NON_NEGATIVE'}), 400

    unit = str(p.get('unit') or '').strip()
    deal_id = str(p.get('deal_id') or '').strip() or None
    note = str(p.get('note') or '').strip() or None
    username = session.get('username') or 'system'

    # Provera FK-a
    if not store.get_entity('partners', partner_id):
        return jsonify({'error': 'PARTNER_NOT_FOUND'}), 404
    if not store.get_entity('products', product_id):
        return jsonify({'error': 'PRODUCT_NOT_FOUND'}), 404
    if deal_id and not store.get_entity('deals', deal_id):
        return jsonify({'error': 'DEAL_NOT_FOUND'}), 404

    try:
        on_hand, reserved = _apply_movement(partner_id, product_id, kind, qty, unit)
        mov_id = str(uuid.uuid4())
        from data_layer import insert as _dl_insert
        _dl_insert('inventory_movements', {
            'id': mov_id, 'partner_id': partner_id, 'product_id': product_id,
            'kind': kind, 'qty': qty, 'unit': unit or '',
            'deal_id': deal_id, 'note': note,
            'created_at': _now_iso(), 'created_by': username,
        })
        log_audit('CREATE', 'inventory',
                  f'Partner {partner_id} · product {product_id} · {kind} {qty} {unit or ""} '
                  f'→ on_hand={on_hand} reserved={reserved}'
                  f'{" (deal " + deal_id + ")" if deal_id else ""}')
        return jsonify({
            'movementId': mov_id,
            'partnerId': partner_id, 'productId': product_id,
            'kind': kind, 'qty': qty, 'unit': unit or '',
            'qtyOnHand': on_hand, 'qtyReserved': reserved,
            'qtyFree': on_hand - reserved,
            'dealId': deal_id, 'note': note,
            'createdAt': _now_iso(), 'createdBy': username,
        })
    except Exception as e:
        logger.exception(f'Inventory movement failed for partner {partner_id}')
        return jsonify({'error': 'MOVEMENT_FAILED',
                        'detail': f'{type(e).__name__}: {str(e)[:200]}'}), 500


@inventory_bp.route('/api/partners/<partner_id>/inventory/movements', methods=['GET'])
@login_required
def list_movements(partner_id):
    """V24.1 SUPABASE-ONLY."""
    if not partner_id:
        return jsonify({'error': 'PARTNER_ID_REQUIRED'}), 400
    product_id = (request.args.get('product_id') or '').strip()
    deal_id = (request.args.get('deal_id') or '').strip()
    try:
        limit = min(int(request.args.get('limit') or 200), 1000)
    except ValueError:
        limit = 200

    filters = {'partner_id': partner_id}
    if product_id:
        filters['product_id'] = product_id
    if deal_id:
        filters['deal_id'] = deal_id

    from data_layer import select as _dl_select
    rows = _dl_select('inventory_movements', filters=filters,
                      order='-created_at', limit=limit) or []
    return jsonify({
        'partnerId': partner_id,
        'total': len(rows),
        'movements': [{
            'id': r.get('id'), 'productId': r.get('product_id'),
            'kind': r.get('kind'), 'qty': float(r.get('qty') or 0),
            'unit': r.get('unit') or '',
            'dealId': r.get('deal_id'), 'note': r.get('note'),
            'createdAt': r.get('created_at'), 'createdBy': r.get('created_by'),
        } for r in rows]
    })
