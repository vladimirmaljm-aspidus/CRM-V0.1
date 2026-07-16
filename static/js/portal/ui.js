// Aspidus B2B Portal — UI helpers, renderers, tab handling

const statusColors = {
    'pending': 'badge-warning',
    'approved': 'badge-success',
    'rejected': 'badge-danger',
    'active': 'badge-info',
    'signed': 'badge-info',
    'in_negotiation': 'badge-muted',
    'payment': 'badge-info',
    'completed': 'badge-success',
    'delivered': 'badge-success',
    'sourced': 'badge-success',
    'closed': 'badge-muted',
    'open': 'badge-info',
    'update_requested': 'badge-warning',
    'expired': 'badge-danger',
    'default': 'badge-muted'
};

function safeText(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[<>&"']/g, ch => ({ '<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;' }[ch]));
}

function switchTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
    document.querySelectorAll('#portal-content > div.flex.gap-1 .tab-btn').forEach(el => el.classList.remove('active'));
    const tc = document.getElementById('tab-' + tabId); if (tc) { tc.classList.remove('hidden'); tc.classList.add('fade-in'); setTimeout(() => tc.classList.remove('fade-in'), 300); }
    const tb = document.getElementById('tab-btn-' + tabId); if (tb) tb.classList.add('active');
    // Lazy-load katalog kad klijent prvi put klikne (bez cena, samo vidljivi proizvodi)
    if (tabId === 'catalog' && typeof loadCatalog === 'function') loadCatalog();
}

// ==========================================================
//  CATALOG (klijent vidi listu proizvoda bez cena, može da traži ponudu)
// ==========================================================
let __catalog_cache = null;

async function loadCatalog() {
    const grid = document.getElementById('catalog-grid');
    const emptyEl = document.getElementById('catalog-empty');
    if (!grid) return;
    grid.innerHTML = '<div class="col-span-full text-center text-slate-500 py-8">Loading catalog…</div>';
    try {
        const res = await fetch(`/api/portal/catalog/${TOKEN}`, { headers: { 'X-Portal-Auth': authKey } });
        if (!res.ok) throw new Error('http ' + res.status);
        const data = await res.json();
        __catalog_cache = data.products || [];
        renderCatalog(__catalog_cache);
    } catch (e) {
        grid.innerHTML = `<div class="col-span-full text-center text-red-500 py-8">Failed to load catalog.</div>`;
    }

    const searchEl = document.getElementById('catalog-search');
    if (searchEl && !searchEl.__wired) {
        searchEl.__wired = true;
        searchEl.addEventListener('input', () => {
            const q = searchEl.value.toLowerCase().trim();
            if (!__catalog_cache) return;
            const filtered = q ? __catalog_cache.filter(p =>
                (p.name || '').toLowerCase().includes(q) ||
                (p.category || '').toLowerCase().includes(q) ||
                (p.hsCode || '').toLowerCase().includes(q) ||
                (p.brand || '').toLowerCase().includes(q) ||
                (p.origins || []).some(o => o.toLowerCase().includes(q))
            ) : __catalog_cache;
            renderCatalog(filtered);
        });
    }
}

function renderCatalog(products) {
    const grid = document.getElementById('catalog-grid');
    const emptyEl = document.getElementById('catalog-empty');
    if (!grid) return;
    if (!products || products.length === 0) {
        grid.innerHTML = '';
        if (emptyEl) emptyEl.classList.remove('hidden');
        return;
    }
    if (emptyEl) emptyEl.classList.add('hidden');
    grid.innerHTML = products.map(p => {
        const originsHtml = (p.origins || []).map(o => `<span class="text-[10px] font-medium bg-slate-100 text-slate-700 border border-slate-200 rounded px-1.5 py-0.5">${escapeHtml(o)}</span>`).join('');
        const certsHtml = (p.certificates || []).slice(0, 3).map(c => `<span class="text-[10px] font-medium bg-emerald-50 text-emerald-800 border border-emerald-200 rounded px-1.5 py-0.5">${escapeHtml(c)}</span>`).join('');
        return `
        <div class="border border-slate-200 rounded-xl p-4 bg-white shadow-sm hover:shadow-md transition-shadow flex flex-col">
            <div class="flex items-start justify-between mb-2 gap-2">
                <div>
                    <h3 class="font-semibold text-slate-900 text-sm leading-tight">${escapeHtml(p.name)}</h3>
                    <div class="text-[11px] text-slate-500 mt-0.5">${escapeHtml(p.category || 'General')}${p.brand ? ' · ' + escapeHtml(p.brand) : ''}</div>
                </div>
                ${p.hsCode ? `<span class="text-[10px] font-mono bg-blue-50 text-blue-800 border border-blue-200 rounded px-1.5 py-0.5 whitespace-nowrap">HS ${escapeHtml(p.hsCode)}</span>` : ''}
            </div>
            <p class="text-xs text-slate-600 mb-3 line-clamp-3 flex-1">${escapeHtml(p.shortDescription || '')}</p>
            ${originsHtml ? `<div class="flex flex-wrap gap-1 mb-2"><span class="text-[10px] font-bold uppercase tracking-wider text-slate-400 mr-1">Origins:</span>${originsHtml}</div>` : ''}
            ${certsHtml ? `<div class="flex flex-wrap gap-1 mb-3">${certsHtml}</div>` : ''}
            ${p.packaging ? `<div class="text-[11px] text-slate-500 mb-3"><strong>Packaging:</strong> ${escapeHtml(p.packaging)}</div>` : ''}
            <button class="w-full bg-blue-600 hover:bg-blue-700 text-white text-xs font-bold py-2 rounded-md uppercase tracking-wider transition-colors mt-auto"
                    onclick="openQuoteRequest('${escapeHtml(p.id)}', '${escapeHtml((p.name || '').replace(/'/g, "\\'"))}', '${escapeHtml(p.unit || '')}')">
              Request quote
            </button>
        </div>`;
    }).join('');
}

// ==========================================================
//  QUOTE REQUEST MODAL — profesionalna zamena za prompt()
// ==========================================================
// Otvara modal sa svim relevantnim poljima (Incoterm, banka, plaćanje,
// logistika, treće lice). Sadrži automation hints koji se menjaju u realnom
// vremenu prema izboru Incotermsa i porekla robe:
//   EXW/FCA/FAS  → klijent preuzima; obavezan freight forwarder + upozorenje
//   CIF/CFR/CIP/CPT → obavezna luka/mesto dostave + upozorenje "extra time"
//   DAP/DPU/DDP  → obavezna tačna adresa dostave
//   ako klijent traži CIF a supplier nudi EXW → hint "conversion needs lead time"

