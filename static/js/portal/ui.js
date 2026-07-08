const statusColors = { 'pending': 'bg-amber-100 text-amber-700 border-amber-200', 'approved': 'bg-green-100 text-green-700 border-green-200', 'rejected': 'bg-red-100 text-red-700 border-red-200', 'active': 'bg-blue-100 text-blue-700 border-blue-200', 'delivered': 'bg-green-100 text-green-700 border-green-200', 'default': 'bg-slate-100 text-slate-700 border-slate-200' };

function switchTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    const tc = document.getElementById('tab-' + tabId); if(tc) tc.classList.remove('hidden');
    const tb = document.getElementById('tab-btn-' + tabId); if(tb) tb.classList.add('active');
}

function logoutPortal() { sessionStorage.removeItem(`portal_auth_${TOKEN}`); window.location.reload(); }

function getPersonHtml(isUBO) {
    return `
    <div class="grid grid-cols-1 md:grid-cols-7 gap-4 bg-slate-50 p-4 rounded-xl border person-entry transition-all">
        <div class="md:col-span-3"><input type="text" placeholder="${t('dir_name')}" class="w-full bg-white border rounded-lg px-4 py-2.5 text-sm font-bold text-slate-900 outline-none p-name" required></div>
        <div class="md:col-span-2"><input type="text" placeholder="${t('dir_pass')}" class="w-full bg-white border rounded-lg px-4 py-2.5 text-sm font-mono text-slate-900 outline-none p-pass" required></div>
        <div class="md:col-span-2 flex gap-3"><input type="text" placeholder="${t('dir_nat')}" class="w-full bg-white border rounded-lg px-4 py-2.5 text-sm font-bold text-slate-900 outline-none p-nat" required><button type="button" onclick="this.parentElement.parentElement.remove()" class="bg-red-50 hover:bg-red-100 text-red-600 border border-red-200 px-4 rounded-lg font-black">✕</button></div>
    </div>`;
}

function addDirector() { const dc = document.getElementById('directors-container'); if(dc) dc.insertAdjacentHTML('beforeend', getPersonHtml(false)); }
function addUBO() { const uc = document.getElementById('ubos-container'); if(uc) uc.insertAdjacentHTML('beforeend', getPersonHtml(true)); }

function renderDeals() {
    const container = document.getElementById('deals-container'); if(!container) return;
    if(!portalData?.deals || portalData.deals.length === 0) { container.innerHTML = `<div class="glass-panel p-10 text-center rounded-2xl bg-white"><p class="text-slate-500 font-bold">${t('no_deals')}</p></div>`; return; }
    container.innerHTML = portalData.deals.map(d => `
        <div class="glass-panel rounded-2xl overflow-hidden bg-white shadow-sm hover:shadow-md transition-shadow">
            <div class="bg-slate-50 px-6 py-4 border-b flex justify-between items-center">
                <div><span class="text-[10px] font-black text-slate-400 uppercase tracking-widest">${t('contract')}: ${d?.contractId || 'N/A'}</span><h3 class="text-xl font-black text-slate-900">${d?.productName || 'Unknown'}</h3><p class="text-sm font-bold text-blue-600">${d?.quantity || 0} ${d?.unit || ''}</p></div>
                <div class="px-3 py-1 rounded-md text-[10px] font-black uppercase tracking-widest border ${statusColors[d?.status] || statusColors['default']}">${(d?.status||'').replace('_',' ')}</div>
            </div>
            <div class="p-6 grid grid-cols-2 md:grid-cols-4 gap-6 bg-white">
                <div><p class="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1">${t('vessel')}</p><p class="font-bold text-slate-800 text-sm">${d?.logistics?.vessel || 'N/A'}</p></div>
                <div><p class="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1">${t('bl')}</p><p class="font-bold text-slate-800 text-sm font-mono">${d?.logistics?.blNumber || 'N/A'}</p></div>
                <div><p class="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1">${t('pol')}</p><p class="font-bold text-slate-800 text-sm">${d?.logistics?.pol || 'N/A'}</p></div>
                <div><p class="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1">${t('pod')}</p><p class="font-bold text-slate-800 text-sm">${d?.logistics?.pod || 'N/A'}</p></div>
            </div>
        </div>`).join('');
}

