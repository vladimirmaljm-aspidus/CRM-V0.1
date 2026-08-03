"""Unified search — globalna pretraga preko svih entiteta (Supabase).

Originalno je ovo bila SQLite FTS5 virtualna tabela (`search_index`). U Faza
3-c smo prešli na 100% Supabase Postgres — FTS5 nije dostupan na Supabase-u
bez dodatak ekstenzije (i tsvector/tsquery kolona u šemi). Zato sada:

  * rebuild_index() — učita sve entitete (partners/products/deals/offers +
    document_register) preko `supabase_store.list_entities` i `data_layer.select`
    u in-memory `_INDEX` dict (per entity_type → list of (id, title, body_lower)).
  * search(query) — radi simple case-insensitive substring/prefix matching
    nad in-memory indeksom. Vraća listu {entity_type, entity_id, title,
    snippet, rank} sortiranih po broju match-eva (descending).
  * index_stats() — broj zapisa po tipu + ukupno (iz in-memory _INDEX-a).

Za male i srednje CRM dataset-ove (<50k entiteta) ovo je dovoljno brzo
(sub-100ms pretraga) i jednostavnije održavati od Postgres FTS setup-a. Za
veće dataset-ove preporuka je dodati `tsvector` kolonu + GIN index direktno
na Supabase šemu — ali to je budući rad, ne deo Faza 3-c.

Sinhronizacija: rebuild_index() briše i ponovo puni in-memory index iz
Supabase izvornih tabela. Poziva se:
  - Ručno preko admin dugmeta (Settings → Diagnostics → Rebuild search)
  - Automatski jednom dnevno preko housekeeping thread-a (ako postoji)
  - Nakon batch import-a (CSV/XLSX partnera/proizvoda)
"""
import json
import logging
import threading
from typing import List, Dict

logger = logging.getLogger(__name__)


_INDEX_LOCK = threading.Lock()
_INDEX: Dict[str, List[Dict]] = {
    'partner': [],
    'product': [],
    'deal': [],
    'offer': [],
    'document': [],
}
_INDEX_BUILT = False


def _to_text(parts) -> str:
    return ' '.join([str(x) for x in parts if x])


def _build_partner_doc(p: dict) -> Dict:
    pid = p.get('id') or p.get('id_') or ''
    title = p.get('companyName') or p.get('company_name') or ''
    body_parts = [
        p.get('taxId') or p.get('tax_id'),
        p.get('regNumber') or p.get('reg_number'),
        (p.get('address') or {}).get('city'),
        (p.get('address') or {}).get('country'),
        (p.get('contact') or {}).get('person'),
        (p.get('contact') or {}).get('email'),
        (p.get('bank') or {}).get('accountNumber'),
        p.get('notes'),
        ' '.join(p.get('types') or []),
    ]
    return {'entity_type': 'partner', 'entity_id': pid,
            'title': str(title or ''), 'body': _to_text(body_parts).lower()}


def _build_product_doc(pr: dict) -> Dict:
    prid = pr.get('id') or ''
    title = pr.get('name') or ''
    body_parts = [
        pr.get('category'), pr.get('hsCode') or pr.get('hs_code'),
        pr.get('sku'), pr.get('brand'),
        pr.get('casNumber') or pr.get('cas_number'),
        pr.get('description'), pr.get('detailedSpec') or pr.get('detailed_spec'),
    ]
    return {'entity_type': 'product', 'entity_id': prid,
            'title': str(title or ''), 'body': _to_text(body_parts).lower()}


def _build_deal_doc(d: dict) -> Dict:
    did = d.get('id') or ''
    title = f"{d.get('contractId', '')} — {d.get('productName', '')}"
    body_parts = [d.get('supplierName'), d.get('buyerName'),
                  d.get('status'), d.get('remarks')]
    return {'entity_type': 'deal', 'entity_id': did,
            'title': str(title or ''), 'body': _to_text(body_parts).lower()}


def _build_offer_doc(o: dict) -> Dict:
    oid = o.get('id') or ''
    title = f"{o.get('offerNo', '')} — {o.get('productName', '')}"
    body_parts = [o.get('buyerName'), o.get('status'), o.get('notes')]
    return {'entity_type': 'offer', 'entity_id': oid,
            'title': str(title or ''), 'body': _to_text(body_parts).lower()}


