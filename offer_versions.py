"""OFFER VERSIONING — istorija svake izmene ponude.

Cilj: kada admin ili radnik podesi cenu, količinu, incoterm ili bilo koji
drugi detalj ponude, prethodna verzija se automatski snima u tabelu
`offer_versions`. Time se omogućava:

  1. Precizna revizija — ko je i kada šta menjao, i zbog čega.
  2. Rekonstrukcija svake starije verzije kao PDF (snapshot je pun JSON).
  3. Diff prikaz u UI-ju — koje polje je promenjeno, sa koje vrednosti na koju.
  4. Roll-back — admin može da vrati raniju verziju u aktivni offer.
  5. Sigurnost — snapshot pripada revizionoj kontroli, ne briše se ni pri
     brisanju ponude (mora zasebna admin akcija — ostavljamo za budućnost).

Snapshot se snima SAMO ako se stvarno nešto promenilo (poređenje po JSON-u
ključnih polja) — trivijalna re-snimanja iste ponude ne pune tabelu.

V25 SUPABASE-ONLY: sve operacije idu kroz `data_layer` facade. `conn`
parametar je zadržan u signature-u radi backward-compat sa starim call
sajtovima, ali se ignoriše (isto kao `utils._ensure_queue_schema(_conn=None)`
u Fazi 3-a).
"""
from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Polja čija se promena smatra značajnom i triggeruje snapshot.
# Namerno šira lista — bolje sačuvati verziju viška nego premalo.
TRACKED_FIELDS = (
    'offerNo', 'date', 'validUntil', 'customerId', 'customerName',
    'productId', 'productName', 'quantity', 'unit', 'price', 'sellingPrice',
    'currency', 'incoterm', 'pol', 'pod', 'packaging', 'leadTime',
    'paymentTerms', 'advance', 'discount', 'customVatRate', 'taxClause',
    'bankDetails', 'notes', 'items', 'services', 'weights', 'certificates',
    'detailedSpec', 'productSpec', 'productOrigin', 'origin', 'hsCode',
    'clientStatus',
)


def _canonical(obj: Any) -> Any:
    """Sortira ključeve rekurzivno da poredjenje bude stabilno."""
    if isinstance(obj, dict):
        return {k: _canonical(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, list):
        return [_canonical(x) for x in obj]
    return obj


def _diff_fields(old: dict, new: dict) -> list[str]:
    """Vrati listu naziva polja iz TRACKED_FIELDS gde su old i new različiti."""
    changed = []
    for f in TRACKED_FIELDS:
        if f not in old and f not in new:
            continue
        a = _canonical(old.get(f))
        b = _canonical(new.get(f))
        if a != b:
            changed.append(f)
    return changed


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def snapshot_if_changed(
    _conn=None,
    offer_id: str = '',
    old_offer: dict | None = None,
    new_offer: dict | None = None,
    changed_by: str = 'SYSTEM',
    changed_by_role: str = 'system',
    origin: str = 'crm',
    change_reason: str = '',
    **legacy_kwargs,
) -> str | None:
    """
    Snima snapshot STARE verzije (old_offer) u offer_versions AKO ima izmena.
    Poziva se PRE nego što se new_offer upiše nazad u offers tabelu.

    Vraća ID snapshot-a ili None ako nema izmena.

    V25: `conn` parametar se ignoriše (Supabase-only). Sve ide kroz
    `data_layer` facade. Prvi argument je zadržan kao `_conn` da bi stari
    pozivi `snapshot_if_changed(conn, offer_id, old, new, ...)` nastavili
    da rade bez izmena call sajta.

    Parametri:
      _conn        — ignorisan (legacy SQLite connection; backward-compat)
      offer_id     — ID ponude koja se menja
      old_offer    — trenutno stanje (pre izmene)
      new_offer    — novo stanje (posle izmene)
      changed_by   — user_id/partner_id
      changed_by_role — 'admin' | 'employee' | 'partner' | 'system'
      origin       — 'crm' | 'portal' | 'auto'
      change_reason — opciono, ručno uneto obrazloženje
    """
    # Backward-compat: ako je prvi argumenat string (offer_id) — stari poziv
    # je bio `snapshot_if_changed(conn, offer_id, old, new, ...)`. U tom
    # slučaju je `_conn` zapravo offer_id, pa moramo da rotiramo argumente.
    if isinstance(_conn, str) and not offer_id:
        offer_id = _conn
        _conn = None

    if not isinstance(old_offer, dict) or not isinstance(new_offer, dict):
        return None
    if not offer_id:
        return None
    changed = _diff_fields(old_offer, new_offer)
    if not changed:
        return None
    try:
        from data_layer import select as _dl_select, insert as _dl_insert
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
            'changed_by_role': changed_by_role,
            'changed_at': _now_iso(),
            'origin': origin or 'crm',
        })
        return ver_id
    except Exception as e:
        # Verzioniranje ne sme da obori sam save — samo logujemo.
        logger.warning(f"offer_versions.snapshot_if_changed failed for offer {offer_id}: {e}")
        return None


def list_versions(_conn=None, offer_id: str = '', **legacy) -> list[dict]:
    """Vraća listu verzija (bez snapshot-a — samo metapodaci za listu).

    V25: `conn` parametar se ignoriše. Sve ide kroz `data_layer`.
    """
    if isinstance(_conn, str) and not offer_id:
        offer_id = _conn
    if not offer_id:
        return []
    try:
        from data_layer import select as _dl_select
        rows = _dl_select('offer_versions',
                          filters={'offer_id': offer_id},
                          columns='id,version,changed_fields,change_reason,changed_by,changed_by_role,changed_at,origin',
                          order='-version', limit=500) or []
        out = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            cf = r.get('changed_fields') or r.get('changedFields') or ''
            out.append({
                'id': r.get('id'),
                'version': r.get('version'),
                'changedFields': cf.split(',') if cf else [],
                'changeReason': r.get('change_reason') or r.get('changeReason') or '',
                'changedBy': r.get('changed_by') or r.get('changedBy') or '',
                'changedByRole': r.get('changed_by_role') or r.get('changedByRole') or '',
                'changedAt': r.get('changed_at') or r.get('changedAt') or '',
                'origin': r.get('origin') or 'crm',
            })
        return out
    except Exception as e:
        logger.warning(f"offer_versions.list_versions({offer_id}) failed: {e}")
        return []


def get_snapshot(_conn=None, version_id: str = '', **legacy) -> dict | None:
    """Vraća pun JSON snapshot za jednu verziju.

    V25: `conn` parametar se ignoriše. Sve ide kroz `data_layer`.
    """
    if isinstance(_conn, str) and not version_id:
        version_id = _conn
    if not version_id:
        return None
    try:
        from data_layer import select_one as _dl_select_one
        row = _dl_select_one('offer_versions', {'id': version_id})
        if not row:
            return None
        snap = row.get('snapshot')
        if isinstance(snap, str):
            try: snap = json.loads(snap)
            except Exception: snap = {}
        if not isinstance(snap, dict):
            snap = {}
        return {
            'offerId': row.get('offer_id') or row.get('offerId'),
            'version': row.get('version'),
            'changedAt': row.get('changed_at') or row.get('changedAt'),
            'snapshot': snap,
        }
    except Exception as e:
        logger.warning(f"offer_versions.get_snapshot({version_id}) failed: {e}")
        return None