function renderOffers() {
    const container = document.getElementById('offers-container'); if(!container) return;
    if(!portalData?.offers || portalData.offers.length === 0) { container.innerHTML = `<div class="glass-panel p-10 text-center rounded-2xl bg-white"><p class="text-slate-500 font-bold">${t('no_offers')}</p></div>`; return; }
    container.innerHTML = portalData.offers.map(o => `
        <div class="glass-panel rounded-2xl overflow-hidden shadow-sm hover:shadow-md transition-shadow">
            <div class="px-6 py-5 flex justify-between items-center border-b bg-white">
                <div><h3 class="text-xl font-black text-slate-900">${o?.productName || 'Unknown'}</h3><p class="text-[10px] text-slate-400 font-black tracking-widest uppercase mt-1">${t('offer_no')}: ${o?.offerNo || 'N/A'}</p></div>
                <div class="text-right"><p class="text-2xl font-black text-emerald-600">${o?.price || 0} ${o?.currency || ''}</p><p class="text-[10px] font-black text-slate-400 uppercase tracking-widest">/ ${o?.unit || ''}</p></div>
            </div>
            <div class="p-6 grid grid-cols-3 gap-6 bg-slate-50">
                <div><p class="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1">${t('qty')}</p><p class="font-bold text-slate-800 text-sm">${o?.quantity || 0} ${o?.unit || ''}</p></div>
                <div><p class="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1">${t('incoterm')}</p><p class="font-black text-slate-800 text-sm">${o?.incoterm || 'N/A'}</p></div>
                <div><p class="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1">${t('valid')}</p><p class="font-bold text-red-600 text-sm bg-red-50 inline-block px-2 py-0.5 rounded">${o?.validUntil ? new Date(o.validUntil).toLocaleDateString() : 'N/A'}</p></div>
            </div>
        </div>`).join('');
}

function openRFQModal() {
    document.getElementById('rfq-product').value = '';
    document.getElementById('rfq-qty').value = '';
    document.getElementById('rfq-price').value = '';
    document.getElementById('rfq-notes').value = '';
    document.getElementById('rfq-modal').classList.remove('hidden');
}

function closeRFQModal() {
    document.getElementById('rfq-modal').classList.add('hidden');
}

function renderRFQs() {
    const container = document.getElementById('rfq-container'); if(!container) return;
    if(!portalData?.my_demands || portalData.my_demands.length === 0) { container.innerHTML = `<div class="p-10 text-center text-slate-400 font-bold border rounded-xl bg-slate-50">${t('no_rfq')}</div>`; return; }
    container.innerHTML = portalData.my_demands.map(d => `
        <div class="p-5 border border-slate-200 rounded-xl flex justify-between items-center bg-white hover:bg-slate-50 transition-colors shadow-sm">
            <div>
                <h4 class="text-lg font-black text-slate-900">${d?.productName || 'Unknown'}</h4>
                <p class="text-[11px] text-slate-500 font-bold uppercase tracking-widest mt-1">
                    ${d?.date ? new Date(d.date).toLocaleDateString() : ''} | Qty: ${d?.quantity||0} | T. Price: $${d?.targetPrice||0}
                </p>
            </div>
            <div>
                <span class="px-3 py-1.5 rounded-md text-[10px] font-black uppercase tracking-widest border shadow-sm ${statusColors[d?.status] || statusColors['default']}">${d?.status||'pending'}</span>
            </div>
        </div>`).join('');
}

document.querySelectorAll('.ptab-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
        document.querySelectorAll('.ptab-btn').forEach(b => { b.classList.remove('active','border-blue-600','text-blue-700'); b.classList.add('border-transparent','text-slate-500'); });
        e.currentTarget.classList.add('active','border-blue-600','text-blue-700'); e.currentTarget.classList.remove('border-transparent','text-slate-500');
        document.querySelectorAll('.ptab-pane').forEach(p => p.classList.add('hidden'));
        document.getElementById(e.currentTarget.dataset.target).classList.remove('hidden');
    });
});

function renderGoodsTable() {
    const body = document.getElementById('goods-table-body'); if(!body) return;
    if(!portalData?.my_products || portalData.my_products.length === 0) { body.innerHTML = `<tr><td colspan="5" class="p-8 text-center text-slate-400 font-bold">No partner products added yet.</td></tr>`; return; }
    body.innerHTML = portalData.my_products.map(p => {
        const off = p.data.supplyOffers && p.data.supplyOffers.length > 0 ? p.data.supplyOffers[0] : {};
        return `
        <tr class="border-b hover:bg-slate-50 transition-colors">
            <td class="p-4"><div class="font-black text-slate-900">${p.data.name}</div><div class="text-[10px] font-mono text-slate-500 mt-1">${p.data.sku||''}</div></td>
            <td class="p-4 font-mono font-bold text-emerald-600 text-lg">${off.price||0} ${off.currency||'USD'} <span class="text-xs text-slate-500 ml-1">/ ${off.unit||'MT'}</span></td>
            <td class="p-4 text-xs whitespace-pre-wrap max-w-xs text-slate-600">${p.data.detailedSpec || 'N/A'}</td>
            <td class="p-4"><span class="px-2 py-1 rounded text-[10px] font-black uppercase tracking-wider border shadow-sm ${statusColors[p.status] || statusColors['default']}">${p.status}</span></td>
            <td class="p-4 text-right"><button class="text-[10px] font-black uppercase bg-white hover:bg-blue-50 text-slate-700 hover:text-blue-700 px-3 py-2 rounded-lg border shadow-sm transition-all" onclick="editProductItem('${p.id}')">EDIT</button></td>
        </tr>`;
    }).join('');
}

