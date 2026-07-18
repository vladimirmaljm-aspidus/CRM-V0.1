"""Geo enrichment endpoints — thin caching proxy over free public APIs.

Sources:
    * REST Countries v3.1 (restcountries.com) — country details (calling code,
      currency, languages, timezone, capital, flag URL).
    * PubChem PUG-REST (pubchem.ncbi.nlm.nih.gov) — chemical CAS lookup.

All proxies cache aggressively (24h RAM) to protect the free tiers and to keep
latency sub-100ms once warm. Cache is per-process, cleared on restart.
"""
import json
import time
import urllib.request
import urllib.parse
from flask import Blueprint, jsonify, request

from utils import login_required, log_audit

geo_bp = Blueprint('geo', __name__, url_prefix='/api/geo')

# ---------- REST Countries ----------
_CTY_CACHE = {}         # {iso2: (expiry_ts, payload)}
_CTY_TTL_S = 24 * 3600  # 24h
_HTTP_TIMEOUT_S = 6

def _fetch_country_from_restcountries(iso2):
    """One-shot GET restcountries.com/v3.1/alpha/{iso2}. Returns compact
    normalized dict or None on failure. Never raises — network errors just
    return None so caller falls back to bundled ISO_COUNTRIES on frontend."""
    try:
        url = f'https://restcountries.com/v3.1/alpha/{urllib.parse.quote(iso2)}'
        req = urllib.request.Request(url, headers={'User-Agent': 'AspidusCRM/1.0'})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as r:
            data = json.loads(r.read().decode('utf-8'))
        if not data or not isinstance(data, list):
            return None
        c = data[0]
        currencies = c.get('currencies') or {}
        cur_code = next(iter(currencies.keys()), None) if currencies else None
        cur = currencies.get(cur_code, {}) if cur_code else {}
        idd = c.get('idd') or {}
        root = idd.get('root') or ''
        suffixes = idd.get('suffixes') or []
        dial = root + (suffixes[0] if suffixes else '')
        return {
            'iso2': c.get('cca2'),
            'iso3': c.get('cca3'),
            'numeric': c.get('ccn3'),
            'name': (c.get('name') or {}).get('common'),
            'official_name': (c.get('name') or {}).get('official'),
            'capital': (c.get('capital') or [None])[0],
            'region': c.get('region'),
            'subregion': c.get('subregion'),
            'currency': cur_code,
            'currency_symbol': cur.get('symbol'),
            'currency_name': cur.get('name'),
            'dial_code': dial or None,
            'languages': list((c.get('languages') or {}).values()),
            'timezones': c.get('timezones') or [],
            'flag_url': (c.get('flags') or {}).get('svg') or (c.get('flags') or {}).get('png'),
            'source': 'restcountries.com',
        }
    except Exception:
        return None


def _country_cached(iso2):
    iso2 = (iso2 or '').upper().strip()
    if len(iso2) != 2:
        return None
    now = time.time()
    entry = _CTY_CACHE.get(iso2)
    if entry and entry[0] > now:
        return entry[1]
    data = _fetch_country_from_restcountries(iso2)
    if data:
        _CTY_CACHE[iso2] = (now + _CTY_TTL_S, data)
    return data


@geo_bp.route('/country/<iso2>', methods=['GET'])
@login_required
def get_country(iso2):
    data = _country_cached(iso2)
    if not data:
        return jsonify({'error': 'not_found', 'iso2': iso2}), 404
    return jsonify(data)


@geo_bp.route('/portal/country/<iso2>', methods=['GET'])
def portal_get_country(iso2):
    """Portal-side ekvivalent — bez CRM login-a, ali samo za ISO2 lookup.
    Ne otkriva ništa što nije javno na restcountries.com; služi da izbegne
    CORS na portal strani. Portal token nije obavezan jer sadržaj je javan."""
    data = _country_cached(iso2)
    if not data:
        return jsonify({'error': 'not_found', 'iso2': iso2}), 404
    return jsonify(data)


# ---------- PubChem CAS lookup ----------
_CHEM_CACHE = {}         # {cas: (expiry_ts, payload)}
_CHEM_TTL_S = 7 * 24 * 3600   # 1 nedelja — PubChem podaci se retko menjaju

def _fetch_chem_by_cas(cas):
    """PubChem PUG-REST: CAS # → CID → compound properties + hazards.
    Vraca None ako CAS nije poznat ili PubChem ne odgovori u roku."""
    try:
        # 1) CAS → CID
        url = f'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{urllib.parse.quote(cas)}/cids/JSON'
        req = urllib.request.Request(url, headers={'User-Agent': 'AspidusCRM/1.0'})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as r:
            j = json.loads(r.read().decode('utf-8'))
        cids = ((j.get('IdentifierList') or {}).get('CID') or [])
        if not cids:
            return None
        cid = cids[0]

        # 2) CID → osnovne osobine
        prop_props = 'MolecularFormula,MolecularWeight,IUPACName,CanonicalSMILES,InChIKey,Title'
        url2 = f'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/{prop_props}/JSON'
        req2 = urllib.request.Request(url2, headers={'User-Agent': 'AspidusCRM/1.0'})
        with urllib.request.urlopen(req2, timeout=_HTTP_TIMEOUT_S) as r:
            j2 = json.loads(r.read().decode('utf-8'))
        props = ((j2.get('PropertyTable') or {}).get('Properties') or [{}])[0]

        return {
            'cas': cas,
            'cid': cid,
            'name': props.get('Title') or props.get('IUPACName'),
            'iupac_name': props.get('IUPACName'),
            'formula': props.get('MolecularFormula'),
            'molecular_weight': props.get('MolecularWeight'),
            'smiles': props.get('CanonicalSMILES'),
            'inchi_key': props.get('InChIKey'),
            'pubchem_url': f'https://pubchem.ncbi.nlm.nih.gov/compound/{cid}',
            'source': 'pubchem.ncbi.nlm.nih.gov',
        }
    except Exception:
        return None


def _chem_cached(cas):
    cas = (cas or '').strip()
    if not cas or len(cas) > 40:
        return None
    now = time.time()
    entry = _CHEM_CACHE.get(cas)
    if entry and entry[0] > now:
        return entry[1]
    data = _fetch_chem_by_cas(cas)
    if data:
        _CHEM_CACHE[cas] = (now + _CHEM_TTL_S, data)
    return data


@geo_bp.route('/chem/cas/<path:cas>', methods=['GET'])
@login_required
def get_chem(cas):
    data = _chem_cached(cas)
    if not data:
        return jsonify({'error': 'not_found', 'cas': cas}), 404
    return jsonify(data)
