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
}

function logoutPortal() {
    sessionStorage.removeItem(`portal_auth_${TOKEN}`);
    window.location.reload();
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
function renderDashboard() {
    const deals = portalData?.deals || [];
    const offers = portalData?.offers || [];
    const rfqs = portalData?.my_demands || [];
    const activeDeals = deals.filter(d => (d.status || '').toLowerCase() !== 'completed' && (d.status || '').toLowerCase() !== 'closed').length;
    const activeOffers = offers.filter(o => {
        if (!o.validUntil) return true;
        try { return new Date(o.validUntil) >= new Date(); } catch(e) { return true; }
    }).length;
    const pendingRfqs = rfqs.filter(r => (r.status || 'pending') === 'pending').length;
    const kycStatus = portalData?.partner?.kycStatus || 'pending';

    const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    setTxt('stat-shipments', activeDeals);
    setTxt('stat-offers', activeOffers);
    setTxt('stat-rfqs', pendingRfqs);
    const kycEl = document.getElementById('stat-kyc');
    if (kycEl) {
        const labels = { 'pending': 'Pending', 'approved': 'Approved', 'update_requested': 'Update Needed', 'expired': 'Expired', 'rejected': 'Rejected' };
        kycEl.textContent = labels[kycStatus] || kycStatus;
    }

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
//  OFFERS
// ==========================================================
function renderOffers() {
    const container = document.getElementById('offers-container'); if (!container) return;
    const offers = portalData?.offers || [];
    if (offers.length === 0) {
        container.innerHTML = `<div class="panel p-10 text-center"><p class="text-slate-500 text-sm">${t('no_offers')}</p></div>`;
        return;
    }
    container.innerHTML = offers.map(o => `
        <div class="panel overflow-hidden">
            <div class="px-6 py-4 border-b border-slate-100 flex justify-between items-center">
                <div>
                    <h3 class="text-base font-semibold text-slate-900">${safeText(o?.productName || 'Product')}</h3>
                    <p class="text-[10px] text-slate-400 font-semibold tracking-widest uppercase mt-1">${t('offer_no')}: ${safeText(o?.offerNo || 'N/A')}</p>
                </div>
                <div class="text-right">
                    <p class="text-xl font-bold text-emerald-600">${o?.price || 0} ${safeText(o?.currency || '')}</p>
                    <p class="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">/ ${safeText(o?.unit || '')}</p>
                </div>
            </div>
            <div class="px-6 py-4 grid grid-cols-2 md:grid-cols-4 gap-6 bg-slate-50/50">
                <div><p class="label">${t('qty')}</p><p class="text-sm font-medium text-slate-800">${o?.quantity || 0} ${safeText(o?.unit || '')}</p></div>
                <div><p class="label">${t('incoterm')}</p><p class="text-sm font-medium text-slate-800">${safeText(o?.incoterm || 'N/A')}</p></div>
                <div><p class="label">${t('valid')}</p><p class="text-sm font-medium text-red-600">${o?.validUntil ? new Date(o.validUntil).toLocaleDateString() : 'N/A'}</p></div>
                <div class="flex items-center justify-end">
                    ${o?.documentId ? `<button class="btn btn-ghost small text-xs" onclick="downloadPortalDocument('${safeText(o.documentId)}')">${t('btn_download_pdf')}</button>` : ''}
                </div>
            </div>
        </div>
    `).join('');
}

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
//  DOCUMENTS
// ==========================================================
function renderDocuments() {
    const body = document.getElementById('documents-table-body'); if (!body) return;
    const docs = portalData?.documents || [];
    if (docs.length === 0) {
        body.innerHTML = `<tr><td colspan="4" class="py-8 px-3 text-center text-slate-400 text-sm">${t('no_docs')}</td></tr>`;
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
    const label = {
        'pending': 'Pending Review',
        'approved': 'Approved',
        'rejected': 'Rejected',
        'update_requested': 'Update Requested',
        'expired': 'Expired'
    }[status] || status;
    line.classList.remove('hidden');
    line.innerHTML = `<span class="badge ${statusColors[status] || statusColors['default']}">${t('kyc_current_status')}: ${label}</span>`;
}

// ==========================================================
//  Permisije (za portalPermissions iz partner zapisa)
// ==========================================================
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
        'tab-btn-docs': 'documents'
    };
    Object.entries(map).forEach(([id, permKey]) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('hidden', !perms.has(permKey));
    });
};