const INCOTERM_META = {
    EXW: { needsAgent: true,  destLabel: 'Pickup location (warehouse address)',  destHint: 'Address where your carrier will collect the goods.',              category: 'buyer_arranges' },
    FCA: { needsAgent: true,  destLabel: 'Named place / carrier hand-over',      destHint: 'Where the seller hands goods to your nominated carrier.',        category: 'buyer_arranges' },
    FAS: { needsAgent: true,  destLabel: 'Port of loading (alongside vessel)',   destHint: 'Named port where goods will be placed alongside the vessel.',    category: 'buyer_arranges' },
    FOB: { needsAgent: true,  destLabel: 'Port of loading',                      destHint: 'Named port where goods will be loaded on board the vessel.',     category: 'buyer_arranges' },
    CPT: { needsAgent: false, destLabel: 'Named destination',                    destHint: 'Named place (city/terminal) where seller pays carriage to.',     category: 'seller_arranges' },
    CIP: { needsAgent: false, destLabel: 'Named destination',                    destHint: 'Named place where seller pays carriage + insurance.',            category: 'seller_arranges' },
    CFR: { needsAgent: false, destLabel: 'Port of destination',                  destHint: 'Named port to which seller pays sea freight (no insurance).',    category: 'seller_arranges' },
    CIF: { needsAgent: false, destLabel: 'Port of destination',                  destHint: 'Named port to which seller pays sea freight AND insurance.',     category: 'seller_arranges' },
    DAP: { needsAgent: false, destLabel: 'Delivery address',                     destHint: 'Full address where goods are delivered (unloaded by buyer).',    category: 'seller_delivers' },
    DPU: { needsAgent: false, destLabel: 'Delivery address (unloaded)',          destHint: 'Full address where goods are delivered AND unloaded by seller.', category: 'seller_delivers' },
    DDP: { needsAgent: false, destLabel: 'Delivery address (duty paid)',         destHint: 'Full address where goods are delivered with all duties paid.',   category: 'seller_delivers' }
};

let __currentQuoteProduct = null;   // za pristup supplyOffers.origin/incoterm iz kataloga

function openQuoteRequest(productId, productName, unit) {
    __currentQuoteProduct = (__catalog_cache || []).find(p => p.id === productId) || { id: productId, name: productName, unit: unit };
    document.getElementById('quote-product-id').value = productId;
    document.getElementById('quote-product-name').value = productName || '';
    document.getElementById('quote-product-unit').value = unit || '';
    document.getElementById('quote-unit-label').textContent = unit || 'unit';
    document.getElementById('quote-modal-subtitle').innerHTML = `<strong>${escapeHtml(productName || '')}</strong> · Fill in the details below for an accurate quote.`;
    // Reset polja
    const f = document.getElementById('quote-form');
    if (f) f.reset();
    // Presetuj radio na 'self' (reset ga ne diram jer je već checked, ali osiguraj)
    const selfRadio = document.querySelector('input[name="quote-requestor"][value="self"]');
    if (selfRadio) selfRadio.checked = true;
    document.getElementById('third-party-buyer-block').classList.add('hidden');
    document.getElementById('quote-logistics-block').classList.add('hidden');
    document.getElementById('quote-hints').classList.add('hidden');
    document.getElementById('quote-hints').innerHTML = '';
    // Otvori
    const modal = document.getElementById('quote-modal');
    if (modal) { modal.classList.remove('hidden'); modal.classList.add('flex'); }
    // Fokusiraj količinu
    setTimeout(() => document.getElementById('quote-qty')?.focus(), 100);
    // Prvi rerender hint-ova
    refreshQuoteHints();
}

function closeQuoteModal() {
    const modal = document.getElementById('quote-modal');
    if (modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); }
    __currentQuoteProduct = null;
}

// Kad se Incoterm promeni: prilagodi labelu polja destinacije + obaveznost, i
// prikaži/ukloni logistics blok. Sve automation hint-ove ponovo iscrtava.
document.addEventListener('change', (ev) => {
    if (!ev.target) return;
    if (ev.target.name === 'quote-requestor') {
        const block = document.getElementById('third-party-buyer-block');
        const bankLabel = document.getElementById('quote-bank-label');
        if (block) block.classList.toggle('hidden', ev.target.value !== 'third_party');
        if (bankLabel) bankLabel.textContent = ev.target.value === 'third_party' ? 'End-buyer bank' : 'Buyer bank';
        refreshQuoteHints();
    }
    if (ev.target.id === 'quote-incoterm') {
        const meta = INCOTERM_META[ev.target.value];
        const destInput = document.getElementById('quote-destination');
        const destLabel = document.getElementById('quote-loc-label');
        const destHint = document.getElementById('quote-loc-hint');
        const logBlock = document.getElementById('quote-logistics-block');
        if (meta) {
            destInput.placeholder = meta.destLabel;
            destLabel.textContent = meta.destLabel + ' *';
            destHint.textContent = meta.destHint;
            logBlock.classList.toggle('hidden', !meta.needsAgent);
        } else {
            destInput.placeholder = 'Please select an Incoterm first';
            destLabel.textContent = 'Delivery location *';
            destHint.textContent = '';
            logBlock.classList.add('hidden');
        }
        refreshQuoteHints();
    }
    if (['quote-payment', 'quote-destination', 'quote-agent'].includes(ev.target.id)) {
        refreshQuoteHints();
    }
});
document.addEventListener('input', (ev) => {
    if (['quote-destination', 'quote-agent', 'quote-qty'].includes(ev.target.id)) refreshQuoteHints();
});

function refreshQuoteHints() {
    const hintBox = document.getElementById('quote-hints');
    if (!hintBox) return;
    const hints = [];
    const push = (icon, cls, text) => hints.push(`<div class="flex items-start gap-2 border ${cls} rounded-lg px-3 py-2 text-xs"><span class="text-base leading-none">${icon}</span><span class="flex-1">${text}</span></div>`);

    const inc = document.getElementById('quote-incoterm')?.value;
    const meta = INCOTERM_META[inc];
    const prod = __currentQuoteProduct;
    // 1. Incoterm mismatch između supplierovog i traženog
    const supplierIncoterms = new Set();
    const supplierOrigins = new Set();
    if (prod && Array.isArray(prod.origins)) prod.origins.forEach(o => supplierOrigins.add(o));
    // (katalog vraća samo origins listu, ne incoterms — pa nudimo generalna upozorenja)

    // 2. CIF/CFR: obavesti da će konverzija iz EXW/FOB dodati vreme
    if (inc === 'CIF' || inc === 'CFR' || inc === 'CIP' || inc === 'CPT') {
        push('⏱', 'border-amber-200 bg-amber-50 text-amber-900',
             `<strong>${inc}</strong> quotes typically require <strong>3–7 additional working days</strong> because our team needs to price freight${inc === 'CIF' || inc === 'CIP' ? ' and marine insurance' : ''} for the requested route.`);
    }
    // 3. EXW/FCA/FAS/FOB: klijent mora da navede agenta
    if (inc && meta && meta.needsAgent) {
        const agent = document.getElementById('quote-agent')?.value?.trim();
        if (!agent) {
            push('🚚', 'border-blue-200 bg-blue-50 text-blue-900',
                 `Under <strong>${inc}</strong>, pickup is arranged by <strong>your side</strong>. Please fill in your freight forwarder / agent above so we can coordinate loading.`);
        }
    }
    // 4. DAP/DPU/DDP: obavezna tačna adresa
    if (['DAP', 'DPU', 'DDP'].includes(inc)) {
        const dest = document.getElementById('quote-destination')?.value?.trim();
        if (dest && !/\d/.test(dest)) {
            push('📍', 'border-amber-200 bg-amber-50 text-amber-900',
                 `<strong>${inc}</strong> requires a full delivery address (street + number). Please make sure the destination field has a specific address, not just a city.`);
        }
    }
    // 5. DDP: dodatna napomena o carini
    if (inc === 'DDP') {
        push('🛃', 'border-orange-200 bg-orange-50 text-orange-900',
             '<strong>DDP</strong> means the seller pays <strong>all import duties, VAT and customs charges</strong> in the destination country. This may add significant cost — CIP or DAP are often preferred alternatives.');
    }
    // 6. L/C plaćanje — dodatno vreme
    const pay = document.getElementById('quote-payment')?.value;
    if (pay && pay.startsWith('LC_')) {
        push('🏦', 'border-slate-200 bg-slate-50 text-slate-800',
             'Payment by <strong>Letter of Credit</strong> requires our bank to review the L/C draft before shipment. Please plan an extra <strong>5–10 days</strong> for L/C document workflow.');
    }
    // 7. Third-party buyer
    const requestor = document.querySelector('input[name="quote-requestor"]:checked')?.value;
    if (requestor === 'third_party') {
        push('👥', 'border-indigo-200 bg-indigo-50 text-indigo-900',
             'You are requesting <strong>on behalf of an end-buyer</strong>. Please provide the end-buyer\'s company details above — the quote will reference your intermediary role.');
    }
    // 8. Origin awareness — ako je poznato poreklo
    if (supplierOrigins.size > 0 && inc && meta && meta.category === 'seller_arranges') {
        push('🌍', 'border-slate-200 bg-slate-50 text-slate-700',
             `This product is currently sourced from: <strong>${[...supplierOrigins].map(escapeHtml).join(', ')}</strong>. Your requested Incoterm (${inc}) means we will arrange transport from origin to your named destination.`);
    }

    hintBox.innerHTML = hints.join('');
    hintBox.classList.toggle('hidden', hints.length === 0);
}

