// static/js/modules/logistics/planner.js
//
// MULTIMODALNI LOGISTIČKI PLANER — deljeni modul CRM + portal.
//
// Kako se koristi:
//   openLogisticsPlanner({
//     origin: { lat, lon, label } | { address: 'Beograd' } | { unlocode: 'RSBEG' },
//     destination: { lat, lon, label } | { address: 'New York' },
//     cargoTons: 20,
//     apiBase: '/api/logistics'   // '/api/portal/logistics' u portalu
//     portalAuth: '...'           // opciono, samo za portal
//   })
//
// Modul dinamički učitava Leaflet + Font Awesome CSS ako još nisu učitani,
// otvara full-screen overlay sa mapom levo i sidebar-om desno (kao u primeru
// koji je korisnik dao), automatski geokodira adrese preko javnog Nominatim
// endpointa, poziva /api/logistics/plan, i renderuje sve varijante rute
// (kopno, more, vazduh) sa timeline-om, SLA procenom, CO2 i prekidima ruta.
//
// Zahtevi: Leaflet 1.9+, Font Awesome (za ikonice u markerima).

(function () {
    'use strict';

    // ---------- DINAMIČKO UČITAVANJE ZAVISNOSTI ----------

    const LEAFLET_CSS = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
    const LEAFLET_JS  = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    const FA_CSS      = 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css';

    function ensureCssOnce(href, id) {
        if (document.getElementById(id)) return;
        const l = document.createElement('link');
        l.rel = 'stylesheet'; l.href = href; l.id = id;
        document.head.appendChild(l);
    }
    function ensureScriptOnce(src, id) {
        return new Promise((resolve, reject) => {
            if (document.getElementById(id)) return resolve();
            const s = document.createElement('script');
            s.src = src; s.id = id;
            s.onload = () => resolve();
            s.onerror = () => reject(new Error('failed to load ' + src));
            document.head.appendChild(s);
        });
    }

    async function ensureDeps() {
        ensureCssOnce(LEAFLET_CSS, 'leaflet-css');
        ensureCssOnce(FA_CSS, 'fa-css');
        if (!window.L) await ensureScriptOnce(LEAFLET_JS, 'leaflet-js');
    }

    // ---------- I18N (mini — modul mora raditi i van CRM-a) ----------

    const _isSr = () => {
        try {
            if (typeof Utils !== 'undefined' && Utils.getLang) return Utils.getLang() === 'sr';
        } catch (_) {}
        return false;
    };
    const T = (sr, en) => (_isSr() ? sr : en);

    // ---------- GEOKODIRANJE (klijentski, Nominatim javni endpoint) ----------
    // Koristi se samo ako korisnik prosledi 'address' bez lat/lon.

    async function geocodeAddress(address) {
        const url = 'https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(address);
        const res = await fetch(url, { headers: { 'Accept-Language': _isSr() ? 'sr,en' : 'en' } });
        if (!res.ok) throw new Error('Geocoder unreachable');
        const arr = await res.json();
        if (!arr.length) throw new Error(T('Adresa nije pronađena', 'Address not found'));
        return { lat: parseFloat(arr[0].lat), lon: parseFloat(arr[0].lon), label: arr[0].display_name };
    }

    // ---------- STIL OVERLAY-A (self-contained, bez pipanja globalnog CSS-a) ----------

    function ensureStyleOnce() {
        if (document.getElementById('logi-planner-style')) return;
        const s = document.createElement('style');
        s.id = 'logi-planner-style';
        s.textContent = `
        .logi-overlay { position: fixed; inset: 0; z-index: 9999;
            background: rgba(15,23,42,0.65); backdrop-filter: blur(4px);
            display: flex; align-items: stretch; justify-content: stretch;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        .logi-shell { position: relative; flex: 1; display: flex;
            background: #f3f4f6; overflow: hidden; }
        .logi-map { flex: 1; height: 100%; z-index: 1; }
        .logi-side { position: absolute; top: 20px; left: 20px; bottom: 20px;
            width: 420px; max-width: calc(100vw - 40px);
            background: rgba(255,255,255,0.97); backdrop-filter: blur(12px);
            border-radius: 16px; box-shadow: 0 10px 25px rgba(0,0,0,0.15);
            display: flex; flex-direction: column; overflow: hidden;
            border: 1px solid rgba(255,255,255,0.5); z-index: 400; }
        .logi-header { background: linear-gradient(135deg,#1e3a8a 0%,#3b82f6 100%);
            color: #fff; padding: 20px 24px; display: flex; justify-content: space-between; align-items: center; }
        .logi-close { background: rgba(255,255,255,0.15); border: 0; color: #fff;
            width: 32px; height: 32px; border-radius: 8px; cursor: pointer; font-size: 14px; }
        .logi-close:hover { background: rgba(255,255,255,0.28); }
        .logi-body { flex: 1; overflow-y: auto; padding: 20px 24px; }
        .logi-input { width: 100%; padding: 10px 12px; border: 1px solid #d1d5db;
            border-radius: 10px; font-size: 13px; background: #fff; }
        .logi-input:focus { outline: none; border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,0.15); }
        .logi-btn { display: inline-flex; align-items: center; gap: 6px; padding: 10px 14px;
            border-radius: 10px; font-weight: 600; font-size: 13px; border: 0; cursor: pointer; transition: all .15s; }
        .logi-btn-primary { background: #1e3a8a; color: #fff; }
        .logi-btn-primary:hover { background: #1e40af; }
        .logi-btn-primary:disabled { opacity: 0.55; cursor: not-allowed; }
        .logi-btn-ghost { background: #f3f4f6; color: #374151; border: 1px solid #d1d5db; }
        .logi-btn-ghost:hover { background: #e5e7eb; }
        .logi-tag { display: inline-block; padding: 3px 8px; border-radius: 999px;
            font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; }
        .logi-tag-road { background: #dbeafe; color: #1e3a8a; }
        .logi-tag-sea  { background: #d1fae5; color: #065f46; }
        .logi-tag-air  { background: #fef3c7; color: #92400e; }
        .logi-plan-card { border: 1px solid #e5e7eb; border-radius: 12px; padding: 14px;
            margin-bottom: 10px; background: #fff; cursor: pointer; transition: all .12s; }
        .logi-plan-card:hover { border-color: #93c5fd; box-shadow: 0 3px 12px rgba(0,0,0,0.06); }
        .logi-plan-card.active { border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,0.18); }
        .logi-metric { display: flex; justify-content: space-between; align-items: center;
            padding: 6px 0; font-size: 13px; }
        .logi-metric .k { color: #6b7280; }
        .logi-metric .v { font-weight: 700; color: #111827; }
        .logi-timeline-item { position: relative; padding-left: 26px; margin-bottom: 14px; }
        .logi-timeline-item:not(:last-child)::before { content: ''; position: absolute;
            left: 6px; top: 20px; bottom: -14px; width: 2px; background: #e5e7eb; }
        .logi-timeline-dot { position: absolute; left: 0; top: 4px; width: 14px; height: 14px;
            border-radius: 50%; background: #3b82f6; border: 2px solid #fff;
            box-shadow: 0 0 0 2px #3b82f6; }
        .logi-disruption { background: #fef2f2; border: 1px solid #fecaca; color: #991b1b;
            border-radius: 10px; padding: 10px 12px; font-size: 12px; margin-bottom: 8px; }
        .logi-disruption.warning { background: #fffbeb; border-color: #fde68a; color: #92400e; }
        .logi-vehicle-marker { background: #fff; border: 3px solid #1e3a8a; border-radius: 50%;
            width: 36px; height: 36px; display: flex; align-items: center; justify-content: center;
            box-shadow: 0 3px 8px rgba(0,0,0,0.25); color: #1e3a8a; font-size: 14px; }
        .logi-point-marker { width: 18px; height: 18px; background: #10b981; border-radius: 50%;
            box-shadow: 0 0 0 0 rgba(16,185,129,0.7); animation: logipulse 1.5s infinite; }
        @keyframes logipulse {
            0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16,185,129,0.7); }
            70% { transform: scale(1); box-shadow: 0 0 0 12px rgba(16,185,129,0); }
            100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16,185,129,0); }
        }
        .logi-spinner { display: inline-block; width: 14px; height: 14px;
            border: 2px solid #cbd5e1; border-top-color: #1e3a8a;
            border-radius: 50%; animation: logispin 0.8s linear infinite; vertical-align: middle; }
        @keyframes logispin { to { transform: rotate(360deg); } }
        @media (max-width: 640px) {
          .logi-side { position: absolute; top: 10px; left: 10px; right: 10px; bottom: 10px; width: auto; }
          .logi-map { display: none; }
        }
        `;
        document.head.appendChild(s);
    }

    // ---------- HELPER: fetch koji poštuje CSRF/portal auth ----------

    async function apiFetch(url, opts, apiOpts) {
        opts = opts || {};
        opts.headers = Object.assign({'Content-Type': 'application/json'}, opts.headers || {});
        if (apiOpts && apiOpts.portalAuth) opts.headers['X-Portal-Auth'] = apiOpts.portalAuth;
        opts.credentials = opts.credentials || 'same-origin';
        return fetch(url, opts);
    }

    // ---------- GLAVNA FUNKCIJA ----------

    window.openLogisticsPlanner = async function openLogisticsPlanner(opts) {
        opts = opts || {};
        const apiBase = opts.apiBase || '/api/logistics';
        const apiOpts = { portalAuth: opts.portalAuth };

        ensureStyleOnce();
        try { await ensureDeps(); }
        catch (e) {
            alert(T('Ne mogu da učitam mapu (proverite internet vezu).',
                    'Cannot load map assets (check your internet).'));
            return;
        }

        // -------- OVERLAY --------
        const overlay = document.createElement('div');
        overlay.className = 'logi-overlay';
        overlay.innerHTML = `
          <div class="logi-shell">
            <div id="logi-map" class="logi-map"></div>
            <div class="logi-side">
              <div class="logi-header">
                <div>
                  <div style="font-size:18px;font-weight:800;letter-spacing:.02em;">
                    <i class="fa-solid fa-route" style="margin-right:6px;"></i>${T('Logistički planer','Logistics Planner')}
                  </div>
                  <div style="font-size:12px;opacity:.85;margin-top:2px;">${T('Kopno · More · Vazduh — automatska ruta','Road · Sea · Air — automatic multimodal routing')}</div>
                </div>
                <button class="logi-close" id="logi-close" title="Close">✕</button>
              </div>
              <div class="logi-body" id="logi-body">
                <div style="margin-bottom:14px;">
                  <label style="font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.05em;">${T('Polazište','Origin')}</label>
                  <input class="logi-input" id="logi-origin" placeholder="${T('adresa, grad ili UN/LOCODE...','address, city or UN/LOCODE...')}" style="margin-top:4px;">
                </div>
                <div style="margin-bottom:14px;">
                  <label style="font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.05em;">${T('Odredište','Destination')}</label>
                  <input class="logi-input" id="logi-dest" placeholder="${T('adresa, grad ili UN/LOCODE...','address, city or UN/LOCODE...')}" style="margin-top:4px;">
                </div>
                <div style="display:flex;gap:8px;margin-bottom:16px;">
                  <div style="flex:1;">
                    <label style="font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.05em;">${T('Teret (t)','Cargo (t)')}</label>
                    <input class="logi-input" id="logi-cargo" type="number" min="0.1" step="0.1" value="${opts.cargoTons || 20}" style="margin-top:4px;">
                  </div>
                  <div style="flex:1;">
                    <label style="font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.05em;">${T('Prioritet','Priority')}</label>
                    <select class="logi-input" id="logi-prefer" style="margin-top:4px;">
                      <option value="auto">${T('Najbrži','Fastest')}</option>
                      <option value="cheap">${T('Najjeftiniji','Cheapest')}</option>
                      <option value="green">${T('Najzeleniji','Lowest CO₂')}</option>
                    </select>
                  </div>
                </div>
                <button id="logi-compute" class="logi-btn logi-btn-primary" style="width:100%;justify-content:center;">
                  <i class="fa-solid fa-magic-wand-sparkles"></i>&nbsp;${T('Izračunaj rute','Compute routes')}
                </button>
                <div id="logi-result" style="margin-top:20px;"></div>
              </div>
            </div>
          </div>
        `;
        document.body.appendChild(overlay);
        document.body.style.overflow = 'hidden';

        const close = () => {
            try { map.remove(); } catch (_) {}
            overlay.remove();
            document.body.style.overflow = '';
        };
        overlay.querySelector('#logi-close').addEventListener('click', close);
        overlay.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });

        // -------- MAPA --------
        const map = L.map('logi-map', { zoomControl: false }).setView([25, 10], 2);
        L.control.zoom({ position: 'bottomright' }).addTo(map);
        L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
            maxZoom: 18, attribution: '© OpenStreetMap © CARTO'
        }).addTo(map);

        let mapLayers = [];
        let animRequest = null;

        function clearMap() {
            mapLayers.forEach(l => map.removeLayer(l));
            mapLayers = [];
            if (animRequest) cancelAnimationFrame(animRequest);
        }

        function iconFor(kind) {
            const ic = kind === 'sea' ? 'fa-ship' : (kind === 'air' ? 'fa-plane' : 'fa-truck');
            const color = kind === 'sea' ? '#10b981' : (kind === 'air' ? '#f59e0b' : '#3b82f6');
            return L.divIcon({
                className: 'logi-vehicle-icon',
                html: `<div class="logi-vehicle-marker" style="color:${color};border-color:${color};"><i class="fa-solid ${ic}"></i></div>`,
                iconSize: [36, 36], iconAnchor: [18, 18]
            });
        }
        const pointIcon = L.divIcon({
            className: 'logi-point-icon',
            html: `<div class="logi-point-marker"></div>`,
            iconSize: [18, 18], iconAnchor: [9, 9]
        });

        function drawPlan(plan) {
            clearMap();
            const bounds = [];
            plan.legs.forEach(leg => {
                const latlngs = leg.polyline.map(([la, lo]) => [la, lo]);
                const opts = {
                    color: leg.kind === 'sea' ? '#10b981' : (leg.kind === 'air' ? '#f59e0b' : '#3b82f6'),
                    weight: 4, opacity: 0.85,
                };
                if (leg.kind === 'sea' || leg.kind === 'air') opts.dashArray = '10, 10';
                const line = L.polyline(latlngs, opts).addTo(map);
                line.bindPopup(`<b>${leg.kind.toUpperCase()}</b><br>${leg.from_label} → ${leg.to_label}<br>${leg.distance_km} km · ${leg.hours} h`);
                mapLayers.push(line);
                latlngs.forEach(pt => bounds.push(pt));
                // fazni marker na start
                const m = L.marker(latlngs[0], { icon: pointIcon }).addTo(map);
                mapLayers.push(m);
            });
            // finalni marker
            const last = plan.legs[plan.legs.length - 1];
            const endpt = last.polyline[last.polyline.length - 1];
            const finalM = L.marker(endpt, { icon: pointIcon }).addTo(map);
            mapLayers.push(finalM);

            if (bounds.length) map.fitBounds(bounds, { padding: [40, 40] });

            // animiraj vozilo
            animatePlan(plan);
        }

        function animatePlan(plan) {
            let stageIdx = 0, pointIdx = 0;
            const first = plan.legs[0];
            const moving = L.marker([first.polyline[0][0], first.polyline[0][1]], { icon: iconFor(first.kind) }).addTo(map);
            mapLayers.push(moving);
            function step() {
                const leg = plan.legs[stageIdx];
                if (!leg) return;
                const pts = leg.polyline;
                if (pointIdx < pts.length) {
                    moving.setLatLng(pts[pointIdx]);
                    pointIdx += (leg.kind === 'sea' ? 1 : (leg.kind === 'air' ? 1 : 2));
                    animRequest = requestAnimationFrame(step);
                } else {
                    stageIdx++;
                    pointIdx = 0;
                    if (stageIdx < plan.legs.length) {
                        moving.setIcon(iconFor(plan.legs[stageIdx].kind));
                        animRequest = requestAnimationFrame(step);
                    } else {
                        moving.bindPopup(T('<b>Isporučeno!</b>','<b>Delivered!</b>')).openPopup();
                    }
                }
            }
            step();
        }

        // -------- PODNI DEO: RENDER REZULTATA --------
        function renderResult(payload) {
            const box = overlay.querySelector('#logi-result');
            if (!payload || !payload.plans || !payload.plans.length) {
                box.innerHTML = `<div class="logi-disruption">${T('Nema mogućih ruta.','No routes possible.')}</div>`;
                return;
            }

            const modeIcon = m => m === 'sea' ? 'fa-ship' : (m === 'air' ? 'fa-plane' : 'fa-truck');
            const modeCls  = m => m === 'sea' ? 'logi-tag-sea' : (m === 'air' ? 'logi-tag-air' : 'logi-tag-road');

            const cards = payload.plans.map((p, i) => {
                const isRec = p.mode === payload.recommended_mode;
                const warns = (p.warnings || []).map(w =>
                    `<div class="logi-disruption ${w.severity === 'medium' ? 'warning' : ''}">
                        <b><i class="fa-solid fa-triangle-exclamation"></i> ${escapeHtml(w.title)}</b>
                        <div style="margin-top:4px;opacity:.9;">${escapeHtml(w.description || '')}</div>
                    </div>`
                ).join('');
                return `
                <div class="logi-plan-card ${i===0 ? 'active' : ''}" data-plan-idx="${i}">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <div>
                      <span class="logi-tag ${modeCls(p.mode)}"><i class="fa-solid ${modeIcon(p.mode)}"></i>&nbsp;${escapeHtml(p.label)}</span>
                      ${isRec ? `<span class="logi-tag" style="background:#dcfce7;color:#166534;margin-left:6px;">★ ${T('preporuka','recommended')}</span>` : ''}
                    </div>
                  </div>
                  <div class="logi-metric"><span class="k"><i class="fa-solid fa-ruler-combined"></i> ${T('Distanca','Distance')}</span><span class="v">${p.total_distance_km.toLocaleString()} km</span></div>
                  <div class="logi-metric"><span class="k"><i class="fa-regular fa-clock"></i> ${T('Vreme (ETA)','ETA')}</span><span class="v">${p.total_days} ${T('dana','days')} (${Math.round(p.total_hours)} h)</span></div>
                  <div class="logi-metric"><span class="k"><i class="fa-solid fa-leaf"></i> CO₂</span><span class="v">${p.co2_tons} t</span></div>
                  ${warns}
                </div>`;
            }).join('');

            const activeIdx = Math.max(0, payload.plans.findIndex(p => p.mode === payload.recommended_mode));
            box.innerHTML = `
              <div style="font-size:12px;color:#6b7280;margin-bottom:6px;">
                <b>${T('Od','From')}:</b> ${escapeHtml(payload.origin.label || '')} &nbsp;•&nbsp;
                <b>${T('Do','To')}:</b> ${escapeHtml(payload.destination.label || '')}
              </div>
              ${cards}
              <div id="logi-timeline" style="margin-top:20px;"></div>`;

            const pickPlan = (idx) => {
                overlay.querySelectorAll('.logi-plan-card').forEach((el, i) => {
                    el.classList.toggle('active', i === idx);
                });
                const plan = payload.plans[idx];
                drawPlan(plan);
                renderTimeline(plan);
            };
            overlay.querySelectorAll('.logi-plan-card').forEach(card => {
                card.addEventListener('click', () => pickPlan(parseInt(card.dataset.planIdx, 10)));
            });
            pickPlan(activeIdx);
        }

        function renderTimeline(plan) {
            const el = overlay.querySelector('#logi-timeline');
            if (!el) return;
            const items = plan.legs.map((leg, i) => {
                const dwell = leg.port_dwell_hours ? T(`+ ${leg.port_dwell_hours}h u lukama (utovar/istovar)`, `+ ${leg.port_dwell_hours}h port dwell (load/unload)`)
                             : leg.airport_dwell_hours ? T(`+ ${leg.airport_dwell_hours}h aerodromski handling`, `+ ${leg.airport_dwell_hours}h airport handling`)
                             : '';
                const via = (leg.via_waypoints && leg.via_waypoints.length)
                    ? `<div style="font-size:11px;color:#6b7280;margin-top:3px;">${T('Preko','Via')}: ${leg.via_waypoints.map(w => escapeHtml(w)).join(' → ')}</div>` : '';
                const color = leg.kind === 'sea' ? '#10b981' : (leg.kind === 'air' ? '#f59e0b' : '#3b82f6');
                const icon = leg.kind === 'sea' ? 'fa-ship' : (leg.kind === 'air' ? 'fa-plane' : 'fa-truck');
                return `
                <div class="logi-timeline-item">
                  <div class="logi-timeline-dot" style="background:${color};box-shadow:0 0 0 2px ${color};"></div>
                  <div style="font-weight:700;font-size:13px;color:#111827;"><i class="fa-solid ${icon}" style="color:${color};margin-right:4px;"></i>${T('Etapa','Leg')} ${i+1}: ${escapeHtml(leg.from_label)} → ${escapeHtml(leg.to_label)}</div>
                  <div style="font-size:12px;color:#6b7280;margin-top:2px;">${leg.distance_km.toLocaleString()} km · ${leg.hours} h</div>
                  ${dwell ? `<div style="font-size:11px;color:#9ca3af;margin-top:2px;">${dwell}</div>` : ''}
                  ${via}
                </div>`;
            }).join('');
            el.innerHTML = `
              <h3 style="font-size:13px;font-weight:700;color:#111827;margin:0 0 12px 0;text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid #e5e7eb;padding-bottom:8px;">
                <i class="fa-solid fa-timeline"></i> ${T('Etape transporta','Transport timeline')}
              </h3>
              ${items}`;
        }

        // -------- INPUT: automatsko popunjavanje iz opts --------

        const originInp = overlay.querySelector('#logi-origin');
        const destInp = overlay.querySelector('#logi-dest');

        // Prihvatamo tri oblika: {lat,lon,label}, {address}, {unlocode}, {iata}
        function fmtInput(x) {
            if (!x) return '';
            if (x.label) return x.label;
            if (x.unlocode) return x.unlocode;
            if (x.iata) return x.iata;
            if (x.address) return x.address;
            if (x.lat != null && x.lon != null) return `${x.lat}, ${x.lon}`;
            return '';
        }
        originInp.value = fmtInput(opts.origin);
        destInp.value = fmtInput(opts.destination);

        // Skladištimo resolved lat/lon iz opts ako su prosleđeni
        let originResolved = (opts.origin && opts.origin.lat != null) ? opts.origin : null;
        let destResolved = (opts.destination && opts.destination.lat != null) ? opts.destination : null;

        // Auto-compute ako imamo obe strane iz kontekstuelnih podataka
        if (originResolved && destResolved) {
            setTimeout(() => overlay.querySelector('#logi-compute').click(), 200);
        }

        overlay.querySelector('#logi-compute').addEventListener('click', async () => {
            const btn = overlay.querySelector('#logi-compute');
            const box = overlay.querySelector('#logi-result');
            btn.disabled = true;
            const origText = btn.innerHTML;
            btn.innerHTML = `<span class="logi-spinner"></span>&nbsp;${T('Računam…','Computing…')}`;
            box.innerHTML = '';
            try {
                const originAddr = originInp.value.trim();
                const destAddr = destInp.value.trim();
                if (!originAddr || !destAddr) throw new Error(T('Unesite polazište i odredište.','Enter origin and destination.'));

                // Ako lat/lon već postoji iz opts i input nije menjan, koristi ih.
                // U suprotnom geokodiraj.
                let origin = originResolved;
                if (!origin || (origin.label && origin.label !== originAddr)) {
                    origin = /^-?\d+\.?\d*\s*,\s*-?\d+\.?\d*$/.test(originAddr)
                        ? (function(){ const [la, lo] = originAddr.split(',').map(x => parseFloat(x.trim())); return { lat: la, lon: lo, label: originAddr }; })()
                        : await geocodeAddress(originAddr);
                }
                let dest = destResolved;
                if (!dest || (dest.label && dest.label !== destAddr)) {
                    dest = /^-?\d+\.?\d*\s*,\s*-?\d+\.?\d*$/.test(destAddr)
                        ? (function(){ const [la, lo] = destAddr.split(',').map(x => parseFloat(x.trim())); return { lat: la, lon: lo, label: destAddr }; })()
                        : await geocodeAddress(destAddr);
                }
                originResolved = origin; destResolved = dest;

                const cargo = Math.max(0.1, parseFloat(overlay.querySelector('#logi-cargo').value) || 20);
                const prefer = overlay.querySelector('#logi-prefer').value;

                const res = await apiFetch(apiBase + '/plan', {
                    method: 'POST',
                    body: JSON.stringify({
                        origin: { lat: origin.lat, lon: origin.lon, label: origin.label },
                        destination: { lat: dest.lat, lon: dest.lon, label: dest.label },
                        cargo_tons: cargo,
                        prefer,
                    })
                }, apiOpts);
                const data = await res.json();
                if (!res.ok) throw new Error(data.error || 'plan failed');
                renderResult(data);
            } catch (err) {
                box.innerHTML = `<div class="logi-disruption"><i class="fa-solid fa-triangle-exclamation"></i> ${escapeHtml(err.message || String(err))}</div>`;
            } finally {
                btn.disabled = false;
                btn.innerHTML = origText;
            }
        });
    };

    // --- utility -------------------------------------------------
    function escapeHtml(s) {
        if (s == null) return '';
        return String(s).replace(/[&<>"']/g, c => ({
            '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
        })[c]);
    }
})();
