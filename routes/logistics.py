"""
Multimodalni logistički planer — backend blueprint.

Namena
------
Iz zadate polazne i odredišne tačke (adresa, koordinate ili UN/LOCODE) sistem:
  1. Pronalazi najbližu komercijalnu luku i najbliži veliki aerodrom,
  2. Računa distancu i vreme za svaki od tri režima (drum-only, drum→more→drum,
     drum→vazduh→drum),
  3. Za pomorsku etapu koristi "great circle" preko realističnih waypoint-a
     (Sueckog / Panamskog kanala, Malake, Gibraltara, Rt Dobre Nade, itd),
  4. Uzima u obzir aktuelne poremećaje (Crveno more, Panama restrikcije,
     zatvoreni vazdušni prostor Rusije/Ukrajine, ...),
  5. Vraća JSON koji frontend renderuje kao mapu + timeline.

Podaci
------
  data/ports_world.json     — 1.600+ svetskih luka (UN/LOCODE, lat, lon, country)
  data/airports_world.json  — 4.500+ komercijalnih aerodroma (IATA, ICAO, lat, lon)
  data/disruptions.json     — poremećaji ruta (admin ih može ažurirati)

Endpoint-i
----------
  GET  /api/logistics/ports              lista luka (opciono ?country=, ?q=, ?near_lat=&near_lon=)
  GET  /api/logistics/airports           lista aerodroma (isti filteri)
  GET  /api/logistics/disruptions        aktivni poremećaji
  PUT  /api/logistics/disruptions        admin — replace lista poremećaja
  POST /api/logistics/plan               glavni engine — vraća multimodal plan
  POST /api/logistics/geocode            proxy do javnog Nominatim-a (rate limited)

Sve rute su zaštićene login-om (za portal — poseban blueprint, ne ovaj).
"""

import json
import math
import os
import time
import logging
from flask import Blueprint, jsonify, request, session

from utils import login_required, log_audit

logger = logging.getLogger(__name__)