async function submitQuoteRequest() {
    const requestor = document.querySelector('input[name="quote-requestor"]:checked')?.value || 'self';
    const inc = document.getElementById('quote-incoterm').value;
    const meta = INCOTERM_META[inc];
    const qty = parseFloat(document.getElementById('quote-qty').value);
    const dest = document.getElementById('quote-destination').value.trim();
    const pay = document.getElementById('quote-payment').value;
    const agent = document.getElementById('quote-agent').value.trim();

    // Client-side validacije (server je i dalje autoritativan)
    if (!qty || qty <= 0) return showToast('Please enter a valid quantity.', 'error');
    if (!inc) return showToast('Please select an Incoterm.', 'error');
    if (!dest) return showToast('Please provide a delivery location.', 'error');
    if (!pay) return showToast('Please select payment terms.', 'error');
    if (meta && meta.needsAgent && !agent) return showToast('This Incoterm requires a freight forwarder / agent.', 'error');

    const val = id => (document.getElementById(id)?.value || '').trim();
    const payload = {
        productId: document.getElementById('quote-product-id').value,
        quantity: qty,
        targetPrice: parseFloat(val('quote-target-price')) || 0,
        currency: val('quote-currency'),
        neededBy: val('quote-deadline'),
        incoterm: inc,
        destination: dest,
        paymentTerms: pay,
        buyerBank: val('quote-bank'),
        logisticsAgent: agent,
        logisticsAgentContact: val('quote-agent-contact'),
        notes: val('quote-notes'),
        requestor: requestor,
        endBuyer: requestor === 'third_party' ? {
            companyName: val('qb-company'),
            taxId: val('qb-taxid'),
            country: val('qb-country'),
            email: val('qb-email'),
            phone: val('qb-phone')
        } : null
    };

    if (requestor === 'third_party' && !payload.endBuyer.companyName) {
        return showToast('Please provide the end-buyer company name.', 'error');
    }

    const btn = document.getElementById('btn-submit-quote');
    btn.disabled = true; btn.textContent = 'Sending…';
    try {
        const res = await fetch(`/api/portal/quote_request/${TOKEN}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Portal-Auth': authKey },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            showToast('Quote request submitted. Our team will respond shortly.', 'success');
            closeQuoteModal();
        } else {
            const err = await res.json().catch(() => ({}));
            showToast(err.message || err.error || 'Failed to submit quote request.', 'error');
        }
    } catch (e) {
        showToast('Network error. Please try again.', 'error');
    }
    btn.disabled = false; btn.textContent = 'Send Quote Request';
}
window.openQuoteRequest = openQuoteRequest;
window.closeQuoteModal = closeQuoteModal;
window.submitQuoteRequest = submitQuoteRequest;

function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

// Ownership radio toggler — prikazuje/sakriva third-party panel
document.addEventListener('change', (ev) => {
    if (ev.target && ev.target.name === 'form-product-ownership') {
        const panel = document.getElementById('third-party-panel');
        if (panel) {
            if (ev.target.value === 'third_party') panel.classList.remove('hidden');
            else panel.classList.add('hidden');
        }
    }
});

function logoutPortal() {
    sessionStorage.removeItem(`portal_auth_${TOKEN}`);
    window.location.href = '/portal/login';
}

function showToast(message, type) {
    type = type || 'info';
    const div = document.createElement('div');
    div.className = `toast toast-${type} fade-in`;
    div.textContent = message;
    document.getElementById('toast-container').appendChild(div);
    setTimeout(() => { div.style.opacity = '0'; div.style.transition = 'opacity .3s'; }, 3800);
    setTimeout(() => div.remove(), 4200);
}

function getPersonHtml() {
    return `
    <div class="grid grid-cols-1 md:grid-cols-7 gap-3 bg-slate-50 p-3 rounded-lg border border-slate-200 person-entry">
        <div class="md:col-span-3"><input type="text" placeholder="${t('dir_name')}" class="input text-sm p-name" required></div>
        <div class="md:col-span-2"><input type="text" placeholder="${t('dir_pass')}" class="input text-sm font-mono p-pass" required></div>
        <div class="md:col-span-2 flex gap-2"><input type="text" placeholder="${t('dir_nat')}" class="input text-sm p-nat" required><button type="button" onclick="this.parentElement.parentElement.remove()" class="btn btn-danger small">✕</button></div>
    </div>`;
}
function addDirector() { const dc = document.getElementById('directors-container'); if (dc) dc.insertAdjacentHTML('beforeend', getPersonHtml()); }
function addUBO() { const uc = document.getElementById('ubos-container'); if (uc) uc.insertAdjacentHTML('beforeend', getPersonHtml()); }

// ==========================================================
//  DASHBOARD
// ==========================================================
// Money is deliberately displayed as "1,234.56 USD" without a currency
// symbol — offers/deals can be in any of USD/EUR/AED/RSD and mixing them
// under a single symbol would misinform the client.
function fmtMoney(v, currency) {
    const n = Number(v || 0);
    return `${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency || ''}`.trim();
}

function renderDashboard() {
    const deals = portalData?.deals || [];
    const offers = portalData?.offers || [];
    const rfqs = portalData?.my_demands || [];
    const documents = portalData?.documents || [];
    const isActive = d => { const s = (d.status || '').toLowerCase(); return s !== 'completed' && s !== 'closed'; };
    const isOfferOpen = o => {
        if (o.clientStatus === 'declined') return false;
        if (!o.validUntil) return true;
        try { return new Date(o.validUntil) >= new Date(); } catch(e) { return true; }
    };
    const activeDeals = deals.filter(isActive).length;
    const activeOffers = offers.filter(isOfferOpen).length;
    const pendingRfqs = rfqs.filter(r => (r.status || 'pending') === 'pending').length;
    const kycStatus = portalData?.partner?.kycStatus || 'pending';

    const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    setTxt('stat-shipments', activeDeals);
    setTxt('stat-offers', activeOffers);
    setTxt('stat-rfqs', pendingRfqs);
    const kycEl = document.getElementById('stat-kyc');
    if (kycEl) {
        kycEl.textContent = t(`kyc_status_${kycStatus}`) !== `kyc_status_${kycStatus}` ? t(`kyc_status_${kycStatus}`) : kycStatus;
    }

    // Financial summary — group by currency because mixing currencies would
    // give a nonsense total. If only one currency is used we show it flat,
    // otherwise a comma-separated per-currency breakdown.
    const sumByCurrency = (rows, priceKey = 'price') => {
        const acc = {};
        rows.forEach(r => {
            const cur = r.currency || 'USD';
            const qty = Number(r.quantity || 1);
            const val = Number(r[priceKey] || 0) * qty;
            acc[cur] = (acc[cur] || 0) + val;
        });
        return acc;
    };
    const renderSum = (acc) => {
        const entries = Object.entries(acc).filter(([_, v]) => v > 0);
        if (entries.length === 0) return '—';
        return entries.map(([c, v]) => fmtMoney(v, c)).join(' + ');
    };
    setTxt('fin-total-spend', renderSum(sumByCurrency(deals.filter(d => (d.status || '').toLowerCase() === 'completed'), 'saleValue')));
    setTxt('fin-open-offers', renderSum(sumByCurrency(offers.filter(isOfferOpen))));
    setTxt('fin-completed-deals', String(deals.filter(d => (d.status || '').toLowerCase() === 'completed').length));
    setTxt('fin-doc-count', String(documents.length));

    // Active-shipment timeline (top open deal — the one the client is
    // most likely to want tracking on right now)
    renderActiveShipmentTimeline(deals);

    // Tab counters
    const setCount = (id, n) => {
        const el = document.getElementById(id); if (!el) return;
        el.textContent = n;
        el.classList.toggle('hidden', !n || n === 0);
    };
    setCount('count-offers', activeOffers);
    setCount('count-docs', (portalData?.documents || []).length);

    // Recent offers on dashboard (top 5)
    const dashOffers = document.getElementById('dash-offers');
    if (dashOffers) {
        if (offers.length === 0) {
            dashOffers.innerHTML = `<p class="text-slate-400 text-sm">${t('no_offers')}</p>`;
        } else {
            dashOffers.innerHTML = offers.slice(0, 5).map(o => `
                <div class="flex justify-between items-center py-2 border-b border-slate-50 last:border-0">
                    <div>
                        <p class="font-semibold text-slate-800">${safeText(o.productName)}</p>
                        <p class="text-xs text-slate-400">${safeText(o.offerNo || '')} · ${o.date ? new Date(o.date).toLocaleDateString() : ''}</p>
                    </div>
                    <p class="text-sm font-semibold text-emerald-600">${o.price || 0} ${safeText(o.currency || '')}</p>
                </div>
            `).join('');
        }
    }
    // Recent documents (top 5)
    const dashDocs = document.getElementById('dash-docs');
    if (dashDocs) {
        const docs = portalData?.documents || [];
        if (docs.length === 0) {
            dashDocs.innerHTML = `<p class="text-slate-400 text-sm">${t('no_docs')}</p>`;
        } else {
            dashDocs.innerHTML = docs.slice(0, 5).map(d => `
                <div class="flex justify-between items-center py-2 border-b border-slate-50 last:border-0">
                    <div>
                        <p class="font-semibold text-slate-800">${safeText(d.fileName || 'Document.pdf')}</p>
                        <p class="text-xs text-slate-400">${safeText(d.docType || 'Document')} · ${d.createdAt ? new Date(d.createdAt).toLocaleDateString() : ''}</p>
                    </div>
                    <button class="btn btn-ghost small text-xs" onclick="downloadPortalDocument('${safeText(d.id)}')">${t('btn_download')}</button>
                </div>
            `).join('');
        }
    }
}

// ==========================================================
//  SHIPMENTS
// ==========================================================
function renderDeals() {
    const container = document.getElementById('deals-container'); if (!container) return;
    const deals = portalData?.deals || [];
    if (deals.length === 0) {
        container.innerHTML = `<div class="panel p-10 text-center"><p class="text-slate-500 text-sm">${t('no_deals')}</p></div>`;
        return;
    }
    container.innerHTML = deals.map(d => {
        const stCls = statusColors[d?.status] || statusColors['default'];
        return `
        <div class="panel overflow-hidden">
            <div class="px-6 py-4 border-b border-slate-100 bg-slate-50/60 flex justify-between items-center">
                <div>
                    <p class="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">${t('contract')}: ${safeText(d?.contractId || 'N/A')}</p>
                    <h3 class="text-base font-semibold text-slate-900">${safeText(d?.productName || 'Product')}</h3>
                    <p class="text-sm text-blue-600 font-medium">${d?.quantity || 0} ${safeText(d?.unit || '')}</p>
                </div>
                <span class="badge ${stCls}">${safeText((d?.status || '').replace('_', ' '))}</span>
            </div>
            <div class="px-6 py-4 grid grid-cols-2 md:grid-cols-4 gap-6">
                <div><p class="label">${t('vessel')}</p><p class="text-sm font-medium text-slate-800">${safeText(d?.logistics?.vessel || 'TBA')}</p></div>
                <div><p class="label">${t('bl')}</p><p class="text-sm font-medium text-slate-800 font-mono">${safeText(d?.logistics?.blNumber || 'TBA')}</p></div>
                <div><p class="label">${t('pol')}</p><p class="text-sm font-medium text-slate-800">${safeText(d?.logistics?.pol || 'TBA')}</p></div>
                <div><p class="label">${t('pod')}</p><p class="text-sm font-medium text-slate-800">${safeText(d?.logistics?.pod || 'TBA')}</p></div>
            </div>
        </div>`;
    }).join('');
}

// ==========================================================
//  ACTIVE SHIPMENT TIMELINE (dashboard widget)
// ==========================================================
// Timeline stages map to deal status. Anything before/at current status is
// "done", the current status is "active", later stages are pending. If the
// dealStatus does not match the pipeline (e.g. legacy status names) we fall
// back to marking only the first stage as active so nothing lies to the user.
const TIMELINE_STAGES = [
    { key: 'in_negotiation', label: 'In Negotiation' },
    { key: 'signed',          label: 'Contract Signed' },
    { key: 'payment',         label: 'Payment' },
    { key: 'shipped',         label: 'Shipped' },
    { key: 'completed',       label: 'Delivered' }
];
function renderActiveShipmentTimeline(deals) {
    const wrap = document.getElementById('dash-timeline'); if (!wrap) return;
    // Pick the most recent non-completed deal; if none, most recent overall.
    const openDeals = deals.filter(d => (d.status || '').toLowerCase() !== 'completed' && (d.status || '').toLowerCase() !== 'closed');
    const target = openDeals[0] || deals[0];
    if (!target) { wrap.innerHTML = '<p class="text-slate-400 text-sm">No active shipments to track yet.</p>'; return; }

    const status = (target.status || '').toLowerCase();
    const currentIdx = TIMELINE_STAGES.findIndex(s => s.key === status);
    const html = `
        <div class="mb-3 text-xs text-slate-500">
            <span class="font-semibold text-slate-700">${safeText(target.contractId || 'Deal')}</span>
            ${target.productName ? '· ' + safeText(target.productName) : ''}
        </div>
        <div class="flex items-start">
            ${TIMELINE_STAGES.map((s, i) => {
                const cls = currentIdx < 0 ? (i === 0 ? 'active' : '') : (i < currentIdx ? 'done' : (i === currentIdx ? 'active' : ''));
                return `<div class="timeline-stage ${cls}"><div class="dot"></div><div class="timeline-bar"></div><div class="lbl">${s.label}</div></div>`;
            }).join('')}
        </div>`;
    wrap.innerHTML = html;
}

// ==========================================================
//  OFFERS FILTERS
// ==========================================================
let _offersFilter = 'all';
function setOffersFilter(el, val) {
    _offersFilter = val;
    document.querySelectorAll('[data-filter]').forEach(c => c.classList.remove('active'));
    if (el) el.classList.add('active');
    renderOffers();
}

// ==========================================================
//  OFFERS
// ==========================================================
function renderOffers() {
    const container = document.getElementById('offers-container'); if (!container) return;
    let offers = portalData?.offers || [];
    const q = (document.getElementById('offers-search')?.value || '').toLowerCase().trim();
    if (q) offers = offers.filter(o => (`${o.productName || ''} ${o.offerNo || ''} ${o.hsCode || ''}`).toLowerCase().includes(q));
    if (_offersFilter === 'pending') offers = offers.filter(o => !o.clientStatus);
    else if (_offersFilter === 'accepted') offers = offers.filter(o => o.clientStatus === 'accepted');
    else if (_offersFilter === 'declined') offers = offers.filter(o => o.clientStatus === 'declined');
    if (offers.length === 0) {
        container.innerHTML = `<div class="panel p-10 text-center"><p class="text-slate-500 text-sm">${q || _offersFilter !== 'all' ? 'No offers match your filter.' : t('no_offers')}</p></div>`;
        return;
    }
    container.innerHTML = offers.map(o => {
        const statusMap = {
            'accepted': `<span class="badge badge-success">✓ ${t('offer_accepted') || 'Accepted'}</span>`,
            'declined': `<span class="badge badge-danger">✕ ${t('offer_declined') || 'Declined'}</span>`
        };
        const clientStatusBadge = statusMap[o?.clientStatus] || '';
        const canAct = !o?.clientStatus;
        return `
        <div class="panel overflow-hidden">
            <div class="px-6 py-4 border-b border-slate-100 flex justify-between items-center">
                <div>
                    <h3 class="text-base font-semibold text-slate-900">${safeText(o?.productName || 'Product')}</h3>
                    <p class="text-[10px] text-slate-400 font-semibold tracking-widest uppercase mt-1">${t('offer_no')}: ${safeText(o?.offerNo || 'N/A')}${o?.date ? ' · ' + new Date(o.date).toLocaleDateString() : ''}</p>
                </div>
                <div class="text-right">
                    <p class="text-xl font-bold text-emerald-600">${o?.price || 0} ${safeText(o?.currency || '')}</p>
                    <p class="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">/ ${safeText(o?.unit || '')}</p>
                    ${clientStatusBadge ? `<div class="mt-2">${clientStatusBadge}</div>` : ''}
                </div>
            </div>
            <div class="px-6 py-4 grid grid-cols-2 md:grid-cols-4 gap-4 bg-slate-50/50">
                <div><p class="label">${t('qty')}</p><p class="text-sm font-medium text-slate-800">${o?.quantity || 0} ${safeText(o?.unit || '')}</p></div>
                <div><p class="label">${t('incoterm')}</p><p class="text-sm font-medium text-slate-800">${safeText(o?.incoterm || 'N/A')}</p></div>
                <div><p class="label">${t('valid')}</p><p class="text-sm font-medium text-red-600">${o?.validUntil ? new Date(o.validUntil).toLocaleDateString() : 'N/A'}</p></div>
                <div><p class="label">${t('hs_code') || 'HS Code'}</p><p class="text-sm font-mono font-medium text-slate-800">${safeText(o?.hsCode || o?.productHsCode || 'N/A')}</p></div>
                ${o?.packaging ? `<div class="col-span-2"><p class="label">${t('packaging') || 'Packaging'}</p><p class="text-sm font-medium text-slate-800">${safeText(o.packaging)}</p></div>` : ''}
                ${o?.paymentTerms ? `<div class="col-span-2"><p class="label">${t('payment_terms') || 'Payment Terms'}</p><p class="text-sm font-medium text-slate-800">${safeText(o.paymentTerms)}</p></div>` : ''}
                ${o?.pol || o?.pod ? `<div class="col-span-2"><p class="label">${t('shipping') || 'Shipping'}</p><p class="text-sm font-medium text-slate-800">${safeText(o?.pol || 'TBA')} → ${safeText(o?.pod || 'TBA')}</p></div>` : ''}
                ${o?.detailedSpec || o?.productSpec ? `<div class="col-span-4"><p class="label">${t('specification') || 'Specification'}</p><p class="text-sm text-slate-700 whitespace-pre-wrap">${safeText(o?.detailedSpec || o?.productSpec)}</p></div>` : ''}
            </div>
            <div class="px-6 py-3 border-t border-slate-100 flex justify-between items-center gap-2">
                <div class="flex gap-2">
                    ${o?.documentId ? `<button class="btn btn-ghost small text-xs" onclick="downloadPortalDocument('${safeText(o.documentId)}')">${t('btn_download_pdf') || 'Download PDF'}</button>` : ''}
                    <button class="btn btn-ghost small text-xs" onclick="showOfferDetail('${safeText(o.id)}')">${t('view_details') || 'View Details'}</button>
                </div>
                <div class="flex gap-2">
                    ${canAct ? `
                        <button class="btn btn-danger small text-xs" onclick="respondToOffer('${safeText(o.id)}', 'decline')">${t('offer_decline') || 'Decline'}</button>
                        <button class="btn btn-primary small text-xs" onclick="respondToOffer('${safeText(o.id)}', 'accept')">${t('offer_accept') || 'Accept Offer'}</button>
                    ` : ''}
                </div>
            </div>
        </div>`;
    }).join('');
}

// Accept/decline — koristimo profesionalan modal umesto native confirm/prompt.
// Za decline TRAŽIMO razlog (obavezno), jer admin taj podatak inače nema kako
// da vidi zašto je klijent odbio ponudu.
async function respondToOffer(offerId, action) {
    let note = '';
    if (action === 'accept') {
        const yes = await askConfirm(
            t('confirm_accept_offer_title') || 'Accept this offer',
            t('confirm_accept_offer') || 'By accepting, your account manager will contact you shortly to finalize the deal. Continue?',
            { confirmText: t('accept') || 'Yes, accept', cancelText: t('cancel') || 'Cancel', confirmClass: 'bg-emerald-600 hover:bg-emerald-700 text-white' }
        );
        if (!yes) return;
    } else {
        note = await askInput(t('decline_reason_title') || 'Decline this offer', {
            description: t('decline_reason_desc') || 'Please tell us why. Your feedback goes directly to our team and helps us respond better.',
            inputType: 'textarea', multiline: true, required: true,
            placeholder: t('decline_reason_ph') || 'e.g. Price is too high, timing does not fit, alternative supplier chosen…',
            confirmText: t('decline') || 'Decline offer',
            cancelText: t('cancel') || 'Cancel',
            danger: true,
            validator: v => (v && v.trim().length >= 3) ? null : (t('decline_reason_min') || 'Please provide at least a few words.')
        });
        if (note === null) return;   // korisnik otkazao
    }

    try {
        const res = await fetch(`/api/portal/offers/accept/${TOKEN}/${offerId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Portal-Auth': authKey },
            body: JSON.stringify({ action, note })
        });
        if (res.ok) {
            showToast(action === 'accept' ? (t('msg_offer_accepted') || 'Offer accepted. Thank you!') : (t('msg_offer_declined') || 'Offer declined. Our team will follow up.'), 'success');
            loadPortalData();
        } else {
            showToast(t('err_generic'), 'error');
        }
    } catch (e) { showToast(t('err_network'), 'error'); }
}