def _build_document_doc(row: dict) -> Dict:
    did = str(row.get('id') or '')
    dtype = row.get('doc_type') or row.get('docType') or ''
    dno = row.get('doc_no') or row.get('docNo') or ''
    pname = row.get('partner_name') or row.get('partnerName') or ''
    dh = row.get('hash_value') or row.get('hashValue') or ''
    title = f"{dtype} {dno}".strip()
    return {'entity_type': 'document', 'entity_id': did,
            'title': str(title or ''),
            'body': f"{pname} {dh}".strip().lower()}


def rebuild_index() -> Dict:
    """Briše in-memory index i puni ga sveže iz Supabase izvora.

    Vraća dict sa brojem indeksovanih zapisa po entity_type-u.
    """
    global _INDEX_BUILT
    import supabase_store as store
    from data_layer import select as _dl_select

    counts = {'partner': 0, 'product': 0, 'deal': 0, 'offer': 0, 'document': 0}
    new_index = {k: [] for k in counts.keys()}

    # Partners / products / deals / offers — idu kroz supabase_store.list_entities
    # koji rehidrira top-level + JSONB data.
    builders = {'partners': _build_partner_doc, 'products': _build_product_doc,
                'deals': _build_deal_doc, 'offers': _build_offer_doc}
    for table, builder in builders.items():
        try:
            rows = store.list_entities(table)
            for row in rows or []:
                if not isinstance(row, dict): continue
                if not (row.get('id') or row.get('id_')): continue
                doc = builder(row)
                new_index[doc['entity_type']].append(doc)
        except Exception as e:
            logger.warning('rebuild_index: list_entities(%s) failed: %s', table, e)

    # Re-map counts properly
    counts['partner'] = len(new_index['partner'])
    counts['product'] = len(new_index['product'])
    counts['deal'] = len(new_index['deal'])
    counts['offer'] = len(new_index['offer'])

    # Document_register — read directly via data_layer
    try:
        rows = _dl_select('document_register') or []
        for row in rows:
            if not isinstance(row, dict): continue
            new_index['document'].append(_build_document_doc(row))
        counts['document'] = len(new_index['document'])
    except Exception as e:
        logger.info('rebuild_index: document_register read failed (table may not exist): %s', e)
        counts['document'] = 0

    with _INDEX_LOCK:
        _INDEX.clear()
        _INDEX.update(new_index)
        _INDEX_BUILT = True

    logger.info('search_index rebuilt: %s', counts)
    return counts


def _ensure_index():
    """Lazy rebuild ako index nikada nije napunjen."""
    global _INDEX_BUILT
    if not _INDEX_BUILT:
        try:
            rebuild_index()
        except Exception as e:
            logger.warning('search_index lazy rebuild failed: %s', e)


def search(query: str, limit: int = 20, entity_types: List[str] = None) -> List[Dict]:
    """Pretraži sve entitete. Podržava simple token matching — svi tokeni
    moraju biti pronađeni bilo gde (title ili body) da bi zapis prošao.

    Vraća listu {entity_type, entity_id, title, snippet, rank} sortiranih po
    broju match-eva (više = bolji rank)."""
    _ensure_index()
    query = (query or '').strip().lower()
    if not query:
        return []

    tokens = [t for t in query.split() if t]
    if not tokens:
        return []

    types = entity_types or list(_INDEX.keys())
    results = []
    with _INDEX_LOCK:
        for etype in types:
            if etype not in _INDEX:
                continue
            for doc in _INDEX[etype]:
                hay = (doc.get('title', '').lower() + ' ' + doc.get('body', '')).strip()
                if not hay:
                    continue
                hits = sum(1 for t in tokens if t in hay)
                if hits == 0:
                    continue
                # Snippet — prvi token koji se pojavi u body-u
                snip = ''
                body = doc.get('body', '')
                for t in tokens:
                    idx = body.find(t)
                    if idx >= 0:
                        start = max(0, idx - 30)
                        end = min(len(body), idx + len(t) + 30)
                        snip = (body[start:end] or '')[:200]
                        break
                if not snip:
                    snip = body[:200]
                results.append({
                    'entity_type': doc.get('entity_type'),
                    'entity_id': doc.get('entity_id'),
                    'title': doc.get('title'),
                    'snippet': snip,
                    'rank': -hits,  # negativan da bi više hit-ova dalo bolji (manji) rank
                })

    results.sort(key=lambda r: (r['rank'], r['title']))
    return results[:int(limit)]


def index_stats() -> Dict:
    """Vraća broj indeksovanih zapisa po tipu i ukupno (iz in-memory _INDEX-a)."""
    _ensure_index()
    with _INDEX_LOCK:
        by_type = {k: len(v) for k, v in _INDEX.items()}
    return {'by_type': by_type, 'total': sum(by_type.values())}