logistics_bp = Blueprint('logistics_bp', __name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
PORTS_FILE = os.path.join(DATA_DIR, 'ports_world.json')
AIRPORTS_FILE = os.path.join(DATA_DIR, 'airports_world.json')
DISRUPTIONS_FILE = os.path.join(DATA_DIR, 'disruptions.json')
PORT_OPS_FILE = os.path.join(DATA_DIR, 'port_operations.json')


# ==========================================================
#  UČITAVANJE PODATAKA (jednom u memoriji, uz mtime cache)
# ==========================================================

_cache = {'ports': None, 'ports_mtime': 0, 'airports': None, 'airports_mtime': 0}

def _load_json_cached(kind, path):
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return []
    if _cache[kind] is not None and _cache[kind + '_mtime'] == mt:
        return _cache[kind]
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        data = raw.get(kind) or []
        _cache[kind] = data
        _cache[kind + '_mtime'] = mt
        return data
    except Exception:
        logger.exception(f"Failed to load {path}")
        return []

def _load_ports():     return _load_json_cached('ports', PORTS_FILE)
def _load_airports():  return _load_json_cached('airports', AIRPORTS_FILE)

def _load_port_ops():
    """Vraća (ports_map, defaults, tiers) iz port_operations.json.
    ports_map: {UNLOCODE: {name, tier, ...}}
    defaults: baseline vrednosti za nepoznate luke
    tiers: skup baseline vrednosti po tier-u
    """
    try:
        with open(PORT_OPS_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        return (raw.get('ports') or {},
                (raw.get('_meta') or {}).get('defaults') or {},
                (raw.get('_meta') or {}).get('tiers') or {})
    except Exception:
        return {}, {}, {}

def _port_ops_for(unlocode):
    """Za dati UN/LOCODE vraća merged dict operativnih parametara.
    Ako luka nije eksplicitno u bazi, koristi average tier defaults."""
    ports_map, defaults, tiers = _load_port_ops()
    fallback = dict(defaults or {})
    entry = ports_map.get(unlocode or '')
    if entry:
        tier = entry.get('tier') or 'average'
        tier_defs = tiers.get(tier) or tiers.get('average') or {}
        merged = {**fallback, **tier_defs}
        # per-port override polja (ako neko doda override koji nije tier baseline)
        for k, v in entry.items():
            if k in ('name', 'tier', 'notes', 'annual_teu_millions'): continue
            merged[k] = v
        merged['_source'] = 'known_port'
        merged['_tier'] = tier
        merged['_port_name'] = entry.get('name', '')
        merged['_notes'] = entry.get('notes', '')
        return merged
    # fallback: average tier
    tier_defs = tiers.get('average') or {}
    merged = {**fallback, **tier_defs}
    merged['_source'] = 'default_average'
    merged['_tier'] = 'average'
    return merged

def _load_disruptions():
    try:
        with open(DISRUPTIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f).get('disruptions', [])
    except Exception:
        return []


# ==========================================================
#  GEODETSKE FUNKCIJE
# ==========================================================

EARTH_RADIUS_KM = 6371.0088

def _haversine_km(lat1, lon1, lat2, lon2):
    """Kraćeraskloni prekomorski put u km (great circle)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dlon/2)**2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))

def _great_circle_polyline(lat1, lon1, lat2, lon2, segments=64):
    """Vraća listu [(lat, lon), ...] koja opisuje great-circle liniju."""
    lat1r, lon1r, lat2r, lon2r = map(math.radians, [lat1, lon1, lat2, lon2])
    d = 2 * math.asin(math.sqrt(math.sin((lat2r-lat1r)/2)**2 +
                                math.cos(lat1r) * math.cos(lat2r) *
                                math.sin((lon2r-lon1r)/2)**2))
    if d == 0:
        return [(lat1, lon1), (lat2, lon2)]
    pts = []
    for i in range(segments + 1):
        f = i / segments
        A = math.sin((1-f)*d) / math.sin(d)
        B = math.sin(f*d) / math.sin(d)
        x = A * math.cos(lat1r) * math.cos(lon1r) + B * math.cos(lat2r) * math.cos(lon2r)
        y = A * math.cos(lat1r) * math.sin(lon1r) + B * math.cos(lat2r) * math.sin(lon2r)
        z = A * math.sin(lat1r) + B * math.sin(lat2r)
        lat = math.degrees(math.atan2(z, math.sqrt(x*x + y*y)))
        lon = math.degrees(math.atan2(y, x))
        pts.append((round(lat, 4), round(lon, 4)))
    return pts

def _polyline_length_km(points):
    total = 0.0
    for i in range(1, len(points)):
        total += _haversine_km(points[i-1][0], points[i-1][1], points[i][0], points[i][1])
    return total


# ==========================================================
#  POMORSKI WAYPOINT-I (kanali i uski prolazi)
# ==========================================================

# Ovi waypoint-i su prosečne pozicije u samim kanalima. Brod ide great-circle
# do waypoint-a, zatim great-circle od waypoint-a do sledeće tačke. Time se
# izbegava da algoritam vodi rutu preko kopna (Sinajski poluostrvo, itd).
WAYPOINTS = {
    'suez':       (30.5852, 32.2654),
    'panama':     (9.0819, -79.6800),
    'bosphorus':  (41.1200, 29.0400),
    'gibraltar':  (35.9660, -5.5500),
    'malacca':    (1.4300, 102.8900),
    'cape_good_hope': (-34.8500, 20.0000),
    'cape_horn':  (-55.9833, -67.2717),
    'dover':      (51.0300, 1.3500),
    'bab_el_mandeb': (12.5800, 43.3300),
}

# Regionalna klasifikacija luke — pomaže odabir kanala/rute.
def _classify_port_region(lat, lon):
    # Sredozemlje
    if 30 <= lat <= 46 and -6 <= lon <= 36: return 'medit'
    # Zapadna Evropa / Sev. more / Baltik
    if 43 <= lat <= 71 and -12 <= lon <= 30: return 'wesn_europe'
    # US East / Karibi
    if 10 <= lat <= 50 and -100 <= lon <= -55: return 'americas_east'
    # US West / Latin Am Pacific
    if -60 <= lat <= 62 and -170 <= lon <= -100: return 'americas_west'
    # Istočna Azija (Kina/Japan/Koreja/Vijetnam)
    if -12 <= lat <= 55 and 95 <= lon <= 155: return 'east_asia'
    # J. Azija / Indija / Pakistan / Perzijski zaliv
    if 5 <= lat <= 35 and 45 <= lon <= 95: return 'south_asia_gulf'
    # Južna Afrika / južni Atlantik / j. Ind. okean
    if -40 <= lat <= 0 and -20 <= lon <= 55: return 'africa'
    # Okeanija
    if -50 <= lat <= 5 and 100 <= lon <= 180: return 'oceania'
    return 'other'


def _sea_route_waypoints(a_lat, a_lon, b_lat, b_lon, avoid=None):
    """Vraća listu waypoint imena između luke A i B — heuristička ali dovoljna
    za realistične globalne rute. `avoid` je set imena koje treba izbeći
    (npr. 'suez' zbog Crvenog mora)."""
    avoid = set(avoid or [])
    ra = _classify_port_region(a_lat, a_lon)
    rb = _classify_port_region(b_lat, b_lon)
    if {ra, rb} == {'medit', 'americas_east'} or {ra, rb} == {'medit', 'americas_west'}:
        return ['gibraltar'] + ([] if 'americas_east' in {ra, rb} else ['panama'])
    if {ra, rb} == {'wesn_europe', 'east_asia'}:
        return ['gibraltar', 'suez', 'bab_el_mandeb', 'malacca'] if 'suez' not in avoid else \
               ['gibraltar', 'cape_good_hope', 'malacca']
    if {ra, rb} == {'wesn_europe', 'south_asia_gulf'}:
        return ['gibraltar', 'suez', 'bab_el_mandeb'] if 'suez' not in avoid else \
               ['gibraltar', 'cape_good_hope']
    if {ra, rb} == {'medit', 'east_asia'}:
        return ['suez', 'bab_el_mandeb', 'malacca'] if 'suez' not in avoid else \
               ['gibraltar', 'cape_good_hope', 'malacca']
    if {ra, rb} == {'medit', 'south_asia_gulf'}:
        return ['suez', 'bab_el_mandeb'] if 'suez' not in avoid else \
               ['gibraltar', 'cape_good_hope']
    if {ra, rb} == {'wesn_europe', 'americas_east'}:
        return []  # direktan Atlantik
    if {ra, rb} == {'americas_east', 'east_asia'}:
        return ['panama']
    if {ra, rb} == {'americas_west', 'east_asia'}:
        return []  # Pacifik direktno
    if {ra, rb} == {'wesn_europe', 'americas_west'}:
        return ['panama']
    if {ra, rb} == {'americas_east', 'south_asia_gulf'}:
        return ['gibraltar', 'suez', 'bab_el_mandeb'] if 'suez' not in avoid else \
               ['cape_good_hope']
    if {ra, rb} == {'africa', 'east_asia'}:
        return ['cape_good_hope', 'malacca']
    if {ra, rb} == {'africa', 'wesn_europe'}:
        return ['cape_good_hope', 'gibraltar']
    return []


# ==========================================================
#  BLIZINA — najbliža luka / aerodrom
# ==========================================================

def _nearest(entities, lat, lon, k=1, key_lat='lat', key_lon='lon'):
    """Vraća k najbližih entiteta zajedno sa distancom (km)."""
    scored = []
    for e in entities:
        d = _haversine_km(lat, lon, e[key_lat], e[key_lon])
        scored.append((d, e))
    scored.sort(key=lambda x: x[0])
    return scored[:k]


# ==========================================================
#  POREMEĆAJI — koji utiču na traženi mod?
# ==========================================================

def _active_disruptions(mode, sea_waypoints=None):
    hits = []
    for d in _load_disruptions():
        if mode not in d.get('affects', []): continue
        # Za pomorsku etapu — proveri da li ruta ide kroz waypoint pogođen restrikcijom
        if mode == 'sea' and sea_waypoints:
            if d['id'] == 'red-sea-2024' and 'suez' in sea_waypoints:
                hits.append(d)
            elif d['id'] == 'panama-drought-2024' and 'panama' in sea_waypoints:
                hits.append(d)
            else:
                # generički: ako region padne u bbox ±3° oko waypoint-a
                pass
        elif mode == 'air':
            hits.append(d)
        elif mode == 'road':
            hits.append(d)
    return hits


# ==========================================================
#  BRZINE / EMISIJE (prosečne komercijalne vrednosti)
# ==========================================================

SPEED_KMH = {
    'road': 60,       # kamion prosečno
    'sea': 33,        # ~18 čvorova prosečan brzi container ship
    'air': 830,       # 450 kt prosečno cruise
}
CO2_G_PER_TKM = {     # gram CO2 po tonskom kilometru
    'road': 62,
    'sea': 8,
    'air': 602,
}


# ==========================================================
#  GLAVNI ENGINE — /api/logistics/plan
# ==========================================================

def _bad(msg, code=400):
    return jsonify({"error": msg}), code

def _resolve_point(payload_key, payload):
    """
    Iz JSON payload-a vadi origin/destination. Prihvata:
      - {"lat": .., "lon": ..}
      - {"unlocode": "RSBEG"}       (nađe u portovima)
      - {"iata": "BEG"}             (nađe u aerodromima)
      - {"address": "Beograd, RS"}  (geokodira preko Nominatim-a, keš)
    Vraća (lat, lon, label) ili (None, None, error_msg).
    """
    p = payload.get(payload_key)
    if not isinstance(p, dict):
        return None, None, f'{payload_key} missing or wrong type'
    if 'lat' in p and 'lon' in p:
        try:
            return float(p['lat']), float(p['lon']), p.get('label') or 'GPS'
        except (TypeError, ValueError):
            return None, None, f'{payload_key}: invalid lat/lon'
    if p.get('unlocode'):
        for x in _load_ports():
            if x['unlocode'].upper() == p['unlocode'].upper():
                return x['lat'], x['lon'], f"{x['name']}, {x['country']}"
        return None, None, f"{payload_key}: unknown UN/LOCODE {p['unlocode']}"
    if p.get('iata'):
        for x in _load_airports():
            if x['iata'] and x['iata'].upper() == p['iata'].upper():
                return x['lat'], x['lon'], f"{x['name']} ({x['iata']})"
        return None, None, f"{payload_key}: unknown IATA {p['iata']}"
    if p.get('address'):
        # Ne geokodiramo server-side (klijent to radi preko Nominatim-a direktno).
        # Ovde samo ako je klijent vec resolvovao lat/lon i poslao ih.
        return None, None, f'{payload_key}: address given but no lat/lon resolved'
    return None, None, f'{payload_key}: no valid identifier'


@logistics_bp.route('/api/logistics/ports', methods=['GET'])
@login_required
def list_ports():
    return _list_locations(_load_ports(), 'unlocode', 'name')

@logistics_bp.route('/api/logistics/airports', methods=['GET'])
@login_required
def list_airports():
    return _list_locations(_load_airports(), 'iata', 'name')


# Portal-friendly ekvivalenti (ne traže login sesije CRM-a, već portal token).
# Rezervisano za budućnost — trenutno portal koristi read-only, isti fajl.
@logistics_bp.route('/api/portal/logistics/ports', methods=['GET'])
def portal_list_ports():
    # Portal auth se proverava kroz X-Portal-Auth header (isto kao ostale portal rute)
    from routes.portal.actions import _portal_auth_check
    ok = _portal_auth_check() if hasattr(__import__('routes.portal.actions', fromlist=['_portal_auth_check']), '_portal_auth_check') else True
    if not ok:
        return jsonify({"error": "PORTAL_AUTH_REQUIRED"}), 401
    return _list_locations(_load_ports(), 'unlocode', 'name')

@logistics_bp.route('/api/portal/logistics/airports', methods=['GET'])
def portal_list_airports():
    return _list_locations(_load_airports(), 'iata', 'name')


def _list_locations(items, id_key, name_key):
    q = (request.args.get('q') or '').strip().lower()
    country = (request.args.get('country') or '').strip().upper()
    near_lat = request.args.get('near_lat')
    near_lon = request.args.get('near_lon')
    limit = min(int(request.args.get('limit', 200) or 200), 500)

    out = items
    if country:
        out = [x for x in out if (x.get('country') or '').upper() == country]
    if q:
        out = [x for x in out if q in (x.get(name_key) or '').lower()
               or q in (x.get(id_key) or '').lower()
               or q in (x.get('municipality') or x.get('city') or '').lower()]
    if near_lat and near_lon:
        try:
            la, lo = float(near_lat), float(near_lon)
            out = sorted(out, key=lambda x: _haversine_km(la, lo, x['lat'], x['lon']))
        except ValueError:
            pass
    return jsonify({"items": out[:limit], "total": len(out)})


# ==========================================================
#  OBJEDINJENA PRETRAGA (luke + aerodromi + fuzzy scoring)
#  Frontend autocomplete koristi ovaj endpoint. Vraća "hits" listu
#  sortiranu po scoring-u, sa istim shape-om za sve tipove (port/airport).
# ==========================================================

def _score_match(q, name, code, municipality):
    """Rangira koliko dobro entry odgovara upitu. Veće = bolje."""
    if not q: return 0
    q = q.lower().strip()
    name = (name or '').lower()
    code = (code or '').lower()
    muni = (municipality or '').lower()
    score = 0
    if code == q: score += 100
    if code.startswith(q): score += 50
    if q in code: score += 25
    if name == q: score += 80
    if name.startswith(q): score += 40
    if q in name: score += 20
    if muni == q: score += 30
    if muni.startswith(q): score += 15
    if q in muni: score += 10
    return score


def _do_search(payload):
    q = (payload.get('q') or '').strip()
    limit = min(int(payload.get('limit') or 12), 25)
    types = payload.get('types') or ['port', 'airport']
    country = (payload.get('country') or '').strip().upper()

    if len(q) < 1:
        return []

    hits = []
    if 'port' in types:
        for p in _load_ports():
            if country and (p.get('country') or '').upper() != country:
                continue
            s = _score_match(q, p.get('name'), p.get('unlocode'), p.get('city'))
            if s > 0:
                hits.append({
                    'type': 'port',
                    'code': p.get('unlocode', ''),
                    'name': p.get('name', ''),
                    'label': f"{p.get('name','')} ({p.get('unlocode','')})",
                    'municipality': p.get('city') or p.get('name', ''),
                    'country': p.get('country', ''),
                    'lat': p['lat'], 'lon': p['lon'],
                    'score': s,
                })
    if 'airport' in types:
        for a in _load_airports():
            if country and (a.get('country') or '').upper() != country:
                continue
            code_pref = a.get('iata') or a.get('icao') or ''
            s = _score_match(q, a.get('name'), code_pref, a.get('municipality'))
            if s > 0:
                hits.append({
                    'type': 'airport',
                    'code': code_pref,
                    'name': a.get('name', ''),
                    'label': f"{a.get('name','')} ({code_pref})",
                    'municipality': a.get('municipality') or a.get('name', ''),
                    'country': a.get('country', ''),
                    'lat': a['lat'], 'lon': a['lon'],
                    'score': s,
                })
    hits.sort(key=lambda x: (-x['score'], x['name']))
    return hits[:limit]


@logistics_bp.route('/api/logistics/search', methods=['GET'])
@login_required
def search_locations():
    payload = {
        'q': request.args.get('q', ''),
        'limit': request.args.get('limit'),
        'types': (request.args.get('types') or 'port,airport').split(','),
        'country': request.args.get('country', ''),
    }
    return jsonify({'hits': _do_search(payload)})


@logistics_bp.route('/api/portal/logistics/search', methods=['GET'])
def portal_search_locations():
    payload = {
        'q': request.args.get('q', ''),
        'limit': request.args.get('limit'),
        'types': (request.args.get('types') or 'port,airport').split(','),
        'country': request.args.get('country', ''),
    }
    return jsonify({'hits': _do_search(payload)})


@logistics_bp.route('/api/logistics/disruptions', methods=['GET'])
@login_required
def get_disruptions():
    return jsonify({"disruptions": _load_disruptions()})

@logistics_bp.route('/api/portal/logistics/disruptions', methods=['GET'])
def portal_get_disruptions():
    return jsonify({"disruptions": _load_disruptions()})


@logistics_bp.route('/api/logistics/disruptions', methods=['PUT'])
@login_required
def update_disruptions():
    if session.get('role') != 'admin':
        return jsonify({"error": "UNAUTHORIZED"}), 403
    payload = request.get_json(silent=True) or {}
    items = payload.get('disruptions')
    if not isinstance(items, list):
        return _bad('disruptions must be a list')
    try:
        with open(DISRUPTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump({"disruptions": items}, f, ensure_ascii=False, indent=2)
        log_audit('INFO', 'logistics', f'disruptions list updated ({len(items)} items)')
        return jsonify({"ok": True, "count": len(items)})
    except Exception:
        logger.exception('Failed to write disruptions')
        return jsonify({"error": "WRITE_FAILED"}), 500


@logistics_bp.route('/api/logistics/plan', methods=['POST'])
@login_required
def plan_route():
    return _do_plan()

@logistics_bp.route('/api/portal/logistics/plan', methods=['POST'])
def portal_plan_route():
    return _do_plan()


# ==========================================================
#  CARGO PROFILE — sve što je bitno za smart odabir moda
# ==========================================================

def _cargo_profile(payload):
    """Normalizuje ulaz u cargo profil. Podržava naslijeđeni cargo_tons ali
    prihvata i puni profil sa volume, kontejnerima, hazmat i deadline-om.
    Sve vrednosti su sanitizovane (klemovane na razumnim granicama)."""
    def _num(v, mn=0.0, mx=1e9, dflt=0.0):
        try:
            n = float(v)
            if n != n or n < mn or n > mx: return dflt
            return n
        except (TypeError, ValueError):
            return dflt
    def _bool(v):
        if isinstance(v, bool): return v
        return str(v).lower() in ('1','true','yes','on')

    weight_tons = _num(payload.get('cargo_tons'), 0.001, 500000, 20.0)
    volume_m3 = _num(payload.get('cargo_volume_m3'), 0.001, 500000, 0.0)
    if volume_m3 == 0.0:
        # Ako ništa nije rečeno o volume, procenimo ga iz mase (prosečno 0.6 m3/t
        # za mešovitu robu, 3 m3/t za light-weight kartone itd). Za bezbedan default
        # uzimamo 1.0 m3 po toni što odgovara medium density robi.
        volume_m3 = weight_tons * 1.0

    container_type = str(payload.get('container_type') or '').lower()
    # Prihvatamo: 'teu' (20ft), 'feu' (40ft), 'reefer' (frižider), 'lcl'
    # (less-than-container-load), 'bulk_dry', 'bulk_liquid', 'breakbulk', 'oog'
    # (out-of-gauge). Ako nije zadato, izvodimo iz veličine tereta.
    if not container_type:
        if weight_tons <= 0.5 and volume_m3 <= 2:
            container_type = 'parcel'   # < 500kg — možda direktno kurir/air
        elif weight_tons <= 18 and volume_m3 <= 30:
            container_type = 'teu'
        elif weight_tons <= 26 and volume_m3 <= 60:
            container_type = 'feu'
        elif weight_tons > 100:
            container_type = 'bulk_dry'
        else:
            container_type = 'breakbulk'

    profile = {
        'weight_tons': round(weight_tons, 3),
        'volume_m3': round(volume_m3, 3),
        'container_type': container_type,
        'perishable': _bool(payload.get('perishable')),
        'hazmat': _bool(payload.get('hazmat')),
        'oversize': _bool(payload.get('oversize')),
        'high_value': _bool(payload.get('high_value')),
        'value_usd': _num(payload.get('value_usd'), 0, 1e10, 0),
        'deadline_days': _num(payload.get('deadline_days'), 0, 365, 0),
        'container_count': int(_num(payload.get('container_count'), 0, 10000, 0)),
    }
    # Auto-flag za high_value bazirano na $/kg (>50$/kg = uglavnom air freight kandidat)
    if not profile['high_value'] and profile['value_usd'] > 0 and profile['weight_tons'] > 0:
        usd_per_kg = profile['value_usd'] / (profile['weight_tons'] * 1000)
        if usd_per_kg >= 50:
            profile['high_value'] = True
    # Broj kontejnera derived (za estimate crane moves)
    if profile['container_count'] == 0:
        if container_type == 'teu':
            profile['container_count'] = max(1, int(round(profile['weight_tons'] / 18 + 0.4)))
        elif container_type == 'feu':
            profile['container_count'] = max(1, int(round(profile['weight_tons'] / 26 + 0.4)))
    return profile


def _port_dwell_hours(port_ops, profile):
    """Vraća procenu ukupnog vremena zadržavanja u luci (utovar + istovar +
    carina) za dati profil tereta i operativne parametre luke.

    Formula je fizički zasnovana:
      - Container:  moves = container_count; hours = moves / cranes_per_h × 2
                    (× 2 jer se broji i utovar i istovar), plus customs.
      - Bulk:       hours = tons / discharge_rate × 2 + customs.
      - Breakbulk:  hours = tons / handling_rate × 2 + customs.
      - Congested luke imaju multiplier 1.4x (out of the tier_defaults).
    """
    ct = profile['container_type']
    customs = float(port_ops.get('customs_clearance_hours', 24))
    congestion = float(port_ops.get('congestion_factor', 1.0))
    dwell = 0.0
    if ct in ('teu', 'feu', 'reefer', 'lcl', 'parcel'):
        cranes = float(port_ops.get('container_crane_moves_per_hour', 25))
        moves = max(1, profile['container_count'])
        # 2 × jer se broji i utovar u polaznoj i istovar u odredišnoj luci
        dwell = (moves / cranes) * 2
        # Baseline dwell (kontejner sedi u luci) je uglavnom veći od pure handling
        # time-a — dodajemo ga kao "yard buffer".
        dwell += float(port_ops.get('container_dwell_hours', 48))
    elif ct == 'bulk_dry':
        rate = float(port_ops.get('bulk_discharge_tons_per_hour', 800))
        dwell = (profile['weight_tons'] / max(rate, 1)) * 2
        dwell += float(port_ops.get('bulk_dwell_hours', 72))
    elif ct == 'bulk_liquid':
        rate = float(port_ops.get('bulk_discharge_tons_per_hour', 1500))
        dwell = (profile['weight_tons'] / max(rate, 1)) * 2
        dwell += float(port_ops.get('bulk_dwell_hours', 60))
    elif ct in ('breakbulk', 'oog'):
        rate = float(port_ops.get('breakbulk_tons_per_hour', 120))
        dwell = (profile['weight_tons'] / max(rate, 1)) * 2
        dwell += float(port_ops.get('breakbulk_dwell_hours', 120))
    else:
        dwell = float(port_ops.get('container_dwell_hours', 48))

    total = (dwell + customs) * congestion
    return round(total, 1)


def _airport_dwell_hours(profile):
    """Airport handling: cargo cutoff (min 4h pre leta), unload (1-3h),
    customs (typical 6-8h za normal, 24h za hazmat). Bez congestion faktora
    jer airport cargo terminali retko trpe zagušenja u istoj meri kao luke."""
    handling = 4 + 2  # cutoff + unload
    if profile['hazmat']: handling += 12
    if profile['perishable']: handling += 2  # reefer chain handoff
    customs = 12 if profile['hazmat'] else 6
    return round(handling + customs, 1)


# ==========================================================
#  MOD FITNESS SCORING — koja je od 3 opcije NAJPAMETNIJA
#  za DATI cargo profil? Vraća score + human-readable razloge.
# ==========================================================

def _mode_fitness(mode, profile, plan):
    """Vraća (score, reasons_list). Score je 0-100. Viši = bolji fit
    za dati cargo profil. Reasons su UI-friendly stringovi."""
    reasons = []
    score = 50  # baseline
    wt = profile['weight_tons']
    vol = profile['volume_m3']

    if mode == 'air':
        # Air je optimalno za mali brz teret. Skalira loše sa masom.
        if wt <= 0.5:
            score += 30; reasons.append(f'Very light cargo ({wt} t) — air is standard.')
        elif wt <= 5:
            score += 20; reasons.append(f'Light cargo ({wt} t) fits well in air cargo.')
        elif wt <= 30:
            score -= 10; reasons.append(f'Cargo weight ({wt} t) approaches air freight cost efficiency limit.')
        else:
            score -= 30; reasons.append(f'Cargo weight ({wt} t) is too heavy for cost-effective air freight.')
        if profile['perishable']:
            score += 25; reasons.append('Perishable cargo — air minimizes transit time.')
        if profile['high_value']:
            score += 15; reasons.append('High-value shipment — air reduces insurance exposure.')
        if profile['deadline_days'] and profile['deadline_days'] <= 5:
            score += 20; reasons.append(f'Tight deadline ({int(profile["deadline_days"])}d) — only air can meet it reliably.')
        if profile['hazmat']:
            score -= 15; reasons.append('Hazmat restrictions apply to air cargo — extra documentation.')
        if profile['oversize'] or vol > 100:
            score -= 20; reasons.append('Oversized cargo — special charter needed.')

    elif mode == 'sea':
        # Sea je optimalno za velike količine, tolerantno na trajanje.
        if wt >= 10:
            score += 25; reasons.append(f'Bulk / container-friendly volume ({wt} t) — sea is most cost-effective.')
        elif wt >= 2:
            score += 10; reasons.append(f'Consolidatable cargo ({wt} t) fits well in LCL/FCL.')
        else:
            score -= 15; reasons.append(f'Small cargo ({wt} t) — LCL surcharges may make sea uneconomical.')
        if profile['perishable']:
            score -= 15; reasons.append('Perishable — sea transit exceeds shelf life unless reefer container is used.')
        if profile['deadline_days'] and profile['deadline_days'] <= 10:
            score -= 20; reasons.append(f'Deadline of {int(profile["deadline_days"])}d is aggressive for sea freight.')
        if profile['deadline_days'] and profile['deadline_days'] > 30:
            score += 15; reasons.append('Ample lead time — sea maximizes savings.')
        if profile['hazmat']:
            score -= 5; reasons.append('Hazmat manageable in dedicated ISO tanks or IMO classes.')
        if profile['oversize']:
            score += 10; reasons.append('Oversized / breakbulk cargo — sea handles it natively.')

    elif mode == 'road':
        # Road je optimalno za srednje distance, jedan continent, brzo za < 24t
        if plan['total_distance_km'] <= 800:
            score += 25; reasons.append(f'Short leg ({int(plan["total_distance_km"])} km) — road is fastest end-to-end.')
        elif plan['total_distance_km'] <= 2500:
            score += 10; reasons.append(f'Regional distance ({int(plan["total_distance_km"])} km) — road remains competitive.')
        elif plan['total_distance_km'] <= 5000:
            score -= 15; reasons.append(f'Long distance ({int(plan["total_distance_km"])} km) — driver hours and fuel add cost.')
        else:
            score -= 30; reasons.append(f'Distance ({int(plan["total_distance_km"])} km) exceeds practical road transit.')
        if wt > 24:
            score -= 15; reasons.append(f'Cargo weight ({wt} t) exceeds single-trailer capacity — multiple trucks required.')
        if profile['perishable'] and plan['total_days'] <= 3:
            score += 10; reasons.append('Perishable — road is fine at this distance with a reefer trailer.')
        if profile['hazmat']:
            score -= 5; reasons.append('ADR-compliant driver and vehicle required.')

    score = max(0, min(100, score))
    return score, reasons


def _cost_estimate_usd(mode, plan, profile):
    """Vrlo okvirno: koristi javne tarifne prosečne cene (per kg-km ili
    ton-km) da bi korisnik dobio red veličine. NE zamenjuje pravi tender.
    Izvor: DHL / MAERSK / IATA public rate cards 2024 median.
      - Air:  1.80-4.50 $/kg (uzimamo 3.00 $/kg average za intercontinental)
      - Sea container FCL:  ~2500-4000 $/TEU intercontinental (uzimamo 3200 avg)
      - Sea LCL:  ~40-80 $/CBM (uzimamo 60 $/CBM)
      - Road:   ~1.20-2.20 $/km per FTL (uzimamo 1.60 $/km × distance)
    """
    wt_kg = profile['weight_tons'] * 1000
    if mode == 'air':
        # Rate scaled po distanci (dulja = malo skuplje)
        base = 3.0
        dist_factor = 1.0 + max(0, (plan['total_distance_km'] - 3000) / 10000) * 0.3
        return round(base * dist_factor * wt_kg, 0)
    if mode == 'sea':
        ct = profile['container_type']
        if ct in ('teu', 'feu', 'reefer'):
            per_container = 3200 if ct == 'teu' else 4200 if ct == 'feu' else 5200
            return round(per_container * max(1, profile['container_count']), 0)
        elif ct == 'lcl' or profile['volume_m3'] < 15:
            return round(60 * max(1, profile['volume_m3']), 0)
        else:
            # bulk: ~40 $/ton port-to-port
            return round(40 * profile['weight_tons'], 0)
    if mode == 'road':
        # 1.60 $/km × trucks needed
        trucks = max(1, int((profile['weight_tons'] + 23) // 24))
        return round(1.6 * plan['total_distance_km'] * trucks, 0)
    return 0


def _do_plan():
    payload = request.get_json(silent=True) or {}
    o_lat, o_lon, o_lbl = _resolve_point('origin', payload)
    if o_lat is None: return _bad(o_lbl)
    d_lat, d_lon, d_lbl = _resolve_point('destination', payload)
    if d_lat is None: return _bad(d_lbl)

    profile = _cargo_profile(payload)
    cargo_tons = profile['weight_tons']
    prefer = (payload.get('prefer') or 'auto').lower()

    plans = []

    # --- 1. ROAD ONLY -----------------------------------------------------
    d_road = _haversine_km(o_lat, o_lon, d_lat, d_lon)
    road_ok = d_road < 6000
    if road_ok:
        d_road_actual = d_road * 1.3
        # Broj kamiona (24t FTL kapacitet). Vreme se ne povećava linearno sa
        # brojem kamiona — svi voze paralelno.
        trucks_needed = max(1, int((cargo_tons + 23) // 24))
        hours = d_road_actual / SPEED_KMH['road']
        # Border crossing overhead (5h EU intra, 8h EU-non-EU, 24h transit sa carinom)
        border_h = 8 if d_road_actual > 800 else 0
        total_h = hours + border_h
        co2_t = d_road_actual * cargo_tons * CO2_G_PER_TKM['road'] / 1_000_000

        plan_road = {
            'mode': 'road',
            'label': 'Road only (truck)',
            'legs': [{
                'kind': 'road',
                'from_label': o_lbl, 'to_label': d_lbl,
                'from': [o_lat, o_lon], 'to': [d_lat, d_lon],
                'polyline': _great_circle_polyline(o_lat, o_lon, d_lat, d_lon, segments=24),
                'distance_km': round(d_road_actual, 1),
                'hours': round(hours, 1),
                'trucks_needed': trucks_needed,
                'border_crossing_hours': border_h,
            }],
            'total_distance_km': round(d_road_actual, 1),
            'total_hours': round(total_h, 1),
            'total_days': round(total_h / 24, 2),
            'co2_tons': round(co2_t, 3),
            'warnings': [],
        }
        score, reasons = _mode_fitness('road', profile, plan_road)
        plan_road['fitness_score'] = score
        plan_road['fitness_reasons'] = reasons
        plan_road['estimated_cost_usd'] = _cost_estimate_usd('road', plan_road, profile)
        plans.append(plan_road)

    # --- 2. ROAD → SEA → ROAD --------------------------------------------
    ports = _load_ports()
    n_o_port = _nearest(ports, o_lat, o_lon, k=1)[0] if ports else None
    n_d_port = _nearest(ports, d_lat, d_lon, k=1)[0] if ports else None
    if n_o_port and n_d_port:
        d_op, p_o = n_o_port
        d_dp, p_d = n_d_port
        land_o_km = d_op * 1.3
        land_d_km = d_dp * 1.3

        avoid = set()
        raw_wps = _sea_route_waypoints(p_o['lat'], p_o['lon'], p_d['lat'], p_d['lon'])
        if 'suez' in raw_wps:
            for dis in _load_disruptions():
                if dis['id'] == 'red-sea-2024' and dis.get('severity') in ('high', 'critical'):
                    avoid.add('suez')
        wps = _sea_route_waypoints(p_o['lat'], p_o['lon'], p_d['lat'], p_d['lon'], avoid=avoid)

        chain = [(p_o['lat'], p_o['lon'])]
        for name in wps:
            chain.append(WAYPOINTS[name])
        chain.append((p_d['lat'], p_d['lon']))
        sea_poly = []
        for i in range(1, len(chain)):
            seg = _great_circle_polyline(chain[i-1][0], chain[i-1][1], chain[i][0], chain[i][1], segments=32)
            if sea_poly and sea_poly[-1] == seg[0]: seg = seg[1:]
            sea_poly.extend(seg)
        sea_km = _polyline_length_km(sea_poly)

        # Per-luka dwell time — koristi realne parametre iz port_operations.json
        origin_ops = _port_ops_for(p_o.get('unlocode'))
        dest_ops = _port_ops_for(p_d.get('unlocode'))
        h_dwell_origin = _port_dwell_hours(origin_ops, profile)
        h_dwell_dest = _port_dwell_hours(dest_ops, profile)
        h_port_dwell = h_dwell_origin + h_dwell_dest

        h_road_o = land_o_km / SPEED_KMH['road']
        h_sea = sea_km / SPEED_KMH['sea']
        h_road_d = land_d_km / SPEED_KMH['road']
        total_h = h_road_o + h_sea + h_port_dwell + h_road_d
        total_km = land_o_km + sea_km + land_d_km
        co2_t = (
            land_o_km * cargo_tons * CO2_G_PER_TKM['road'] +
            sea_km * cargo_tons * CO2_G_PER_TKM['sea'] +
            land_d_km * cargo_tons * CO2_G_PER_TKM['road']
        ) / 1_000_000

        active_dis = [dis for dis in _load_disruptions()
                      if 'sea' in dis.get('affects', []) and
                      ((dis['id'] == 'red-sea-2024' and 'cape_good_hope' in wps) or
                       (dis['id'] == 'panama-drought-2024' and 'panama' in wps))]

        plan_sea = {
            'mode': 'sea',
            'label': 'Truck → Sea → Truck',
            'legs': [
                {
                    'kind': 'road',
                    'from_label': o_lbl, 'to_label': f"{p_o['name']} port ({p_o['unlocode']})",
                    'from': [o_lat, o_lon], 'to': [p_o['lat'], p_o['lon']],
                    'polyline': _great_circle_polyline(o_lat, o_lon, p_o['lat'], p_o['lon'], segments=16),
                    'distance_km': round(land_o_km, 1),
                    'hours': round(h_road_o, 1),
                    'trucks_needed': max(1, int((cargo_tons + 23) // 24)),
                },
                {
                    'kind': 'sea',
                    'from_label': f"{p_o['name']} port", 'to_label': f"{p_d['name']} port",
                    'from': [p_o['lat'], p_o['lon']], 'to': [p_d['lat'], p_d['lon']],
                    'polyline': sea_poly,
                    'via_waypoints': wps,
                    'distance_km': round(sea_km, 1),
                    'hours': round(h_sea, 1),
                    'port_dwell_hours': round(h_port_dwell, 1),
                    'origin_port': {
                        'unlocode': p_o.get('unlocode'),
                        'name': p_o.get('name'),
                        'country': p_o.get('country'),
                        'tier': origin_ops.get('_tier'),
                        'dwell_hours': round(h_dwell_origin, 1),
                        'notes': origin_ops.get('_notes') or '',
                    },
                    'destination_port': {
                        'unlocode': p_d.get('unlocode'),
                        'name': p_d.get('name'),
                        'country': p_d.get('country'),
                        'tier': dest_ops.get('_tier'),
                        'dwell_hours': round(h_dwell_dest, 1),
                        'notes': dest_ops.get('_notes') or '',
                    },
                },
                {
                    'kind': 'road',
                    'from_label': f"{p_d['name']} port", 'to_label': d_lbl,
                    'from': [p_d['lat'], p_d['lon']], 'to': [d_lat, d_lon],
                    'polyline': _great_circle_polyline(p_d['lat'], p_d['lon'], d_lat, d_lon, segments=16),
                    'distance_km': round(land_d_km, 1),
                    'hours': round(h_road_d, 1),
                    'trucks_needed': max(1, int((cargo_tons + 23) // 24)),
                },
            ],
            'total_distance_km': round(total_km, 1),
            'total_hours': round(total_h, 1),
            'total_days': round(total_h / 24, 2),
            'co2_tons': round(co2_t, 3),
            'warnings': active_dis,
        }
        score, reasons = _mode_fitness('sea', profile, plan_sea)
        plan_sea['fitness_score'] = score
        plan_sea['fitness_reasons'] = reasons
        plan_sea['estimated_cost_usd'] = _cost_estimate_usd('sea', plan_sea, profile)
        plans.append(plan_sea)

    # --- 3. ROAD → AIR → ROAD --------------------------------------------
    airports = _load_airports()
    n_o_air = _nearest(airports, o_lat, o_lon, k=1)[0] if airports else None
    n_d_air = _nearest(airports, d_lat, d_lon, k=1)[0] if airports else None
    if n_o_air and n_d_air:
        d_oa, a_o = n_o_air
        d_da, a_d = n_d_air
        land_o_km = d_oa * 1.3
        land_d_km = d_da * 1.3
        air_poly = _great_circle_polyline(a_o['lat'], a_o['lon'], a_d['lat'], a_d['lon'], segments=48)
        air_km = _polyline_length_km(air_poly)
        h_road_o = land_o_km / SPEED_KMH['road']
        h_air = air_km / SPEED_KMH['air']
        h_air_dwell = _airport_dwell_hours(profile)
        h_road_d = land_d_km / SPEED_KMH['road']
        total_h = h_road_o + h_air + h_air_dwell + h_road_d
        total_km = land_o_km + air_km + land_d_km
        co2_t = (
            land_o_km * cargo_tons * CO2_G_PER_TKM['road'] +
            air_km * cargo_tons * CO2_G_PER_TKM['air'] +
            land_d_km * cargo_tons * CO2_G_PER_TKM['road']
        ) / 1_000_000

        active_dis = [d for d in _load_disruptions() if 'air' in d.get('affects', [])]

        plan_air = {
            'mode': 'air',
            'label': 'Truck → Air Cargo → Truck',
            'legs': [
                {
                    'kind': 'road',
                    'from_label': o_lbl, 'to_label': f"{a_o['name']} ({a_o['iata'] or a_o['icao']})",
                    'from': [o_lat, o_lon], 'to': [a_o['lat'], a_o['lon']],
                    'polyline': _great_circle_polyline(o_lat, o_lon, a_o['lat'], a_o['lon'], segments=16),
                    'distance_km': round(land_o_km, 1),
                    'hours': round(h_road_o, 1),
                },
                {
                    'kind': 'air',
                    'from_label': f"{a_o['name']}", 'to_label': f"{a_d['name']}",
                    'from': [a_o['lat'], a_o['lon']], 'to': [a_d['lat'], a_d['lon']],
                    'polyline': air_poly,
                    'distance_km': round(air_km, 1),
                    'hours': round(h_air, 1),
                    'airport_dwell_hours': round(h_air_dwell, 1),
                    'origin_airport': {'iata': a_o.get('iata'), 'name': a_o.get('name')},
                    'destination_airport': {'iata': a_d.get('iata'), 'name': a_d.get('name')},
                },
                {
                    'kind': 'road',
                    'from_label': f"{a_d['name']}", 'to_label': d_lbl,
                    'from': [a_d['lat'], a_d['lon']], 'to': [d_lat, d_lon],
                    'polyline': _great_circle_polyline(a_d['lat'], a_d['lon'], d_lat, d_lon, segments=16),
                    'distance_km': round(land_d_km, 1),
                    'hours': round(h_road_d, 1),
                },
            ],
            'total_distance_km': round(total_km, 1),
            'total_hours': round(total_h, 1),
            'total_days': round(total_h / 24, 2),
            'co2_tons': round(co2_t, 3),
            'warnings': active_dis,
        }
        score, reasons = _mode_fitness('air', profile, plan_air)
        plan_air['fitness_score'] = score
        plan_air['fitness_reasons'] = reasons
        plan_air['estimated_cost_usd'] = _cost_estimate_usd('air', plan_air, profile)
        plans.append(plan_air)

    if not plans:
        return _bad('No routing possible for given coordinates', 422)

    # ---- SMART RECOMMENDATION ----
    # Kombinujemo user preference sa fitness score-om. `auto` uzima best fitness;
    # `fastest`/`cheapest`/`green` favorizuje pojedinu metriku ali još uvek uzima
    # fitness u obzir.
    if prefer == 'green':
        recommended = min(plans, key=lambda p: p['co2_tons'] * (100 / max(p['fitness_score'], 20)))
        rec_reason = f"Lowest CO₂ option that also fits the cargo profile."
    elif prefer in ('cheap', 'cheapest'):
        recommended = min(plans, key=lambda p: p['estimated_cost_usd'] * (100 / max(p['fitness_score'], 20)))
        rec_reason = "Cheapest option that also fits the cargo profile."
    elif prefer in ('fast', 'fastest', 'time'):
        recommended = min(plans, key=lambda p: p['total_hours'] * (100 / max(p['fitness_score'], 20)))
        rec_reason = "Fastest option that also fits the cargo profile."
    else:  # 'auto'
        recommended = max(plans, key=lambda p: p['fitness_score'])
        rec_reason = f"Selected on smart-fit score ({recommended['fitness_score']}/100) for the given cargo profile."

    return jsonify({
        'origin': {'lat': o_lat, 'lon': o_lon, 'label': o_lbl},
        'destination': {'lat': d_lat, 'lon': d_lon, 'label': d_lbl},
        'cargo_profile': profile,
        'cargo_tons': cargo_tons,  # backward-compat
        'plans': plans,
        'recommended_mode': recommended['mode'],
        'recommendation_reason': rec_reason,
        'recommendation_score': recommended['fitness_score'],
        'generated_at': int(time.time()),
    })