// Detaljan prikaz ponude (modal)
function showOfferDetail(offerId) {
    const o = (portalData?.offers || []).find(x => x.id === offerId);
    if (!o) return;
    // Otvori u istom stilu kao product/RFQ modal
    let modal = document.getElementById('offer-detail-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'offer-detail-modal';
        modal.className = 'fixed inset-0 bg-slate-900/50 backdrop-blur-sm z-50 hidden items-center justify-center p-4';
        document.body.appendChild(modal);
    }
    modal.innerHTML = `
    <div class="bg-white rounded-2xl shadow-2xl border border-slate-200 max-w-3xl w-full max-h-[92vh] overflow-hidden flex flex-col">
      <div class="flex justify-between items-center border-b border-slate-100 px-6 py-4">
        <div>
          <h3 class="text-lg font-semibold text-slate-900">${safeText(o.productName)}</h3>
          <p class="text-xs text-slate-400 mt-0.5">${t('offer_no')}: ${safeText(o.offerNo)} · ${o.date ? new Date(o.date).toLocaleDateString() : ''}</p>
        </div>
        <button class="icon-btn" onclick="closeOfferDetail()">✕</button>
      </div>
      <div class="p-6 overflow-y-auto flex-1 space-y-5">
        <div class="grid grid-cols-2 md:grid-cols-3 gap-4">
          <div><p class="label">${t('price')}</p><p class="text-lg font-bold text-emerald-600">${o.price || 0} ${safeText(o.currency || '')} / ${safeText(o.unit || '')}</p></div>
          <div><p class="label">${t('qty')}</p><p class="text-sm font-medium">${safeText(o.quantity || '')} ${safeText(o.unit || '')}</p></div>
          <div><p class="label">${t('incoterm')}</p><p class="text-sm font-medium">${safeText(o.incoterm || 'N/A')}</p></div>
          <div><p class="label">${t('valid')}</p><p class="text-sm font-medium">${o.validUntil ? new Date(o.validUntil).toLocaleDateString() : 'N/A'}</p></div>
          <div><p class="label">${t('hs_code') || 'HS Code'}</p><p class="text-sm font-mono">${safeText(o.hsCode || o.productHsCode || 'N/A')}</p></div>
          <div><p class="label">${t('origin') || 'Origin'}</p><p class="text-sm">${safeText(o.origin || o.productOrigin || 'N/A')}</p></div>
        </div>
        ${o.packaging ? `<div><p class="label">${t('packaging') || 'Packaging'}</p><p class="text-sm">${safeText(o.packaging)}</p></div>` : ''}
        ${o.paymentTerms ? `<div><p class="label">${t('payment_terms') || 'Payment Terms'}</p><p class="text-sm">${safeText(o.paymentTerms)}</p></div>` : ''}
        ${o.leadTime ? `<div><p class="label">${t('lead_time') || 'Lead Time'}</p><p class="text-sm">${safeText(o.leadTime)}</p></div>` : ''}
        ${(o.pol || o.pod) ? `<div><p class="label">${t('shipping') || 'Shipping'}</p><p class="text-sm">${safeText(o.pol || 'TBA')} → ${safeText(o.pod || 'TBA')}${o.vessel ? ' · ' + safeText(o.vessel) : ''}</p></div>` : ''}
        ${o.detailedSpec || o.productSpec ? `<div><p class="label">${t('specification') || 'Specification'}</p><pre class="text-sm text-slate-700 whitespace-pre-wrap font-sans bg-slate-50 border border-slate-200 rounded-lg p-3">${safeText(o.detailedSpec || o.productSpec)}</pre></div>` : ''}
        ${o.notes ? `<div><p class="label">${t('notes') || 'Notes'}</p><p class="text-sm italic">${safeText(o.notes)}</p></div>` : ''}
      </div>
      <div class="p-4 border-t border-slate-200 bg-slate-50 flex justify-end gap-2">
        ${o.documentId ? `<button class="btn btn-ghost" onclick="downloadPortalDocument('${safeText(o.documentId)}'); closeOfferDetail();">${t('btn_download_pdf') || 'Download PDF'}</button>` : ''}
        <button class="btn btn-ghost" onclick="closeOfferDetail()">${t('close') || 'Close'}</button>
      </div>
    </div>`;
    modal.classList.remove('hidden'); modal.classList.add('flex');
}
function closeOfferDetail() { const m = document.getElementById('offer-detail-modal'); if (m) { m.classList.add('hidden'); m.classList.remove('flex'); } }