function renderPortalCOA() {
    document.getElementById('portal-coa-list').innerHTML = activeCOAParams.map((c, i) => `
        <div class="flex items-center justify-between bg-white border rounded p-2 text-xs font-bold text-slate-700 shadow-sm">
            <span><span class="text-slate-400 uppercase tracking-widest text-[9px] mr-2">${c.name}:</span> ${c.value}</span>
            <button type="button" class="text-red-500 hover:bg-red-50 px-2 rounded" onclick="removePortalCOA(${i})">✕</button>
        </div>`).join('');
}
function addPortalCOA() { const n = document.getElementById('p-coa-name').value; const v = document.getElementById('p-coa-value').value; if(n&&v) { activeCOAParams.push({name:n, value:v}); document.getElementById('p-coa-name').value=''; document.getElementById('p-coa-value').value=''; renderPortalCOA(); } }
window.removePortalCOA = function(i) { activeCOAParams.splice(i, 1); renderPortalCOA(); };

function openProductModal() {
    document.getElementById('product-form').reset();
    document.getElementById('form-product-id').value = '';
    document.getElementById('form-existing-certs').innerHTML = '';
    uploadedCertUrls = []; activeCOAParams = []; renderPortalCOA();
    document.getElementById('product-modal').classList.remove('hidden');
}
function closeProductModal() { document.getElementById('product-modal').classList.add('hidden'); }

function editProductItem(id) {
    const prod = portalData.my_products.find(p => p.id === id); if(!prod) return;
    document.getElementById('form-product-id').value = prod.id;
    document.getElementById('form-product-name').value = prod.data.name || '';
    document.getElementById('form-product-category').value = prod.data.category || '';
    document.getElementById('form-product-hscode').value = prod.data.hsCode || '';
    document.getElementById('form-product-sku').value = prod.data.sku || '';
    document.getElementById('form-product-brand').value = prod.data.brand || '';
    document.getElementById('form-product-cap20').value = prod.data.logistics?.cap20 || '';
    document.getElementById('form-product-cap40').value = prod.data.logistics?.cap40 || '';
    document.getElementById('form-product-spec').value = prod.data.detailedSpec || '';
    
    const off = prod.data.supplyOffers && prod.data.supplyOffers.length > 0 ? prod.data.supplyOffers[0] : {};
    document.getElementById('form-product-price').value = off.price || '';
    document.getElementById('form-product-currency').value = off.currency || 'USD';
    document.getElementById('form-product-unit').value = off.unit || 'MT';
    document.getElementById('form-product-moq').value = off.moq || '';
    document.getElementById('form-product-incoterm').value = off.incoterm || 'FOB';
    document.getElementById('form-product-origin').value = off.country || '';
    document.getElementById('form-product-valid').value = off.validUntil || '';

    activeCOAParams = prod.data.coaParams || []; renderPortalCOA();
    uploadedCertUrls = off.certificates ? off.certificates.split(', ') : [];
    document.getElementById('form-existing-certs').innerHTML = uploadedCertUrls.map((c, i) => `<a href="${c}" target="_blank" class="block">✓ Current Certificate #${i+1}</a>`).join('');
    document.getElementById('product-modal').classList.remove('hidden');
}

function renderDocuments() {
    const body = document.getElementById('documents-table-body'); 
    if(!body) return;
    
    if(!portalData?.documents || portalData.documents.length === 0) { 
        body.innerHTML = `<tr><td colspan="4" class="p-8 text-center text-slate-400 font-bold">${t('no_docs')}</td></tr>`; 
        return; 
    }
    
    body.innerHTML = portalData.documents.map(d => `
        <tr class="border-b hover:bg-slate-50 transition-colors">
            <td class="p-4 text-xs font-bold text-slate-500 whitespace-nowrap">${new Date(d.createdAt).toLocaleDateString()}</td>
            <td class="p-4"><span class="px-3 py-1.5 rounded-md text-[10px] font-black uppercase tracking-widest border bg-slate-100 text-slate-700 shadow-sm">${d.docType || 'Document'}</span></td>
            <td class="p-4 font-bold text-slate-900">${d.fileName || 'Official_Document.pdf'}</td>
            <td class="p-4 text-right">
                <a href="${d.fileUrl}" target="_blank" download class="inline-block bg-white text-blue-700 hover:bg-blue-600 hover:text-white font-black text-[10px] uppercase tracking-widest px-4 py-2 rounded-lg transition-colors border border-blue-200 shadow-sm">
                    ${t('btn_download')}
                </a>
            </td>
        </tr>
    `).join('');
}

// DODATO: Funkcija za dinamičko sakrivanje tabova na osnovu dozvola
window.applyPermissions = function(permissions) {
    if (!permissions || permissions.length === 0) return;

    // Sakrij sve tabove prvo
    document.querySelectorAll('.ptab-btn').forEach(tab => {
        tab.classList.add('hidden');
    });

    // Otkrij samo one za koje postoji dozvola
    let firstVisibleSet = false;
    document.querySelectorAll('.ptab-btn').forEach(tab => {
        const targetId = tab.getAttribute('data-target') || '';
        const hasPermission = permissions.some(p => targetId.includes(p)); 
        
        // Osnovni tabovi uvek treba da budu vidljivi
        if (hasPermission || targetId.includes('kyc') || targetId.includes('profile')) {
            tab.classList.remove('hidden');
            
            if (!firstVisibleSet) {
                tab.click();
                firstVisibleSet = true;
            }
        }
    });
};