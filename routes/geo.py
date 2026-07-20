"""Geo enrichment endpoints — thin caching proxy over free public APIs.

Sources:
    * REST Countries v3.1 (restcountries.com) — country details (calling code,
      currency, languages, timezone, capital, flag URL).
    * PubChem PUG-REST (pubchem.ncbi.nlm.nih.gov) — chemical CAS lookup.
    * VIES (ec.europa.eu/taxation_customs/vies) — EU VAT number validation.

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


# ---------- VIES VAT validation ----------
_VAT_CACHE = {}         # {'DE_123456789': (expiry_ts, payload)}
_VAT_TTL_S = 24 * 3600

# EU VIES podržane zemlje (27 članica + Sev. Irska XI)
VIES_COUNTRIES = {
    'AT','BE','BG','CY','CZ','DE','DK','EE','EL','ES','FI','FR','HR','HU',
    'IE','IT','LT','LU','LV','MT','NL','PL','PT','RO','SE','SI','SK','XI',
}
# Grčka koristi 'EL' kao VAT prefix, ne 'GR' — mapiramo obe strane
_VAT_ALIASES = {'GR': 'EL', 'EL': 'EL'}


def _vies_country(cc):
    cc = (cc or '').upper().strip()
    return _VAT_ALIASES.get(cc, cc)


def _fetch_vies(cc, vat):
    """SOAP call na VIES check_vat. Vraća dict sa validity + name + address
    kad je uspešno; None kad servis padne ili VAT ne postoji.
    VIES SOAP 1.1 endpoint prima jednostavan XML request."""
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:urn="urn:ec.europa.eu:taxud:vies:services:checkVat:types">'
        '<soapenv:Header/><soapenv:Body>'
        '<urn:checkVat>'
        f'<urn:countryCode>{cc}</urn:countryCode>'
        f'<urn:vatNumber>{vat}</urn:vatNumber>'
        '</urn:checkVat></soapenv:Body></soapenv:Envelope>'
    ).encode('utf-8')
    try:
        req = urllib.request.Request(
            'https://ec.europa.eu/taxation_customs/vies/services/checkVatService',
            data=envelope,
            headers={
                'Content-Type': 'text/xml; charset=utf-8',
                'SOAPAction': '',
                'User-Agent': 'AspidusCRM/1.0',
            },
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            body = r.read().decode('utf-8', 'ignore')
        # Regex-based tag extraction — bez pravog XML parsera, da izbegnemo
        # dependency na xml.etree za jednu jednostavnu response strukturu.
        import re
        def _tag(t):
            m = re.search(rf'<(?:\w+:)?{t}>([^<]*)</(?:\w+:)?{t}>', body)
            return (m.group(1).strip() if m else '')
        valid_txt = _tag('valid').lower()
        return {
            'valid': valid_txt in ('true', '1'),
            'country_code': _tag('countryCode') or cc,
            'vat_number': _tag('vatNumber') or vat,
            'request_date': _tag('requestDate'),
            'name': _tag('name'),
            'address': _tag('address').replace('\n', ', ').strip(),
            'source': 'vies.europa.eu',
        }
    except Exception:
        return None


def _validate_vat_format(cc, vat):
    """Osnovna struktura provera pre nego što VIES pogoditi.
    Vraća True ako format pluziblno odgovara toj zemlji."""
    import re
    patterns = {
        'AT': r'^U\d{8}$',
        'BE': r'^0\d{9}$',
        'BG': r'^\d{9,10}$',
        'CY': r'^\d{8}[A-Z]$',
        'CZ': r'^\d{8,10}$',
        'DE': r'^\d{9}$',
        'DK': r'^\d{8}$',
        'EE': r'^\d{9}$',
        'EL': r'^\d{9}$',
        'ES': r'^[A-Z0-9]\d{7}[A-Z0-9]$',
        'FI': r'^\d{8}$',
        'FR': r'^[A-Z0-9]{2}\d{9}$',
        'HR': r'^\d{11}$',
        'HU': r'^\d{8}$',
        'IE': r'^\d[A-Z0-9]\d{5}[A-Z]{1,2}$',
        'IT': r'^\d{11}$',
        'LT': r'^\d{9}(\d{3})?$',
        'LU': r'^\d{8}$',
        'LV': r'^\d{11}$',
        'MT': r'^\d{8}$',
        'NL': r'^\d{9}B\d{2}$',
        'PL': r'^\d{10}$',
        'PT': r'^\d{9}$',
        'RO': r'^\d{2,10}$',
        'SE': r'^\d{12}$',
        'SI': r'^\d{8}$',
        'SK': r'^\d{10}$',
        'XI': r'^\d{9}(\d{3})?$',
    }
    pat = patterns.get(cc)
    if not pat: return False
    return bool(re.match(pat, vat))


def _vat_cached(cc, vat):
    key = f'{cc}_{vat}'
    now = time.time()
    entry = _VAT_CACHE.get(key)
    if entry and entry[0] > now:
        return entry[1]
    data = _fetch_vies(cc, vat)
    if data:
        _VAT_CACHE[key] = (now + _VAT_TTL_S, data)
    return data


@geo_bp.route('/vat/validate', methods=['POST'])
@login_required
def validate_vat():
    """POST {vat_number: 'DE123456789'} — parse country prefix, VIES check.
    Vraća:
      { valid: true, name, address, country_code, vat_number, source }
      { valid: false, format_valid: false } — struktura ne odgovara
      { error: 'service_unavailable' } — VIES timeout ili down
    """
    payload = request.get_json(silent=True) or {}
    raw = str(payload.get('vat_number', '')).strip().upper().replace(' ', '').replace('-', '')
    if len(raw) < 4:
        return jsonify({'valid': False, 'reason': 'too_short'}), 200
    cc = _vies_country(raw[:2])
    vat = raw[2:]
    if cc not in VIES_COUNTRIES:
        return jsonify({'valid': False, 'reason': 'non_eu_country',
                        'country_code': cc,
                        'message': f'{cc} is not part of the EU VIES system'}), 200
    if not _validate_vat_format(cc, vat):
        return jsonify({'valid': False, 'format_valid': False,
                        'country_code': cc, 'vat_number': vat,
                        'reason': 'bad_format',
                        'message': f'{cc} VAT number format does not match'}), 200
    data = _vat_cached(cc, vat)
    if data is None:
        return jsonify({'error': 'service_unavailable',
                        'message': 'VIES service temporarily unreachable — please retry'}), 502
    return jsonify(data)