// ==========================================================
//  RFQ
// ==========================================================
function openRFQModal() {
    ['rfq-product','rfq-qty','rfq-price','rfq-notes'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    const m = document.getElementById('rfq-modal'); m.classList.remove('hidden'); m.classList.add('flex');
}
function closeRFQModal() {
    const m = document.getElementById('rfq-modal'); m.classList.add('hidden'); m.classList.remove('flex');
}
function renderRFQs() {
    const container = document.getElementById('rfq-container'); if (!container) return;
    const rfqs = portalData?.my_demands || [];
    if (rfqs.length === 0) { container.innerHTML = `<div class="p-8 text-center text-slate-400 text-sm border border-dashed border-slate-200 rounded-xl">${t('no_rfq')}</div>`; return; }
    container.innerHTML = rfqs.map(d => `
        <div class="p-4 border border-slate-200 rounded-xl flex justify-between items-center bg-white hover:bg-slate-50 transition-colors">
            <div>
                <h4 class="text-sm font-semibold text-slate-900">${safeText(d?.productName || 'Product')}</h4>
                <p class="text-xs text-slate-500 mt-1">
                    ${d?.date ? new Date(d.date).toLocaleDateString() : ''} · Qty: ${d?.quantity || 0}${d?.targetPrice ? ` · Target: $${d.targetPrice}` : ''}
                </p>
            </div>
            <span class="badge ${statusColors[d?.status] || statusColors['default']}">${safeText(d?.status || 'pending')}</span>
        </div>
    `).join('');
}

// ==========================================================
//  DOCUMENTS FILTERS & EXPORT
// ==========================================================
let _docsFilter = 'all';
function setDocsFilter(el, val) {
    _docsFilter = val;
    document.querySelectorAll('[data-doc-filter]').forEach(c => c.classList.remove('active'));
    if (el) el.classList.add('active');
    renderDocuments();
}

// CSV export of the download history helps the client keep an audit trail
// on their side (procurement/compliance often want a record).
function exportDocumentsHistory() {
    const docs = portalData?.documents || [];
    if (docs.length === 0) { showToast('No documents to export.', 'info'); return; }
    const rows = [
        ['Date', 'Type', 'File name', 'Document ID'],
        ...docs.map(d => [
            d.createdAt ? new Date(d.createdAt).toISOString() : '',
            d.docType || 'Document',
            d.fileName || '',
            d.id || ''
        ])
    ];
    const csv = rows.map(r => r.map(f => {
        const s = String(f == null ? '' : f);
        return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    }).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `document-history-${new Date().toISOString().slice(0,10)}.csv`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    showToast('Document history exported.', 'success');
}

// ==========================================================
//  DOCUMENTS
// ==========================================================
function renderDocuments() {
    const body = document.getElementById('documents-table-body'); if (!body) return;
    let docs = portalData?.documents || [];
    const q = (document.getElementById('docs-search')?.value || '').toLowerCase().trim();
    if (q) docs = docs.filter(d => (`${d.fileName || ''} ${d.docType || ''}`).toLowerCase().includes(q));
    if (_docsFilter !== 'all') docs = docs.filter(d => (d.docType || '').toUpperCase() === _docsFilter);
    if (docs.length === 0) {
        body.innerHTML = `<tr><td colspan="4" class="py-8 px-3 text-center text-slate-400 text-sm">${q || _docsFilter !== 'all' ? 'No documents match your filter.' : t('no_docs')}</td></tr>`;
        return;
    }
    body.innerHTML = docs.map(d => `
        <tr class="row-hover">
            <td class="py-3 px-3 text-xs text-slate-500 whitespace-nowrap">${d.createdAt ? new Date(d.createdAt).toLocaleDateString() : ''}</td>
            <td class="py-3 px-3"><span class="badge badge-muted">${safeText(d.docType || 'Document')}</span></td>
            <td class="py-3 px-3 text-sm font-medium text-slate-900">${safeText(d.fileName || 'Document.pdf')}</td>
            <td class="py-3 px-3 text-right">
                <button class="btn btn-ghost small text-xs" onclick="downloadPortalDocument('${safeText(d.id)}')">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" class="w-3.5 h-3.5"><path d="M12 3v14m-5-5l5 5 5-5M5 21h14"/></svg>
                    ${t('btn_download')}
                </button>
            </td>
        </tr>
    `).join('');
}

// ==========================================================
//  PRODUCTS (my_products) — same table, cleaner style
// ==========================================================
function renderGoodsTable() {
    const body = document.getElementById('goods-table-body'); if (!body) return;
    const items = portalData?.my_products || [];
    if (items.length === 0) {
        body.innerHTML = `<tr><td colspan="5" class="py-8 px-3 text-center text-slate-400 text-sm">${t('no_products')}</td></tr>`;
        return;
    }
    body.innerHTML = items.map(p => {
        const off = p.data.supplyOffers && p.data.supplyOffers.length > 0 ? p.data.supplyOffers[0] : {};
        return `
        <tr class="row-hover">
            <td class="py-3 px-3">
                <div class="font-semibold text-slate-900">${safeText(p.data.name)}</div>
                <div class="text-xs text-slate-400 font-mono">${safeText(p.data.sku || '')}</div>
            </td>
            <td class="py-3 px-3 font-mono font-semibold text-emerald-600">${off.price || 0} ${safeText(off.currency || 'USD')} <span class="text-xs text-slate-400">/ ${safeText(off.unit || 'MT')}</span></td>
            <td class="py-3 px-3 text-xs text-slate-600 max-w-xs truncate">${safeText(p.data.detailedSpec || p.data.shortDescription || 'N/A')}</td>
            <td class="py-3 px-3"><span class="badge ${statusColors[p.status] || statusColors['default']}">${safeText(p.status)}</span></td>
            <td class="py-3 px-3 text-right">
                <button class="btn btn-ghost small text-xs" onclick="editProductItem('${safeText(p.id)}')">${t('btn_edit')}</button>
            </td>
        </tr>`;
    }).join('');
}

function renderPortalCOA() {
    const list = document.getElementById('portal-coa-list'); if (!list) return;
    list.innerHTML = activeCOAParams.map((c, i) => `
        <div class="flex items-center justify-between bg-slate-50 border border-slate-200 rounded p-2 text-xs">
            <span><span class="text-slate-400 uppercase text-[9px] mr-2">${safeText(c.name)}:</span> ${safeText(c.value)}</span>
            <button type="button" class="text-red-500 hover:bg-red-50 px-2 py-0.5 rounded" onclick="removePortalCOA(${i})">✕</button>
        </div>
    `).join('');
}
function addPortalCOA() {
    const n = document.getElementById('p-coa-name').value.trim();
    const v = document.getElementById('p-coa-value').value.trim();
    if (n && v) { activeCOAParams.push({ name: n, value: v }); document.getElementById('p-coa-name').value=''; document.getElementById('p-coa-value').value=''; renderPortalCOA(); }
}
window.removePortalCOA = function(i) { activeCOAParams.splice(i, 1); renderPortalCOA(); };

function openProductModal() {
    document.getElementById('product-form').reset();
    document.getElementById('form-product-id').value = '';
    document.getElementById('form-existing-certs').innerHTML = '';
    uploadedCertUrls = []; activeCOAParams = []; renderPortalCOA();
    const m = document.getElementById('product-modal'); m.classList.remove('hidden'); m.classList.add('flex');
}
function closeProductModal() {
    const m = document.getElementById('product-modal'); m.classList.add('hidden'); m.classList.remove('flex');
}
function editProductItem(id) {
    const prod = (portalData?.my_products || []).find(p => p.id === id); if (!prod) return;
    const set = (elId, v) => { const el = document.getElementById(elId); if (el) el.value = v || ''; };
    set('form-product-id', prod.id);
    set('form-product-name', prod.data.name);
    set('form-product-category', prod.data.category);
    set('form-product-hscode', prod.data.hsCode);
    set('form-product-sku', prod.data.sku);
    set('form-product-brand', prod.data.brand);
    set('form-product-shortdesc', prod.data.shortDescription);
    set('form-product-cap20', prod.data.logistics?.cap20);
    set('form-product-cap40', prod.data.logistics?.cap40);
    set('form-product-spec', prod.data.detailedSpec);
    set('form-product-packaging', prod.data.packaging);
    set('form-product-package-weight', prod.data.packageWeight);
    set('form-product-per-pallet', prod.data.unitsPerPallet);
    set('form-product-stock', prod.data.availableStock);
    set('form-product-warehouse', prod.data.warehouseLocation);
    set('form-product-leadtime', prod.data.leadTime);

    const off = prod.data.supplyOffers && prod.data.supplyOffers.length > 0 ? prod.data.supplyOffers[0] : {};
    set('form-product-price', off.price);
    set('form-product-currency', off.currency || 'USD');
    set('form-product-unit', off.unit || 'MT');
    set('form-product-moq', off.moq);
    set('form-product-incoterm', off.incoterm || 'FOB');
    set('form-product-origin', off.country);
    set('form-product-valid', off.validUntil);
    set('form-product-payterms', off.paymentTerms);

    activeCOAParams = prod.data.coaParams || []; renderPortalCOA();
    uploadedCertUrls = off.certificates ? off.certificates.split(', ').filter(Boolean) : [];
    document.getElementById('form-existing-certs').innerHTML = uploadedCertUrls.map((c, i) => `<a href="${safeText(c)}" target="_blank" class="block">✓ Certificate #${i+1}</a>`).join('');
    const m = document.getElementById('product-modal'); m.classList.remove('hidden'); m.classList.add('flex');
}

// Product modal tabs
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.ptab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.ptab-btn').forEach(b => b.classList.remove('active'));
            e.currentTarget.classList.add('active');
            document.querySelectorAll('.ptab-pane').forEach(p => p.classList.add('hidden'));
            const targetEl = document.getElementById(e.currentTarget.dataset.target);
            if (targetEl) targetEl.classList.remove('hidden');
        });
    });
});

