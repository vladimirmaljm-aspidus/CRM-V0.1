// static/js/modules/products/products_form.js

function showProductForm(id = null) {
    state.editingItem = id ? state.data.products.find(p => p.id === id) : null; 
    const item = state.editingItem || { supplyOffers: [], inventory: [], coaParams: [], logistics: {}, tags: [] };
    item.inventory = item.inventory || [];
    item.coaParams = item.coaParams || [];
    item.logistics = item.logistics || { cap20: '', cap40: '' };
    item.tags = item.tags || [];
    
    const catFallback = typeof PRODUCT_CATEGORIES !== 'undefined' ? PRODUCT_CATEGORIES : [];
    const categoriesOptions = [''].concat(catFallback).map(c => {
        const catName = typeof getTranslatedCategory === 'function' ? getTranslatedCategory(c) : c;
        return `<option value="${c}" ${item.category === c ? 'selected' : ''}>${catName || '...'}</option>`;
    }).join('');
    
    const isSupplier = (p) => (p.types || []).includes('supplier') || (p.types || []).includes('Dobavljač');
    const supplierOptions = state.data.partners.filter(isSupplier).map(p => `<option value="${p.id}">${Utils.escapeHtml(p.companyName)}</option>`).join('');
    
    let currentEditOfferIndex = -1;
    const currentLang = Utils.getLang() === 'sr' ? 'sr-RS' : 'en-US';
    const tLang = (srStr, enStr) => Utils.getLang() === 'sr' ? srStr : enStr;
    const predefinedTags = ['New Crop', 'Fast Moving', 'Bestseller', 'Organic', 'Fairtrade', 'Premium', 'Clearance'];
    
    const tagsHtml = predefinedTags.map(t => {
        const isChecked = item.tags.includes(t);
        return `<label class="inline-flex items-center text-xs bg-white px-3 py-1.5 rounded-full cursor-pointer hover:bg-slate-50 border border-slate-200 text-slate-700 font-bold transition-colors"><input type="checkbox" name="product_tags" value="${t}" ${isChecked?'checked':''} class="mr-2 w-4 h-4 text-blue-600 focus:ring-blue-500 rounded-sm border-slate-300"> ${t}</label>`;
    }).join('');

    const renderOffersList = () => {
        return (item.supplyOffers || []).map((o, i) => {
            const histBtn = (o.history && o.history.length > 0) ? `<button type="button" class="bg-blue-50 hover:bg-blue-100 text-blue-700 font-bold px-3 py-1 rounded text-xs transition-colors w-full mt-2 border border-blue-200" onclick="document.getElementById('hist-${i}').classList.toggle('hidden')">⏱️ ${Utils.t('actions.history') || 'Istorija Cena'}</button>` : '';
            const histLog = (o.history || []).map(h => `<div class="text-[10px] text-slate-600 border-l-2 border-amber-400 pl-2 mb-1.5 bg-slate-50 p-1.5 rounded">${Utils.t('fields.modified') || 'Izmenjeno:'} <strong>${new Date(h.date).toLocaleString(currentLang)}</strong><br>${Utils.t('fields.oldPrice') || 'Stara Cena:'} <strong class="text-red-500">${h.price} ${h.currency || o.currency}</strong> | ${Utils.t('fields.incoterm') || 'Incoterm'}: ${h.incoterm}</div>`).join('');
            
            const reserved = Math.max(
                (state.data.offers || []).filter(off => off.productId === item.id && off.offerIndex === i).reduce((s, off) => s + Number(off.quantity || 0), 0),
                (state.data.deals || []).filter(d => d.productId === item.id && d.supplierId === o.supplierId).reduce((s, d) => s + Number(d.quantity || 0), 0)
            );
            const available = (o.quantity || 0) - reserved;
            
            let stockWarning = '';
            if (available < 0) stockWarning = `<span class="bg-red-600 text-white px-2 py-0.5 rounded text-[10px] ml-2 font-black uppercase animate-pulse">Manjak: ${available}</span>`;
            else if (available === 0) stockWarning = `<span class="bg-amber-100 text-amber-800 border border-amber-300 px-2 py-0.5 rounded text-[10px] ml-2 font-black uppercase">Rasprodato</span>`;
            
            let validityBadge = '';
            if(o.validUntil) {
                const diffDays = Math.ceil((new Date(o.validUntil) - new Date()) / (1000 * 60 * 60 * 24));
                if(diffDays < 0) validityBadge = `<span class="bg-red-50 text-red-700 text-[9px] font-black px-1.5 py-0.5 rounded border border-red-200 ml-2 uppercase">${tLang('ISTEKLA PONUDA', 'EXPIRED')}</span>`;
                else if(diffDays <= 7) validityBadge = `<span class="bg-orange-50 text-orange-700 text-[9px] font-black px-1.5 py-0.5 rounded border border-orange-200 ml-2 uppercase">${tLang('Istiće uskoro', 'Expiring soon')} (${diffDays}d)</span>`;
                else validityBadge = `<span class="text-[9px] text-slate-500 ml-2 font-bold uppercase tracking-wider">${tLang('Važi do', 'Valid until')}: ${new Date(o.validUntil).toLocaleDateString(currentLang)}</span>`;
            }
            return `
            <div class="mb-3 p-4 border border-slate-200 rounded-xl bg-white shadow-sm ${o.validUntil && Math.ceil((new Date(o.validUntil) - new Date()) / (1000 * 60 * 60 * 24)) < 0 ? 'opacity-70 grayscale' : ''}">
                <div class="flex justify-between items-start mb-2">
                    <div class="text-sm text-slate-800">
                        <div class="flex items-center"><span class="text-xs text-slate-500 uppercase tracking-widest font-black mr-2">${tLang('Dobavljač', 'Supplier')}:</span> <strong class="text-base">${Utils.getPartnerNameById(o.supplierId)}</strong> ${validityBadge}</div>
                        <div class="text-blue-700 mt-2 text-xs font-bold bg-blue-50 inline-block px-2 py-1 rounded border border-blue-200">
                            Zalihe kod dobavljača: <strong>${o.quantity || 0} ${Utils.escapeHtml(o.unit || '')}</strong> | Rezervisano: <strong>${reserved}</strong> | Slobodno: <strong class="${available < 0 ? 'text-red-500' : 'text-emerald-600'}">${available}</strong> ${stockWarning}
                        </div>
                        <div class="mt-3 flex items-center gap-3">
                            <span class="bg-slate-50 px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-bold text-slate-600 uppercase tracking-wider">Cena: <strong class="text-lg text-slate-900 font-black ml-1">${Utils.formatCurrency(o.price, o.currency)}</strong> / ${Utils.escapeHtml(o.unit || '')}</span>
                            <span class="bg-slate-50 px-2 py-1.5 rounded-lg border border-slate-200 text-xs font-black text-slate-600">${Utils.escapeHtml(o.incoterm || 'N/A')}</span>
                            ${o.moq ? `<span class="bg-purple-50 text-purple-700 text-[10px] font-black px-2 py-1.5 rounded border border-purple-200 tracking-widest uppercase">MOQ: ${o.moq} ${Utils.escapeHtml(o.unit || '')}</span>` : ''}
                        </div>
                        <div class="mt-3 text-xs text-slate-600 grid grid-cols-2 gap-2">
                            <div><strong class="uppercase tracking-wider text-[9px] block mb-0.5 text-slate-400">Poreklo</strong> <span class="font-bold text-slate-800">${Utils.escapeHtml(o.country || 'N/A')}</span></div>
                            ${o.certificates ? `<div><strong class="uppercase tracking-wider text-[9px] block mb-0.5 text-slate-400">Sertifikati</strong> <span class="font-bold text-slate-800">${Utils.escapeHtml(o.certificates)}</span></div>` : ''}
                        </div>
                    </div>
                    <div class="flex flex-col gap-2">
                        <button type="button" class="bg-amber-50 hover:bg-amber-100 border border-amber-200 text-amber-700 font-bold px-3 py-1.5 rounded shadow-sm text-xs transition-colors edit-offer" data-index="${i}">✏️ ${tLang('Izmeni', 'Edit')}</button>
                        <button type="button" class="bg-red-50 hover:bg-red-100 text-red-600 border border-red-200 font-bold px-3 py-1.5 rounded shadow-sm text-xs transition-colors remove-offer" data-index="${i}">🗑️ ${tLang('Obriši', 'Delete')}</button>
                    </div>
                </div>
                ${histBtn}
                <div id="hist-${i}" class="hidden mt-2 border-t border-slate-200 pt-2">${histLog}</div>
            </div>`;
        }).join('');
    };

    const renderInventoryList = () => {
        const stMap = {
            'available': { label: tLang('Slobodno', 'Available'), cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
            'in_transit': { label: tLang('U tranzitu', 'In Transit'), cls: 'bg-blue-50 text-blue-700 border-blue-200' },
            'customs': { label: tLang('Na carini', 'Customs Hold'), cls: 'bg-orange-50 text-orange-700 border-orange-200' },
            'reserved': { label: tLang('Rezervisano', 'Reserved'), cls: 'bg-slate-100 text-slate-700 border-slate-300' }
        };
        return item.inventory.map((inv, i) => {
            const expBadge = inv.expiry ? `<span class="ml-2 text-[10px] bg-red-50 text-red-700 px-2 py-0.5 rounded border border-red-200 font-black tracking-widest uppercase">EXP: ${new Date(inv.expiry).toLocaleDateString(currentLang)}</span>` : '';
            const stBadgeInfo = stMap[inv.status || 'available'];
            const statusBadge = `<span class="ml-2 text-[10px] px-2 py-0.5 rounded border font-black uppercase tracking-wider shadow-sm ${stBadgeInfo.cls}">${stBadgeInfo.label}</span>`;
            
            return `
            <div class="mb-2 p-4 border border-emerald-200 bg-emerald-50/50 rounded-xl flex justify-between items-center shadow-sm">
                <div class="text-sm text-slate-800">
                    <div class="flex items-center mb-1"><span class="text-lg mr-2">📍</span> <strong class="text-base">${Utils.escapeHtml(inv.location)}</strong> <span class="text-slate-400 mx-2">|</span> <span class="text-xs font-mono text-slate-600 font-bold">BATCH: ${Utils.escapeHtml(inv.batchNo || 'N/A')}</span></div>
                    <div class="flex items-center mt-2">
                        <span class="bg-white border border-emerald-200 px-3 py-1 rounded-lg shadow-sm font-black text-emerald-700 text-base">${inv.qty} ${Utils.escapeHtml(item.supplyOffers[0]?.unit || 'MT')}</span>
                        <span class="text-slate-400 font-bold mx-2">@</span>
                        <span class="text-slate-800 font-bold">${Utils.formatCurrency(inv.purchasePrice, inv.currency)}</span>
                        ${statusBadge}
                        ${expBadge}
                    </div>
                </div>
                <button type="button" class="bg-white hover:bg-red-50 text-red-500 border border-slate-200 hover:border-red-200 font-black px-3 py-2 rounded-lg transition-colors shadow-sm remove-inv" data-index="${i}">🗑️</button>
            </div>`;
        }).join('') || `<div class="p-8 text-center border-2 border-dashed border-emerald-200 rounded-xl bg-emerald-50/30"><span class="text-3xl block mb-2">📦</span><p class="text-emerald-700 font-bold text-sm">${tLang('Sopstveni magacin je prazan.', 'Own warehouse is empty.')}</p></div>`;
    };

    const renderCOAList = () => {
        return item.coaParams.map((coa, i) => `
            <div class="flex items-center gap-3 mb-2 p-3 bg-white border border-slate-200 rounded-lg shadow-sm">
                <span class="font-black text-xs uppercase tracking-widest text-slate-500 min-w-[120px]">${Utils.escapeHtml(coa.name)}:</span>
                <span class="text-sm font-bold text-slate-800 flex-1">${Utils.escapeHtml(coa.value)}</span>
                <button type="button" class="text-red-500 hover:text-red-700 bg-red-50 border border-red-100 px-2 py-1 rounded shadow-sm transition-colors remove-coa" data-index="${i}">✕</button>
            </div>
        `).join('') || `<p class="text-slate-400 text-xs italic text-center p-4 border border-dashed border-slate-200 rounded-lg">${tLang('Nema definisanih parametara.', 'No parameters defined.')}</p>`;
    };

    const html = `
    <form id="product-form" class="space-y-0 relative">
      
      <!-- TABS HEADER -->
      <div class="flex overflow-x-auto border-b border-slate-200 mb-6 custom-scrollbar pb-1">
          <button type="button" class="prod-tab-btn active px-6 py-3 border-b-2 border-blue-600 font-black text-blue-600 text-xs uppercase tracking-widest whitespace-nowrap transition-colors" data-target="tab-general">📝 ${tLang('Osnovni Podaci', 'General Info')}</button>
          <button type="button" class="prod-tab-btn px-6 py-3 border-b-2 border-transparent font-bold text-slate-500 hover:text-slate-800 text-xs uppercase tracking-widest whitespace-nowrap transition-colors" data-target="tab-commercial">🤝 ${tLang('Ponude Dobavljača', 'Supplier Offers')}</button>
          <button type="button" class="prod-tab-btn px-6 py-3 border-b-2 border-transparent font-bold text-slate-500 hover:text-slate-800 text-xs uppercase tracking-widest whitespace-nowrap transition-colors" data-target="tab-inventory">📦 ${tLang('Naš Lager (Zalihe)', 'Own Inventory')}</button>
          <button type="button" class="prod-tab-btn px-6 py-3 border-b-2 border-transparent font-bold text-slate-500 hover:text-slate-800 text-xs uppercase tracking-widest whitespace-nowrap transition-colors" data-target="tab-specs">🧪 ${tLang('Specifikacija & COA', 'Specs & COA')}</button>
      </div>

      <!-- TAB 1: GENERAL INFO -->
      <div id="tab-general" class="prod-pane block space-y-6">
          <div class="bg-slate-50 p-6 rounded-xl border border-slate-200 shadow-sm">
              <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
                  <div class="md:col-span-2"><label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">${Utils.t('fields.productName')} <span class="text-red-500">*</span></label><input name="name" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-3 text-sm font-bold text-slate-900 focus:ring-2 focus:ring-blue-500 outline-none transition-all shadow-sm" value="${Utils.escapeHtml(item.name || '')}" required /></div>
                  <div><label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">${tLang('Slika (URL)', 'Image URL')}</label><input name="imageUrl" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-3 text-sm font-medium text-slate-800 focus:ring-2 focus:ring-blue-500 outline-none transition-all shadow-sm" value="${Utils.escapeHtml(item.imageUrl || '')}" placeholder="https://..." /></div>
              </div>
              
              <div class="grid grid-cols-2 md:grid-cols-4 gap-6 mb-6 border-t border-slate-200 pt-6">
                  <div><label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">${Utils.t('fields.category')}</label><select name="category" class="w-full bg-white border border-slate-300 rounded-lg px-3 py-3 text-sm font-bold text-slate-800 focus:ring-2 focus:ring-blue-500 outline-none shadow-sm cursor-pointer">${categoriesOptions}</select></div>
                  <div><label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">${Utils.t('fields.hsCode')}</label><input name="hsCode" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-3 text-sm font-mono text-slate-800 focus:ring-2 focus:ring-blue-500 outline-none shadow-sm" value="${Utils.escapeHtml(item.hsCode || '')}" placeholder="Npr. 18010000" /></div>
                  <div><label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">SKU / Article No.</label><input name="sku" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-3 text-sm font-mono text-slate-800 focus:ring-2 focus:ring-blue-500 outline-none shadow-sm" value="${Utils.escapeHtml(item.sku || '')}" placeholder="Npr. CCO-001" /></div>
                  <div><label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">${tLang('Brend / Proizvođač', 'Brand / Mfgr')}</label><input name="brand" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-3 text-sm font-bold text-slate-800 focus:ring-2 focus:ring-blue-500 outline-none shadow-sm" value="${Utils.escapeHtml(item.brand || '')}" placeholder="Npr. Cargill" /></div>
              </div>

              <div class="grid grid-cols-1 md:grid-cols-2 gap-6 border-t border-slate-200 pt-6">
                  <div class="bg-blue-50 p-5 rounded-xl border border-blue-200 shadow-inner">
                      <label class="block text-[10px] font-black text-blue-700 uppercase tracking-widest mb-2 flex items-center gap-2">🎯 ${tLang('Ciljana Nabavna Cena (Deal Radar)', 'Target Purchase Price')}</label>
                      <p class="text-[10px] text-blue-600 mb-3">${tLang('Cena koju ciljamo. Ponude ispod ove cene biće markirane zeleno.', 'Price target. Offers below this will be highlighted green.')}</p>
                      <div class="flex gap-2">
                          <input name="targetPrice" type="number" step="0.01" class="w-full bg-white border border-blue-300 rounded-lg px-4 py-3 text-lg font-black text-slate-900 focus:ring-2 focus:ring-blue-500 outline-none shadow-sm" value="${item.targetPrice || ''}" placeholder="0.00">
                          <select name="targetCurrency" class="w-32 bg-white border border-blue-300 rounded-lg px-3 py-3 text-sm font-black text-slate-900 focus:ring-2 focus:ring-blue-500 outline-none shadow-sm cursor-pointer">${CURRENCIES.map(c => `<option value="${c}" ${(item.targetCurrency || 'USD') === c ? 'selected' : ''}>${c}</option>`).join('')}</select>
                      </div>
                  </div>
                  <div>
                      <label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">🏷️ ${tLang('Oznake / Tagovi Proizvoda', 'Product Tags')}</label>
                      <div class="flex flex-wrap gap-2 p-4 bg-white rounded-xl border border-slate-200 shadow-sm min-h-[105px] content-start">
                          ${tagsHtml}
                      </div>
                  </div>
              </div>
          </div>
      </div>

      <!-- TAB 2: SUPPLIER OFFERS -->
      <div id="tab-commercial" class="prod-pane hidden space-y-6">
          <div class="bg-slate-50 p-6 rounded-xl border border-slate-200 shadow-sm">
              <div class="flex justify-between items-center mb-6">
                  <h4 class="font-black text-slate-800 uppercase tracking-widest text-sm flex items-center gap-2">🤝 ${Utils.t('fields.supplier_offers') || 'Aktuelne Ponude Dobavljača'}</h4>
              </div>
              <div id="offers-list" class="space-y-4 mb-8 max-h-[400px] overflow-y-auto custom-scrollbar pr-2">${renderOffersList() || `<div class="text-slate-500 text-sm italic border-dashed border-2 border-slate-300 p-10 text-center rounded-xl font-bold">${Utils.t('product_search.noResults') || 'Nema ponuda.'}</div>`}</div>
              
              <div id="offer-edit-box" class="p-6 border border-slate-300 bg-white rounded-xl shadow-lg space-y-4 transition-all relative">
                  <h5 class="font-black text-blue-600 uppercase tracking-widest text-xs flex items-center gap-2 mb-2 pb-3 border-b border-slate-200" id="offer-box-title">➕ ${Utils.t('actions.add_new_offer') || 'Dodaj Novu Ponudu'}</h5>
                  <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div><label class="block text-[9px] font-black text-slate-500 uppercase tracking-widest mb-1.5">${Utils.t('actions.select_supplier')}</label><select id="offer-supplier" class="w-full bg-slate-50 border border-slate-300 rounded-md px-3 py-2 text-sm font-bold text-slate-800 outline-none focus:border-blue-500 cursor-pointer"><option value="">-- Izaberite Dobavljača --</option>${supplierOptions}</select></div>
                      <div><label class="block text-[9px] font-black text-slate-500 uppercase tracking-widest mb-1.5">${tLang('Poreklo / Država', 'Country of Origin')}</label><div class="autocomplete-container"><input id="offer-country" class="w-full bg-slate-50 border border-slate-300 rounded-md px-3 py-2 text-sm font-bold text-slate-800 outline-none focus:border-blue-500" placeholder="Npr. Indonesia" autocomplete="off" /></div></div>
                  </div>
                  <div class="grid grid-cols-3 md:grid-cols-6 gap-4 border-t border-slate-200 pt-4">
                      <div class="col-span-2 md:col-span-1"><label class="block text-[9px] font-black text-slate-500 uppercase tracking-widest mb-1.5">Količina (Qty)</label><input id="offer-qty" class="w-full bg-slate-50 border border-slate-300 rounded-md px-3 py-2 text-sm font-black text-slate-900 outline-none focus:border-blue-500" placeholder="0.00" type="number" step="0.01" /></div>
                      <div class="col-span-1 md:col-span-1"><label class="block text-[9px] font-black text-slate-500 uppercase tracking-widest mb-1.5">Jedinica</label><select id="offer-unit" class="w-full bg-slate-50 border border-slate-300 rounded-md px-3 py-2 text-sm font-bold text-slate-800 outline-none focus:border-blue-500 cursor-pointer">${UNITS.map(u => `<option value="${u}">${u}</option>`).join('')}</select></div>
                      <div class="col-span-1 md:col-span-1"><label class="block text-[9px] font-black text-slate-500 uppercase tracking-widest mb-1.5">Min (MOQ)</label><input id="offer-moq" class="w-full bg-slate-50 border border-slate-300 rounded-md px-3 py-2 text-sm font-bold text-slate-800 outline-none focus:border-blue-500" placeholder="0.00" type="number" step="0.01" /></div>
                      <div class="col-span-2 md:col-span-2"><label class="block text-[9px] font-black text-blue-600 uppercase tracking-widest mb-1.5">Nabavna Cena</label><input id="offer-price" class="w-full bg-blue-50 border border-blue-300 rounded-md px-3 py-2 text-base font-black text-blue-800 outline-none focus:ring-2 focus:ring-blue-500 shadow-inner" placeholder="0.00" type="number" step="0.01" /></div>
                      <div class="col-span-1 md:col-span-1"><label class="block text-[9px] font-black text-slate-500 uppercase tracking-widest mb-1.5">Valuta</label><select id="offer-currency" class="w-full bg-slate-50 border border-slate-300 rounded-md px-3 py-2 text-sm font-bold text-slate-800 outline-none focus:border-blue-500 cursor-pointer">${CURRENCIES.map(c => `<option value="${c}">${c}</option>`).join('')}</select></div>
                  </div>
                  <div class="grid grid-cols-2 gap-4 border-t border-slate-200 pt-4">
                      <div><label class="block text-[9px] font-black text-slate-500 uppercase tracking-widest mb-1.5">${Utils.t('fields.incoterm')}</label><select id="offer-incoterm" class="w-full bg-slate-50 border border-slate-300 rounded-md px-3 py-2 text-sm font-bold text-slate-800 outline-none focus:border-blue-500 cursor-pointer"><option value="">-- Paritet --</option>${INCOTERMS.map(i => `<option value="${i}">${i}</option>`).join('')}</select></div>
                      <div><label class="block text-[9px] font-black text-red-500 uppercase tracking-widest mb-1.5">Važi do (Valid Until)</label><input id="offer-validUntil" type="date" class="w-full bg-red-50 border border-red-200 text-red-800 rounded-md px-3 py-2 text-sm font-bold outline-none focus:ring-2 focus:ring-red-500 shadow-inner" /></div>
                  </div>
                  <div class="pt-4 border-t border-slate-200">
                      <label class="block text-[9px] font-black text-slate-500 uppercase tracking-widest mb-2">${Utils.t('fields.certificates')}</label>
                      <div id="offer-certs-wrapper" class="flex flex-wrap gap-2 p-3 bg-slate-50 rounded-lg border border-slate-200 shadow-inner">
                          ${(typeof CERTIFICATES !== 'undefined' ? CERTIFICATES : []).map(c => `<label class="inline-flex items-center text-xs bg-white px-3 py-1.5 rounded-full cursor-pointer hover:bg-blue-50 border border-slate-200 text-slate-700 font-bold transition-colors shadow-sm"><input type="checkbox" name="offer_cert" value="${c}" class="mr-2 w-4 h-4 text-blue-600 focus:ring-blue-500 rounded-sm border-slate-300"> ${c}</label>`).join('')}
                      </div>
                  </div>
                  <div class="flex justify-end mt-4 gap-3 pt-4 border-t border-slate-200">
                      <button type="button" id="cancel-offer-edit" class="bg-white border border-slate-300 text-slate-600 hover:bg-slate-50 font-bold px-6 py-2 rounded-lg text-xs shadow-sm transition-colors hidden">${tLang('Odustani', 'Cancel')}</button>
                      <button type="button" id="add-offer" class="bg-blue-600 hover:bg-blue-700 text-white font-black px-8 py-2 rounded-lg text-xs shadow-md tracking-wider uppercase transition-transform transform hover:-translate-y-0.5">💾 ${Utils.t('actions.save')}</button>
                  </div>
              </div>
          </div>
      </div>

      <!-- TAB 3: INVENTORY -->
      <div id="tab-inventory" class="prod-pane hidden space-y-6">
          <div class="bg-emerald-50/50 p-6 rounded-xl border border-emerald-200 shadow-sm">
              <h4 class="font-black mb-6 text-emerald-800 uppercase tracking-widest text-sm flex items-center gap-2">📦 ${tLang('Sopstveni Magacin / Lager', 'Own Inventory & Stock')}</h4>
              
              <div id="inventory-list" class="space-y-3 mb-8 max-h-[300px] overflow-y-auto custom-scrollbar pr-2">${renderInventoryList()}</div>
              
              <div class="p-6 border border-emerald-300 bg-white rounded-xl shadow-lg space-y-4">
                  <h5 class="font-black text-emerald-700 uppercase tracking-widest text-xs flex items-center gap-2 border-b border-slate-200 pb-3 mb-2">➕ ${tLang('Dodaj robu na stanje', 'Receive Inventory')}</h5>
                  <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div><label class="block text-[9px] font-black text-slate-500 uppercase tracking-widest mb-1.5">${tLang('Fizička Lokacija (Skladište)', 'Physical Location')}</label><input id="inv-loc" class="w-full bg-slate-50 border border-slate-300 rounded-md px-3 py-2 text-sm font-bold text-slate-900 outline-none focus:border-emerald-500" placeholder="${tLang('Npr. Jebel Ali, Dubai', 'e.g. Jebel Ali')}" /></div>
                      <div><label class="block text-[9px] font-black text-slate-500 uppercase tracking-widest mb-1.5">Batch / LOT No.</label><input id="inv-batch" class="w-full bg-slate-50 border border-slate-300 rounded-md px-3 py-2 text-sm font-mono text-slate-900 outline-none focus:border-emerald-500" placeholder="Npr. LOT-2026-05A" /></div>
                  </div>
                  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 border-t border-slate-200 pt-4">
                      <div><label class="block text-[9px] font-black text-slate-500 uppercase tracking-widest mb-1.5">Količina (Qty)</label><input id="inv-qty" class="w-full bg-emerald-50 border border-emerald-300 text-emerald-900 rounded-md px-3 py-2 text-lg font-black outline-none focus:ring-2 focus:ring-emerald-500 shadow-inner" placeholder="0.00" type="number" step="0.01" /></div>
                      <div><label class="block text-[9px] font-black text-slate-500 uppercase tracking-widest mb-1.5">Nabavna Cena</label><input id="inv-price" class="w-full bg-slate-50 border border-slate-300 rounded-md px-3 py-2 text-sm font-bold text-slate-900 outline-none focus:border-emerald-500" placeholder="0.00" type="number" step="0.01" /></div>
                      <div><label class="block text-[9px] font-black text-slate-500 uppercase tracking-widest mb-1.5">Valuta</label><select id="inv-currency" class="w-full bg-slate-50 border border-slate-300 rounded-md px-3 py-2 text-sm font-bold text-slate-900 outline-none cursor-pointer focus:border-emerald-500">${CURRENCIES.map(c => `<option value="${c}">${c}</option>`).join('')}</select></div>
                      <div><label class="block text-[9px] font-black text-red-500 uppercase tracking-widest mb-1.5">Rok trajanja (Expiry)</label><input id="inv-expiry" type="date" class="w-full bg-red-50 border border-red-200 text-red-800 rounded-md px-3 py-2 text-sm font-bold outline-none focus:ring-2 focus:ring-red-500 shadow-inner" /></div>
                  </div>
                  <div class="border-t border-slate-200 pt-4">
                      <label class="block text-[9px] font-black text-slate-500 uppercase tracking-widest mb-1.5">Trenutni Status Robe</label>
                      <select id="inv-status" class="w-full bg-white border border-slate-300 rounded-md px-4 py-3 text-sm font-black text-slate-800 outline-none cursor-pointer focus:border-emerald-500 shadow-sm">
                          <option value="available" class="text-emerald-700">🟢 ${tLang('Slobodno za prodaju', 'Available for Sale')}</option>
                          <option value="in_transit" class="text-blue-700">🔵 ${tLang('U tranzitu (Na putu)', 'In Transit')}</option>
                          <option value="customs" class="text-orange-700">🟠 ${tLang('Na carini / Inspekciji', 'Customs / Inspection')}</option>
                          <option value="reserved" class="text-slate-700">⚫ ${tLang('Rezervisano za kupca', 'Reserved for Buyer')}</option>
                      </select>
                  </div>
                  <div class="flex justify-end mt-4 pt-4 border-t border-slate-200">
                      <button type="button" id="add-inv-btn" class="bg-emerald-600 hover:bg-emerald-700 text-white font-black px-8 py-2.5 rounded-lg text-xs shadow-md tracking-wider uppercase transition-transform transform hover:-translate-y-0.5">💾 ${Utils.t('actions.save') || 'Sačuvaj na Lager'}</button>
                  </div>
              </div>
          </div>
      </div>

      <!-- TAB 4: SPECS & COA -->
      <div id="tab-specs" class="prod-pane hidden space-y-6">
          <div class="bg-slate-50 p-6 rounded-xl border border-slate-200 shadow-sm">
              <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
                  
                  <div>
                      <h4 class="font-black text-sm uppercase tracking-widest text-slate-800 mb-4 flex items-center gap-2">🧪 Kvalitet & COA Parametri</h4>
                      <p class="text-[10px] text-slate-500 font-bold mb-4">${tLang('Unesite specifikacije iz laboratorijske analize (COA).', 'Enter specs from Certificate of Analysis.')}</p>
                      
                      <div id="coa-list" class="mb-4 max-h-60 overflow-y-auto custom-scrollbar pr-2 space-y-2">${renderCOAList()}</div>
                      
                      <div class="flex flex-col md:flex-row gap-3 border border-blue-200 bg-blue-50 p-4 rounded-xl shadow-sm mt-4">
                          <input id="coa-name" class="w-full bg-white border border-slate-300 rounded-md px-3 py-2 text-sm font-bold text-slate-900 outline-none focus:border-blue-500" placeholder="${tLang('Parametar (npr. Vlaga)', 'Parameter (e.g. Moisture)')}" />
                          <input id="coa-value" class="w-full bg-white border border-slate-300 rounded-md px-3 py-2 text-sm font-bold text-slate-900 outline-none focus:border-blue-500" placeholder="${tLang('Vrednost (npr. max 5%)', 'Value (e.g. max 5%)')}" />
                          <button type="button" id="add-coa-btn" class="bg-blue-600 hover:bg-blue-700 text-white font-black px-6 py-2 rounded-lg text-xs shadow-sm transition-colors uppercase tracking-wider w-full md:w-auto">Dodaj</button>
                      </div>
                  </div>

                  <div class="space-y-6">
                      <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
                          <h4 class="font-black text-sm uppercase tracking-widest text-slate-800 mb-4 flex items-center gap-2">🚢 Kontejnerska Logistika</h4>
                          <div class="grid grid-cols-2 gap-4">
                              <div><label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-1.5">Capacity 1x20'ft (MT)</label><input name="cap20" type="number" class="w-full bg-slate-50 border border-slate-300 rounded-md px-3 py-2 text-sm font-mono text-slate-900 outline-none focus:border-blue-500" value="${item.logistics.cap20 || ''}" placeholder="Npr. 17" /></div>
                              <div><label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-1.5">Capacity 1x40'ft (MT)</label><input name="cap40" type="number" class="w-full bg-slate-50 border border-slate-300 rounded-md px-3 py-2 text-sm font-mono text-slate-900 outline-none focus:border-blue-500" value="${item.logistics.cap40 || ''}" placeholder="Npr. 25" /></div>
                          </div>
                      </div>
                      
                      <div>
                          <label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">Shelf Life (Rok trajanja)</label>
                          <input name="shelfLife" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-3 text-sm font-bold text-slate-900 focus:ring-2 focus:ring-blue-500 outline-none transition-all shadow-sm" value="${Utils.escapeHtml(item.shelfLife || '')}" placeholder="Npr. 24 meseca" />
                      </div>

                      <div>
                          <label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">${Utils.t('fields.detailedSpec')} & Description</label>
                          <textarea name="detailedSpec" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-3 text-sm font-medium text-slate-800 focus:ring-2 focus:ring-blue-500 outline-none transition-all shadow-sm leading-relaxed" rows="5" placeholder="Detaljan opis, proces proizvodnje, uslovi čuvanja...">${Utils.escapeHtml(item.detailedSpec || '')}</textarea>
                      </div>
                  </div>
              </div>
          </div>
      </div>
      
      <!-- FOOTER SAVE BUTTON -->
      <div class="sticky bottom-0 bg-white p-4 border-t border-slate-200 flex justify-end mt-4 shadow-[0_-4px_6px_-1px_rgba(0,0,0,0.05)] z-10 rounded-b-2xl">
          <button class="bg-white border border-slate-300 text-slate-700 font-bold px-8 py-3 rounded-xl text-sm transition-colors hover:bg-slate-50 mr-4 shadow-sm" type="button" onclick="closeModal()">${tLang('Odustani', 'Cancel')}</button>
          <button class="bg-blue-600 hover:bg-blue-700 text-white px-10 py-3 shadow-xl rounded-xl text-sm font-black uppercase tracking-widest transition-transform transform hover:-translate-y-0.5" type="submit">💾 ${Utils.t('actions.saveChanges') || 'Sačuvaj Proizvod'}</button>
      </div>
    </form>`;
    
    // Uklanjamo p-6 iz modala da bi sticky footer legao lepo
    const mBody = document.getElementById('modal-body');
    if(mBody) { mBody.classList.remove('p-6'); mBody.classList.add('p-0'); }

    Utils.openModal(state.editingItem ? tLang('Uređivanje Proizvoda', 'Edit Product Profile') : tLang('Novi Proizvod u Katalogu', 'Create New Product'), html, async (fd) => {
        const id = state.editingItem?.id || Utils.generateId();
        const prod = { 
             id, 
             name: fd.get('name'), 
             imageUrl: fd.get('imageUrl'),
             category: fd.get('category'), 
             hsCode: fd.get('hsCode'), 
             sku: fd.get('sku'),
             brand: fd.get('brand'),
             shelfLife: fd.get('shelfLife'),
             detailedSpec: fd.get('detailedSpec'), 
             targetPrice: parseFloat(fd.get('targetPrice')) || 0,
             targetCurrency: fd.get('targetCurrency') || 'USD',
             tags: fd.getAll('product_tags'),
             supplyOffers: item.supplyOffers || [], 
             inventory: item.inventory || [],
             coaParams: item.coaParams || [],
             documents: item.documents || [],
             logistics: { cap20: parseFloat(fd.get('cap20')) || null, cap40: parseFloat(fd.get('cap40')) || null },
             lastModified: new Date().toISOString() 
         };
         
         if(state.editingItem) state.data.products[state.data.products.findIndex(p => p.id === id)] = prod; 
         else state.data.products.push(prod);
         
         await saveSingleItem('products', prod); 
         Utils.closeModal(); 
         render(); 
    });

    // Vraćanje paddinga kad se modal zatvori
    const oldClose = window.closeModal;
    window.closeModal = function() {
        if(mBody) { mBody.classList.add('p-6'); mBody.classList.remove('p-0'); }
        oldClose();
        window.closeModal = oldClose; 
    };

    // TAB LOGIC
    document.querySelectorAll('.prod-tab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            document.querySelectorAll('.prod-tab-btn').forEach(b => {
                b.classList.remove('active', 'border-blue-600', 'text-blue-700', 'font-black');
                b.classList.add('border-transparent', 'text-slate-500', 'font-bold');
            });
            const target = e.currentTarget;
            target.classList.add('active', 'border-blue-600', 'text-blue-700', 'font-black');
            target.classList.remove('border-transparent', 'text-slate-500', 'font-bold');
            document.querySelectorAll('.prod-pane').forEach(p => p.classList.add('hidden'));
            document.getElementById(target.dataset.target).classList.remove('hidden');
        });
    });
    
    if(typeof Utils.initAutocomplete === 'function' && typeof COUNTRIES !== 'undefined') Utils.initAutocomplete(document.getElementById('offer-country'), COUNTRIES);
    
    const refreshOffers = () => { document.getElementById('offers-list').innerHTML = renderOffersList(); attachOfferListeners(); };
    const refreshCOA = () => { document.getElementById('coa-list').innerHTML = renderCOAList(); attachCOAListeners(); };
    const refreshInv = () => { document.getElementById('inventory-list').innerHTML = renderInventoryList(); attachInvListeners(); };
    
    const attachCOAListeners = () => {
        document.querySelectorAll('.remove-coa').forEach(b => b.addEventListener('click', e => {
            item.coaParams.splice(parseInt(e.currentTarget.dataset.index, 10), 1); refreshCOA();
        }));
    };
    
    const attachInvListeners = () => {
        document.querySelectorAll('.remove-inv').forEach(b => b.addEventListener('click', e => {
            if(confirm(tLang('Obriši stavku sa lagera?', 'Delete from inventory?'))) { item.inventory.splice(parseInt(e.currentTarget.dataset.index, 10), 1); refreshInv(); }
        }));
    };

    document.getElementById('add-coa-btn').addEventListener('click', () => {
        const name = document.getElementById('coa-name').value.trim(); const val = document.getElementById('coa-value').value.trim();
        if(name && val) { item.coaParams.push({name, value: val}); document.getElementById('coa-name').value=''; document.getElementById('coa-value').value=''; refreshCOA(); }
    });

    document.getElementById('add-inv-btn').addEventListener('click', () => {
        const loc = document.getElementById('inv-loc').value.trim(); const qty = parseFloat(document.getElementById('inv-qty').value); const price = parseFloat(document.getElementById('inv-price').value);
        if(loc && qty > 0) {
            item.inventory.push({ 
                location: loc, batchNo: document.getElementById('inv-batch').value.trim(), 
                qty, purchasePrice: price || 0, currency: document.getElementById('inv-currency').value, 
                expiry: document.getElementById('inv-expiry').value,
                status: document.getElementById('inv-status').value
            });
            ['inv-loc', 'inv-batch', 'inv-qty', 'inv-price', 'inv-expiry'].forEach(id => document.getElementById(id).value = '');
            document.getElementById('inv-status').value = 'available';
            refreshInv();
        } else {
            alert(tLang('Lokacija i Količina su obavezni.', 'Location and Qty are required.'));
        }
    });
    
    const attachOfferListeners = () => {
        document.querySelectorAll('.remove-offer').forEach(b => b.addEventListener('click', (e) => { 
            if(confirm(tLang('Da li ste sigurni?', 'Are you sure?'))) { item.supplyOffers.splice(parseInt(e.currentTarget.dataset.index, 10), 1); refreshOffers(); }
        }));
        document.querySelectorAll('.edit-offer').forEach(b => b.addEventListener('click', (e) => { 
            currentEditOfferIndex = parseInt(e.currentTarget.dataset.index, 10);
            const o = item.supplyOffers[currentEditOfferIndex];
            document.getElementById('offer-supplier').value = o.supplierId || ''; document.getElementById('offer-qty').value = o.quantity || 0; document.getElementById('offer-moq').value = o.moq || ''; document.getElementById('offer-price').value = o.price || 0; document.getElementById('offer-currency').value = o.currency || 'USD'; document.getElementById('offer-unit').value = o.unit || ''; document.getElementById('offer-incoterm').value = o.incoterm || ''; document.getElementById('offer-country').value = o.country || ''; document.getElementById('offer-validUntil').value = o.validUntil || '';
            
            const currentCerts = o.certificates ? o.certificates.split(', ') : [];
            document.querySelectorAll('input[name="offer_cert"]').forEach(cb => { cb.checked = currentCerts.includes(cb.value); });
            document.getElementById('offer-box-title').innerHTML = `✏️ ${tLang('Izmena Ponude', 'Edit Offer')}`; document.getElementById('cancel-offer-edit').classList.remove('hidden');
            document.getElementById('offer-edit-box').className = 'p-6 border border-amber-300 bg-amber-50 rounded-xl shadow-lg space-y-4 transition-all relative';
            document.getElementById('offer-edit-box').scrollIntoView({ behavior: 'smooth' });
        }));
    };
    
    document.getElementById('cancel-offer-edit').addEventListener('click', () => {
        currentEditOfferIndex = -1;
        ['offer-supplier', 'offer-qty', 'offer-moq', 'offer-price', 'offer-country', 'offer-validUntil'].forEach(id => document.getElementById(id).value = '');
        document.querySelectorAll('input[name="offer_cert"]').forEach(cb => cb.checked = false);
        document.getElementById('offer-box-title').innerHTML = `➕ ${tLang('Dodaj Novu Ponudu', 'Add New Offer')}`; document.getElementById('cancel-offer-edit').classList.add('hidden'); 
        document.getElementById('offer-edit-box').className = 'p-6 border border-slate-300 bg-white rounded-xl shadow-lg space-y-4 transition-all relative';
    });

    document.getElementById('add-offer').addEventListener('click', () => {
        const supId = document.getElementById('offer-supplier').value; const qty = parseFloat(document.getElementById('offer-qty').value) || 0; const moq = parseFloat(document.getElementById('offer-moq').value) || null; const price = parseFloat(document.getElementById('offer-price').value) || 0; const currency = document.getElementById('offer-currency').value; const unit = document.getElementById('offer-unit').value; const country = document.getElementById('offer-country').value.trim(); const incoterm = document.getElementById('offer-incoterm').value; const validUntil = document.getElementById('offer-validUntil').value;
        const certificates = Array.from(document.querySelectorAll('input[name="offer_cert"]:checked')).map(cb => cb.value).join(', ');
        
        if(!supId || !price || !country) return alert(tLang('Dobavljač, Cena i Poreklo su obavezni!', 'Supplier, Price, and Country are required!'));
        
        if (currentEditOfferIndex >= 0) {
            const o = item.supplyOffers[currentEditOfferIndex];
            if (o.price !== price || o.incoterm !== incoterm) { o.history = o.history || []; o.history.push({ price: o.price, currency: o.currency, incoterm: o.incoterm, date: new Date().toISOString() }); }
            o.supplierId = supId; o.quantity = qty; o.moq = moq; o.price = price; o.currency = currency; o.unit = unit; o.country = country; o.incoterm = incoterm; o.validUntil = validUntil; o.certificates = certificates;
        } else item.supplyOffers.push({ supplierId: supId, quantity: qty, moq, price, currency, unit, country, incoterm, validUntil, certificates, history: [] });
        
        document.getElementById('cancel-offer-edit').click(); refreshOffers();
    });
    
    attachCOAListeners(); attachInvListeners(); attachOfferListeners();
}