// ==========================================================
//  PROFILE
// ==========================================================
function fillProfile() {
    const p = portalData?.partner || {};
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };
    set('profile-email', p.email || '');
    set('profile-phone', p.phone || '');
    set('profile-person', p.contactPerson || '');
    set('profile-city', p.address?.city || '');
    set('profile-street', p.address?.street || '');
    set('profile-country', p.address?.country || '');
    set('profile-note', '');

    const list = document.getElementById('profile-requests-body');
    const reqs = portalData?.my_profile_requests || [];
    if (!list) return;
    if (reqs.length === 0) {
        list.innerHTML = `<p class="text-slate-400 text-sm">${t('no_profile_requests')}</p>`;
        return;
    }
    list.innerHTML = reqs.map(r => {
        const changesText = Object.entries(r.changes || {}).filter(([k]) => k !== 'note').map(([k, v]) => `<span class="text-slate-500 text-xs mr-3"><strong>${safeText(k)}:</strong> ${safeText(v)}</span>`).join(' ');
        return `
        <div class="p-3 rounded-lg border border-slate-200 flex justify-between items-center bg-slate-50/60">
            <div>
                <div class="text-xs text-slate-400">${r.submitted_at ? new Date(r.submitted_at).toLocaleString() : ''}</div>
                <div class="mt-1">${changesText}</div>
                ${r.changes?.note ? `<div class="text-xs text-slate-500 italic mt-1">"${safeText(r.changes.note)}"</div>` : ''}
            </div>
            <span class="badge ${statusColors[r.status] || statusColors['default']}">${safeText(r.status)}</span>
        </div>`;
    }).join('');
}

// ==========================================================
//  KYC STATUS BADGE
// ==========================================================
function renderKycStatusLine() {
    const line = document.getElementById('kyc-status-line'); if (!line) return;
    const status = portalData?.partner?.kycStatus;
    if (!status) { line.classList.add('hidden'); return; }
    const label = t(`kyc_status_${status}`) !== `kyc_status_${status}` ? t(`kyc_status_${status}`) : status;
    line.classList.remove('hidden');
    line.innerHTML = `<span class="badge ${statusColors[status] || statusColors['default']}">${t('kyc_current_status')}: ${safeText(label)}</span>`;
}

// ==========================================================
//  Permisije (za portalPermissions iz partner zapisa)
// ==========================================================
// ==========================================================
//  NOTIFICATIONS
// ==========================================================
// Client-side derived notifications feed. Derived (not stored) events give
// the client a live at-a-glance view of things that need attention right
// now — new offers, KYC status changes, upcoming validity expiry — without
// any backend changes required.
// Read-state is tracked per (token, event-id) in localStorage so refreshes
// don't wipe out "already seen" markers.
function _notifStorageKey() { return `portal_notif_seen_${typeof TOKEN !== 'undefined' ? TOKEN : ''}`; }
function _getSeenNotifs() {
    try { return JSON.parse(localStorage.getItem(_notifStorageKey()) || '[]'); } catch(e) { return []; }
}
function _saveSeenNotifs(ids) {
    try { localStorage.setItem(_notifStorageKey(), JSON.stringify(ids.slice(-200))); } catch(e) {}
}

function buildNotifications() {
    const list = [];
    const offers = portalData?.offers || [];
    const partner = portalData?.partner || {};
    const now = new Date();

    // Active offers awaiting client response (highest signal for the client)
    offers.filter(o => !o.clientStatus).forEach(o => {
        list.push({
            id: `offer_pending_${o.id}`,
            when: o.date || new Date().toISOString(),
            title: `New offer awaits your response`,
            body: `${o.productName || 'Product'} · ${o.offerNo || ''}`,
            action: () => { switchTab('offers'); toggleNotificationsPanel(true); }
        });
    });
    // Offers expiring within 3 days
    offers.filter(o => !o.clientStatus && o.validUntil).forEach(o => {
        try {
            const d = new Date(o.validUntil);
            const diffDays = Math.ceil((d - now) / (1000*60*60*24));
            if (diffDays >= 0 && diffDays <= 3) {
                list.push({
                    id: `offer_expiring_${o.id}_${diffDays}`,
                    when: new Date().toISOString(),
                    title: `Offer ${o.offerNo || ''} expires in ${diffDays} day${diffDays === 1 ? '' : 's'}`,
                    body: `${o.productName || ''}`,
                    action: () => { switchTab('offers'); toggleNotificationsPanel(true); }
                });
            }
        } catch(e) {}
    });
    // KYC update required
    if (partner.kycStatus === 'update_requested' || partner.kycStatus === 'expired') {
        list.push({
            id: `kyc_${partner.kycStatus}`,
            when: new Date().toISOString(),
            title: partner.kycStatus === 'expired' ? 'KYC documentation has expired' : 'KYC update required',
            body: partner.kycReviewNote || 'Please open the KYC tab to complete the requested items.',
            action: () => { switchTab('kyc'); toggleNotificationsPanel(true); }
        });
    }
    // Sort newest first
    list.sort((a, b) => new Date(b.when) - new Date(a.when));
    return list;
}

let _lastNotifList = [];
function renderNotifications() {
    _lastNotifList = buildNotifications();
    const seen = new Set(_getSeenNotifs());
    const body = document.getElementById('notif-body');
    const badge = document.getElementById('notif-count');
    const unreadCount = _lastNotifList.filter(n => !seen.has(n.id)).length;
    if (badge) {
        if (unreadCount > 0) { badge.textContent = String(unreadCount); badge.classList.remove('hidden'); }
        else { badge.classList.add('hidden'); }
    }
    if (!body) return;
    if (_lastNotifList.length === 0) {
        body.innerHTML = `<div class="p-6 text-center text-slate-400 text-sm">${t('notif_empty')}</div>`;
        return;
    }
    body.innerHTML = _lastNotifList.map((n, i) => `
        <div class="notif-item ${seen.has(n.id) ? '' : 'unread'}" onclick="handleNotifClick(${i})">
            <div class="text-sm font-semibold text-slate-900">${safeText(n.title)}</div>
            ${n.body ? `<div class="text-xs text-slate-500 mt-1">${safeText(n.body)}</div>` : ''}
            <div class="text-[10px] text-slate-400 mt-1">${n.when ? new Date(n.when).toLocaleString() : ''}</div>
        </div>
    `).join('');
}
window.handleNotifClick = function(idx) {
    const n = _lastNotifList[idx]; if (!n) return;
    const seen = new Set(_getSeenNotifs()); seen.add(n.id); _saveSeenNotifs([...seen]);
    if (typeof n.action === 'function') n.action();
    renderNotifications();
};
window.toggleNotificationsPanel = function(forceClose) {
    const panel = document.getElementById('notif-panel'); if (!panel) return;
    if (forceClose === true) { panel.classList.add('hidden'); return; }
    panel.classList.toggle('hidden');
};
window.markAllNotificationsRead = function() {
    _saveSeenNotifs(_lastNotifList.map(n => n.id));
    renderNotifications();
};
// Dismiss the panel on outside click for standard UX.
document.addEventListener('click', (e) => {
    const panel = document.getElementById('notif-panel');
    const btn = document.getElementById('btn-notifications');
    if (!panel || panel.classList.contains('hidden')) return;
    if (panel.contains(e.target) || (btn && btn.contains(e.target))) return;
    panel.classList.add('hidden');
});

window.applyPermissions = function(permissions) {
    if (!permissions || permissions.length === 0) return;
    const perms = new Set(permissions);
    // dashboard, profile su UVEK vidljivi; ostali samo ako partner ima permisiju
    const map = {
        'tab-btn-shipments': 'shipments',
        'tab-btn-offers': 'offers',
        'tab-btn-rfq': 'rfq',
        'tab-btn-kyc': 'kyc',
        'tab-btn-goods': 'goods',
        'tab-btn-docs': 'documents',
        'tab-btn-catalog': 'catalog'
    };
    Object.entries(map).forEach(([id, permKey]) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('hidden', !perms.has(permKey));
    });
};
