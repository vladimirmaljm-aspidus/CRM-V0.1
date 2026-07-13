// static/js/modules/products/customer_offers.js

function showCustomerOfferModal({productId = null, offerIndex = null, isInventory = false, invIndex = null, savedOfferId = null, prefillCustomerId = null, prefillQuantity = null}) {
    let savedOfferData = {};
    let currentItems = [];

    // Učitavanje postojeće ponude ili kreiranje nove
    if (savedOfferId) {
        savedOfferData = state.data.offers.find(o => o.id === savedOfferId);
        if (!savedOfferData) return;
        
        // Kompatibilnost unazad: Ako stara ponuda ima samo jedan proizvod, pretvaramo ga u niz
        if (savedOfferData.items && savedOfferData.items.length > 0) {
            currentItems = JSON.parse(JSON.stringify(savedOfferData.items));
        } else if (savedOfferData.productId) {
            currentItems.push({
                productId: savedOfferData.productId,
                quantity: savedOfferData.quantity || 1,
                price: savedOfferData.sellingPrice || 0,
                unit: savedOfferData.unit || 'MT',
                isInventory: savedOfferData.isInventory,
                invIndex: savedOfferData.invIndex,
                offerIndex: savedOfferData.offerIndex
            });
        }
    } else {
        const product = state.data.products.find(p => p.id === productId);
        const sourceData = isInventory ? product?.inventory[invIndex] : product?.supplyOffers[offerIndex];
        
        if (!product || !sourceData) {
            alert(Utils.getLang() === 'sr' ? 'Izvorni podaci za ovu ponudu više ne postoje (obrisani su).' : 'Source data for this offer no longer exists (deleted).');
            return;
        }

        currentItems.push({
            productId: product.id,
            quantity: (parseFloat(prefillQuantity) > 0 ? parseFloat(prefillQuantity) : 1),
            price: sourceData.sellingPrice || sourceData.price || sourceData.purchasePrice || 0,
            unit: sourceData.unit || 'MT',
            isInventory: isInventory,
            invIndex: invIndex,
            offerIndex: offerIndex
        });
    }

    let displayOfferNum = savedOfferData.offerNo || (((state.settings.lastOfferNumber || 0) + 1) + '/' + new Date().getFullYear());
    const initialCurrency = savedOfferData.currency || 'USD';
    const isBuyer = (p) => (p.types || []).includes('buyer') || (p.types || []).includes('Kupac');
    const currentLang = Utils.getLang() === 'sr' ? 'sr-RS' : 'en-US';
    const tLang = (srStr, enStr) => Utils.getLang() === 'sr' ? srStr : enStr;
    
    const incotermsList = typeof INCOTERMS !== 'undefined' ? INCOTERMS : ['EXW', 'FCA', 'FAS', 'FOB', 'CFR', 'CIF', 'CPT', 'CIP', 'DAP', 'DPU', 'DDP'];

    const datalists = `
      <datalist id="payment-terms-list">
        <option value="100% Avans (Advance)">
        <option value="30% Avans, 70% pre isporuke">
        <option value="100% Neopozivi L/C po viđenju">
        <option value="CAD (Cash Against Documents)">
        <option value="Net 30 Dana">
        <option value="Net 60 Dana">
      </datalist>
      <datalist id="packaging-list">
        <option value="25kg Multi-wall Kraft Paper Bags">
        <option value="25kg PP Woven Bags">
        <option value="50kg PP Woven Bags">
        <option value="1 MT Jumbo Bags (FIBC)">
        <option value="Bulk in 20ft Container">
        <option value="Bulk in 40ft Container">
        <option value="25kg Cartons with inner PE liner">
        <option value="Flexitanks">
        <option value="Palletized and Shrink-wrapped">
      </datalist>
      <datalist id="tax-clause-list">
        <option value="${tLang('Oslobođeno PDV-a (Izvoz)', 'VAT Exempt (Export)')}">
        <option value="Reverse Charge">
        <option value="${tLang('Uključen PDV', 'VAT Included')}">
      </datalist>
    `;
    
    const html = `${datalists}<div id="offer-container" class="p-6">
      <div id="offer-controls" class="p-6 bg-white border border-slate-300 rounded-2xl mb-6 grid grid-cols-1 md:grid-cols-4 gap-6 items-end text-slate-800 shadow-sm relative">
          
          <div class="col-span-1 md:col-span-4 grid grid-cols-2 md:grid-cols-4 gap-6">
              <div class="col-span-2"><label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">${Utils.t('offer.customer')} <span class="text-red-500">*</span></label><select id="offer-customer-select" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-3 text-sm font-bold text-slate-800 outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer shadow-sm"><option value="">${Utils.t('actions.select_buyer')}</option>${state.data.partners.filter(isBuyer).map(p => `<option value="${p.id}" ${p.id===savedOfferData.customerId?'selected':''}>${Utils.escapeHtml(p.companyName)}</option>`).join('')}</select></div>
              <div><label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">${Utils.t('fields.currency')}</label><select id="offer-currency" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-3 text-sm font-bold text-slate-800 outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer shadow-sm">${(typeof CURRENCIES !== 'undefined' ? CURRENCIES : ['USD', 'EUR', 'AED']).map(c => `<option value="${c}" ${c===initialCurrency?'selected':''}>${c}</option>`).join('')}</select></div>
              <div><label class="block text-[10px] font-black text-red-500 uppercase tracking-widest mb-2">${Utils.t('offer.valid_until')}</label><input type="date" id="offer-valid-until" class="w-full bg-red-50 border border-red-200 text-red-800 rounded-lg px-4 py-3 text-sm font-bold outline-none focus:ring-2 focus:ring-red-500 shadow-sm" value="${savedOfferData.validUntil || ''}" /></div>
          </div>

          <div id="offer-items-wrapper" class="col-span-1 md:col-span-4 bg-slate-50 p-5 rounded-xl border border-slate-200 shadow-inner">
              <div class="flex justify-between items-center mb-4 border-b border-slate-200 pb-3">
                  <h4 class="font-black text-slate-800 text-xs uppercase tracking-widest flex items-center gap-2">🛒 ${tLang('Stavke Ponude', 'Offer Items')}</h4>
                  <button id="add-item-btn" class="bg-white border border-slate-300 hover:bg-slate-100 text-blue-700 font-black px-4 py-1.5 rounded-lg text-[10px] uppercase shadow-sm transition-colors">+ ${tLang('Dodaj Proizvod', 'Add Product')}</button>
              </div>
              <div id="offer-items-list" class="space-y-3"></div>
              <div id="logistics-calc" class="mt-3 text-sm font-bold"></div>
              <div id="substitutes-warning-container"></div>
          </div>
          
          <div class="col-span-1 md:col-span-4 grid grid-cols-2 md:grid-cols-4 gap-6 mt-2 border-t border-slate-200 pt-6">
              <div>
                  <label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">Incoterm (Paritet)</label>
                  <select id="off-incoterm" class="w-full bg-white border border-slate-300 text-slate-800 rounded-lg px-4 py-2 text-sm font-bold outline-none focus:border-blue-500">
                      <option value="">-- ${Utils.t('actions.select') || 'Select'} --</option>
                      ${incotermsList.map(i => `<option value="${i}" ${i === savedOfferData.incoterm ? 'selected' : ''}>${i}</option>`).join('')}
                  </select>
              </div>
              <div class="md:col-span-2">
                  <label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">${tLang('Poreska Klauzula', 'Tax Clause')}</label>
                  <input id="off-tax-clause" list="tax-clause-list" class="w-full bg-white border border-slate-300 text-slate-800 rounded-lg px-4 py-2 text-sm outline-none focus:border-blue-500" value="${Utils.escapeHtml(savedOfferData.taxClause || '')}" placeholder="${tLang('Npr. Oslobođeno PDV-a...', 'e.g. VAT Exempt...')}" />
              </div>
              <div><label class="block text-[10px] font-black text-slate-400 uppercase tracking-widest mb-2">${Utils.t('offer.packaging')}</label><input id="off-packaging" list="packaging-list" class="w-full bg-white border border-slate-300 text-slate-800 rounded-lg px-4 py-2 text-sm outline-none focus:border-blue-500" value="${Utils.escapeHtml(savedOfferData.packaging || '')}" placeholder="${Utils.t('placeholders.pack')}" /></div>
              
              <div class="transport-field"><label class="block text-[10px] font-black text-slate-400 uppercase tracking-widest mb-2">${Utils.t('offer.pol')}</label><input id="off-pol" class="w-full bg-white border border-slate-300 text-slate-800 rounded-lg px-4 py-2 text-sm outline-none focus:border-blue-500" value="${Utils.escapeHtml(savedOfferData.pol || '')}" placeholder="${Utils.t('placeholders.pol')}" /></div>
              <div class="transport-field"><label class="block text-[10px] font-black text-slate-400 uppercase tracking-widest mb-2">${Utils.t('offer.pod')}</label><input id="off-pod" class="w-full bg-white border border-slate-300 text-slate-800 rounded-lg px-4 py-2 text-sm outline-none focus:border-blue-500" value="${Utils.escapeHtml(savedOfferData.pod || '')}" placeholder="${Utils.t('placeholders.pod')}" /></div>
              <div class="transport-field"><label class="block text-[10px] font-black text-slate-400 uppercase tracking-widest mb-2">${tLang('Brod/Kamion', 'Vessel/Truck')}</label><input id="off-vessel" class="w-full bg-white border border-slate-300 text-slate-800 rounded-lg px-4 py-2 text-sm outline-none focus:border-blue-500" value="${Utils.escapeHtml(savedOfferData.vessel || '')}" placeholder="${tLang('Ime broda', 'Vessel name')}" /></div>
              <div class="transport-field"><label class="block text-[10px] font-black text-slate-400 uppercase tracking-widest mb-2">${tLang('Br. Kontejnera', 'Container No.')}</label><input id="off-container" class="w-full bg-white border border-slate-300 text-slate-800 rounded-lg px-4 py-2 text-sm outline-none focus:border-blue-500" value="${Utils.escapeHtml(savedOfferData.containerNo || '')}" placeholder="e.g. HLBU1234567" /></div>
              
              <div><label class="block text-[10px] font-black text-slate-400 uppercase tracking-widest mb-2">${Utils.t('offer.leadTime')}</label><input id="off-lead" class="w-full bg-white border border-slate-300 text-slate-800 rounded-lg px-4 py-2 text-sm outline-none focus:border-blue-500" value="${Utils.escapeHtml(savedOfferData.leadTime || '')}" placeholder="${Utils.t('placeholders.lead')}" /></div>
              <div class="md:col-span-3"><label class="block text-[10px] font-black text-slate-400 uppercase tracking-widest mb-2">${Utils.t('offer.paymentTerms')}</label><input id="off-pay-terms" list="payment-terms-list" class="w-full bg-white border border-slate-300 text-slate-800 rounded-lg px-4 py-2 text-sm font-bold outline-none focus:border-blue-500" value="${Utils.escapeHtml(savedOfferData.paymentTerms || '')}" placeholder="${Utils.t('placeholders.pay')}" /></div>
              <div class="md:col-span-4"><label class="block text-[10px] font-black text-slate-400 uppercase tracking-widest mb-2">${Utils.t('offer.specificNotes')}</label><textarea id="off-notes" class="w-full bg-white border border-slate-300 text-slate-800 rounded-lg px-4 py-3 text-sm outline-none focus:border-blue-500" rows="2">${Utils.escapeHtml(savedOfferData.notes || state.settings.defaultOfferNotes || Utils.t('offer.default_note'))}</textarea></div>
          </div>
          
          <div class="col-span-1 md:col-span-4 mt-2 bg-slate-50 p-5 rounded-xl border border-slate-200">
              <div class="flex justify-between items-center mb-4 border-b border-slate-200 pb-3"><strong class="text-slate-800 uppercase tracking-widest text-xs font-black flex items-center gap-2">➕ ${Utils.t('offer.additional_services')}</strong><button id="add-offer-service-btn" class="bg-white border border-slate-300 hover:bg-slate-100 text-slate-700 font-bold px-4 py-1.5 rounded-lg text-[10px] uppercase shadow-sm transition-colors">+ ${Utils.t('actions.addService')}</button></div>
              <div id="offer-services-list" class="space-y-3">
                  ${(savedOfferData.services || []).map(s => `<div class="flex gap-3 off-svc-item"><input class="w-full bg-white border border-slate-300 text-slate-800 rounded-lg px-3 py-2 text-sm outline-none focus:border-blue-500 svc-name" value="${Utils.escapeHtml(s.name)}"><input type="number" step="0.01" class="w-32 bg-white border border-slate-300 text-slate-800 rounded-lg px-3 py-2 text-sm font-bold outline-none focus:border-blue-500 svc-price" value="${s.price}"><button class="bg-red-50 text-red-600 border border-red-200 hover:bg-red-600 hover:text-white px-3 rounded-lg font-black transition-colors" onclick="this.parentElement.remove(); document.getElementById('offer-currency').dispatchEvent(new Event('change'));">✕</button></div>`).join('')}
              </div>
          </div>

          <div class="col-span-1 md:col-span-4 flex flex-wrap gap-3 justify-end mt-4 pt-4 border-t border-slate-200">
              <button id="save-offer-btn" class="bg-emerald-600 text-white px-6 py-3 rounded-xl shadow-md text-sm font-black uppercase tracking-widest hover:bg-emerald-700 transition-transform transform hover:-translate-y-0.5">💾 ${Utils.t('offer.saveOfferBtn')}</button>
              <button id="print-offer-btn" class="bg-blue-600 text-white px-6 py-3 rounded-xl shadow-md text-sm font-black uppercase tracking-widest hover:bg-blue-700 transition-transform transform hover:-translate-y-0.5">🖨️ ${tLang('Sačuvaj i Preuzmi PDF', 'Save & Download')}</button>
              <button id="email-offer-btn" class="bg-indigo-600 text-white px-6 py-3 rounded-xl shadow-md text-sm font-black uppercase tracking-widest hover:bg-indigo-700 transition-transform transform hover:-translate-y-0.5">📧 ${tLang('Pošalji Mail', 'Send Email')}</button>
          </div>
      </div>
      
      <div id="offer-body" class="p-10 bg-white text-slate-800 shadow-md rounded-2xl border border-slate-200 relative overflow-hidden">
        <header class="flex justify-between items-start mb-10 border-b border-slate-200 pb-6 relative z-10">
            <div><h1 class="text-4xl font-black uppercase text-slate-900 tracking-wider">${Utils.t('offer.firmOffer')}</h1></div>
            <div class="text-sm">
                <table class="text-right">
                    <tr><td class="pr-4 font-bold text-slate-500 uppercase tracking-widest text-[10px]">${Utils.t('offer.offer_no')}:</td><td class="font-black text-xl text-slate-900" id="preview-offer-no">${displayOfferNum}</td></tr>
                    <tr><td class="pr-4 font-bold text-slate-500 uppercase tracking-widest text-[10px]">${Utils.t('invoice.date_of_issue')}:</td><td class="font-bold text-slate-700">${savedOfferData.date ? new Date(savedOfferData.date).toLocaleDateString(currentLang) : new Date().toLocaleDateString(currentLang)}</td></tr>
                    <tr id="offer-valid-row" class="hidden"><td class="pr-4 font-bold text-slate-500 uppercase tracking-widest text-[10px]">${Utils.t('offer.valid_until')}:</td><td id="offer-display-valid" class="font-black text-red-600 bg-red-50 px-2 py-0.5 rounded"></td></tr>
                </table>
            </div>
        </header>
        
        <section class="invoice-parties mb-10">
            <div id="offer-customer-details" class="bg-slate-50 p-6 border border-slate-200 rounded-xl">
                <h4 class="font-black mb-3 text-slate-500 uppercase tracking-widest text-[10px]">${Utils.t('invoice.to')}:</h4>
                <p class="italic text-slate-400 font-bold">${Utils.t('offer.select_customer')}</p>
            </div>
        </section>
        
        <h3 class="text-lg font-black mb-4 text-slate-900 uppercase tracking-widest flex items-center gap-3"><span class="bg-blue-100 text-blue-800 w-6 h-6 flex items-center justify-center rounded-full text-xs font-black">1</span> ${Utils.t('offer.product_specs')}</h3>
        <div id="preview-specs-container"></div>
        
        <h3 class="text-lg font-black mb-4 mt-10 text-slate-900 uppercase tracking-widest flex items-center gap-3"><span class="bg-blue-100 text-blue-800 w-6 h-6 flex items-center justify-center rounded-full text-xs font-black">2</span> Logistics & Payment</h3>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-6 mb-10 text-sm bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
            <div><strong class="block text-[10px] text-slate-400 uppercase tracking-widest mb-1">Incoterm:</strong> <span id="disp-off-incoterm" class="font-black text-slate-900"></span></div>
            <div><strong class="block text-[10px] text-slate-400 uppercase tracking-widest mb-1">${Utils.t('offer.packaging')}:</strong> <span id="disp-off-packaging" class="font-bold text-slate-700"></span></div>
            <div><strong class="block text-[10px] text-slate-400 uppercase tracking-widest mb-1">${Utils.t('offer.leadTime')}:</strong> <span id="disp-off-lead" class="font-bold text-slate-700"></span></div>
            <div><strong class="block text-[10px] text-slate-400 uppercase tracking-widest mb-1">${tLang('Poreska Klauzula', 'Tax Clause')}:</strong> <span id="disp-off-tax-clause" class="font-bold text-slate-700"></span></div>

            <div class="disp-transport"><strong class="block text-[10px] text-slate-400 uppercase tracking-widest mb-1">${Utils.t('offer.pol')}:</strong> <span id="disp-off-pol" class="font-bold text-slate-700"></span></div>
            <div class="disp-transport"><strong class="block text-[10px] text-slate-400 uppercase tracking-widest mb-1">${Utils.t('offer.pod')}:</strong> <span id="disp-off-pod" class="font-bold text-slate-700"></span></div>
            <div class="disp-transport"><strong class="block text-[10px] text-slate-400 uppercase tracking-widest mb-1">${tLang('Brod/Kamion', 'Vessel/Truck')}:</strong> <span id="disp-off-vessel" class="font-bold text-slate-700"></span></div>
            <div class="disp-transport"><strong class="block text-[10px] text-slate-400 uppercase tracking-widest mb-1">${tLang('Kontejner', 'Container')}:</strong> <span id="disp-off-container" class="font-bold text-slate-700"></span></div>
            
            <div class="col-span-2 md:col-span-4 mt-2 border-t border-slate-100 pt-4 flex flex-col"><strong class="block text-[10px] text-slate-400 uppercase tracking-widest mb-1">${Utils.t('offer.paymentTerms')}:</strong> <span id="disp-off-pay-terms" class="text-slate-900 font-black text-lg"></span></div>
        </div>
        
        <h3 class="text-lg font-black mb-4 mt-10 text-slate-900 uppercase tracking-widest flex items-center gap-3"><span class="bg-blue-100 text-blue-800 w-6 h-6 flex items-center justify-center rounded-full text-xs font-black">3</span> Financials</h3>
        <table class="w-full text-left mb-8 border-collapse shadow-sm rounded-xl overflow-hidden border border-slate-200">
          <thead>
              <tr class="bg-slate-100 text-slate-700">
                  <th class="p-4 uppercase text-[10px] font-black tracking-widest">${Utils.t('invoice.description')}</th>
                  <th class="p-4 uppercase text-[10px] font-black tracking-widest text-right">${Utils.t('invoice.quantity')}</th>
                  <th class="p-4 uppercase text-[10px] font-black tracking-widest text-right">${Utils.t('invoice.unit_price')}</th>
                  <th class="p-4 uppercase text-[10px] font-black tracking-widest text-right">${Utils.t('invoice.total')}</th>
              </tr>
          </thead>
          <tbody id="offer-table-body" class="bg-white"></tbody>
        </table>
        
        <section class="flex justify-end bg-slate-50 p-6 rounded-xl border border-slate-200">
          <table class="w-full md:w-1/2">
              <tr class="font-black text-3xl text-slate-900">
                  <td class="p-2 pt-2 uppercase tracking-widest text-sm text-slate-500 border-t border-slate-300">${Utils.t('invoice.grand_total')}:</td>
                  <td class="text-right p-2 pt-2 border-t border-slate-300" id="offer-grand-total"></td>
              </tr>
          </table>
        </section>
        
        <div class="mt-12 bg-amber-50 p-6 rounded-xl border border-amber-200"><h4 class="font-black text-amber-800 uppercase tracking-widest text-[10px] mb-3 flex items-center gap-2">⚠️ ${Utils.t('invoice.remarks')}</h4><p id="disp-off-notes" class="text-sm font-bold text-amber-900 whitespace-pre-wrap leading-relaxed"></p></div>
      </div>
    </div>`;
    
    Utils.openModal(Utils.t('offer.generate'), html, null);
    
    const mBody = document.getElementById('modal-body');
    if(mBody) { mBody.classList.remove('p-6'); mBody.classList.add('p-0', 'bg-slate-50'); }
    
    const oldClose = window.closeModal;
    window.closeModal = function() {
        if(mBody) { mBody.classList.add('p-6'); mBody.classList.remove('p-0', 'bg-slate-50'); }
        oldClose();
        window.closeModal = oldClose; 
    };

    const curInp = document.getElementById('offer-currency');
    const custSel = document.getElementById('offer-customer-select'); 

    // RENDEROVANJE STAVKI (ITEMS)
    function renderItemsList() {
        const list = document.getElementById('offer-items-list');
        const curr = curInp.value;
        
        list.innerHTML = currentItems.map((item, idx) => {
            const p = state.data.products.find(x => x.id === item.productId);
            const source = item.isInventory ? p?.inventory[item.invIndex] : p?.supplyOffers[item.offerIndex];
            const baseCost = source ? (source.price || source.purchasePrice || 0) : 0;
            
            // Kalkulacija marže
            let marginHtml = '';
            if (baseCost > 0) {
                const marginAbs = item.price - baseCost;
                const marginPct = ((item.price - baseCost) / baseCost) * 100;
                const badgeClass = marginPct > 0 ? 'bg-emerald-500 text-white' : (marginPct < 0 ? 'bg-red-500 text-white animate-pulse' : 'bg-slate-500 text-white');
                const sign = marginPct > 0 ? '+' : '';
                marginHtml = `<div class="mt-2 w-full text-right"><span class="text-[10px] font-black uppercase text-slate-400 tracking-widest mr-2">${tLang('Profit', 'Margin')}:</span><span class="px-2 py-0.5 rounded-md text-[10px] font-black shadow-sm ${badgeClass}">${sign}${marginPct.toFixed(2)}% | ${sign}${Utils.formatCurrency(marginAbs, curr)}/${item.unit}</span></div>`;
            }

            return `
            <div class="flex flex-wrap md:flex-nowrap gap-3 items-end p-4 bg-white border border-slate-200 rounded-xl shadow-sm relative transition-all hover:border-blue-300">
                <div class="w-full md:w-5/12">
                    <label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-1">${tLang('Proizvod', 'Product')}</label>
                    <select class="item-sel w-full bg-slate-50 border border-slate-300 rounded-md px-3 py-2 text-sm font-bold" data-idx="${idx}">
                        ${state.data.products.map(pr => `<option value="${pr.id}" ${pr.id === item.productId ? 'selected' : ''}>${pr.name}</option>`).join('')}
                    </select>
                </div>
                <div class="w-1/3 md:w-2/12">
                    <label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-1">${Utils.t('fields.quantity')}</label>
                    <input type="number" step="0.01" class="item-qty w-full bg-white border border-slate-300 rounded-md px-3 py-2 text-sm font-black text-slate-800" data-idx="${idx}" value="${item.quantity}">
                </div>
                <div class="w-1/3 md:w-3/12">
                    <label class="block text-[10px] font-black text-blue-600 uppercase tracking-widest mb-1">${Utils.t('offer.your_price')}</label>
                    <input type="number" step="0.01" class="item-price w-full bg-blue-50 border border-blue-300 text-blue-800 rounded-md px-3 py-2 text-sm font-black" data-idx="${idx}" value="${item.price}">
                </div>
                <div class="w-1/4 md:w-1/12">
                    <label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-1">${Utils.t('fields.unit')}</label>
                    <select class="item-unit w-full bg-slate-50 border border-slate-300 rounded-md px-2 py-2 text-xs font-bold" data-idx="${idx}">
                        ${(typeof UNITS !== 'undefined' ? UNITS : ['MT']).map(u => `<option value="${u}" ${u===item.unit?'selected':''}>${u}</option>`).join('')}
                    </select>
                </div>
                <div class="w-full md:w-1/12 text-right">
                    <button type="button" class="item-del bg-red-50 hover:bg-red-100 text-red-600 px-3 py-2 rounded-md font-black border border-red-200 transition-colors w-full" data-idx="${idx}">✕</button>
                </div>
                ${marginHtml}
            </div>`;
        }).join('');

        // Event listeneri za stavke
        document.querySelectorAll('.item-sel').forEach(el => el.addEventListener('change', e => {
            const idx = e.target.dataset.idx;
            const newProdId = e.target.value;
            const np = state.data.products.find(x => x.id === newProdId);
            if(np) {
                const off = np.supplyOffers && np.supplyOffers.length > 0 ? np.supplyOffers[0] : {};
                currentItems[idx].productId = newProdId;
                currentItems[idx].price = off.price || 0;
                currentItems[idx].unit = off.unit || 'MT';
                currentItems[idx].isInventory = false;
                currentItems[idx].offerIndex = 0;
            }
            renderItemsList();
            updateDetails();
        }));
        document.querySelectorAll('.item-qty').forEach(el => el.addEventListener('input', e => { currentItems[e.target.dataset.idx].quantity = parseFloat(e.target.value) || 0; updateDetails(); }));
        document.querySelectorAll('.item-price').forEach(el => el.addEventListener('input', e => { currentItems[e.target.dataset.idx].price = parseFloat(e.target.value) || 0; renderItemsList(); updateDetails(); }));
        document.querySelectorAll('.item-unit').forEach(el => el.addEventListener('change', e => { currentItems[e.target.dataset.idx].unit = e.target.value; updateDetails(); }));
        document.querySelectorAll('.item-del').forEach(el => el.addEventListener('click', e => { currentItems.splice(e.target.dataset.idx, 1); renderItemsList(); updateDetails(); }));
    }

    document.getElementById('add-item-btn').addEventListener('click', (e) => {
        e.preventDefault();
        const firstProd = state.data.products[0];
        currentItems.push({
            productId: firstProd ? firstProd.id : '',
            quantity: 1, price: 0, unit: 'MT', isInventory: false, offerIndex: 0
        });
        renderItemsList();
        updateDetails();
    });

    const toggleTransportFields = () => {
        const incoterm = document.getElementById('off-incoterm').value.toUpperCase();
        const isExw = ['EXW', 'FCA'].includes(incoterm);
        
        document.querySelectorAll('.transport-field').forEach(el => {
            el.style.display = isExw ? 'none' : 'block';
            if (isExw) {
                const input = el.querySelector('input');
                if(input) input.value = '';
            }
        });
        document.querySelectorAll('.disp-transport').forEach(el => {
            el.style.display = isExw ? 'none' : 'block';
        });
    };

    const updateDetails = () => {
        const cust = state.data.partners.find(p => p.id === custSel.value);
        if (cust) {
            document.getElementById('offer-customer-details').innerHTML = `<p class="text-xl font-black text-slate-900 uppercase tracking-wide"><strong>${Utils.escapeHtml(cust.companyName)}</strong></p><p class="text-slate-600 font-medium mt-2">${Utils.escapeHtml(cust.address?.street || '')}</p><p class="text-slate-600 font-medium">${Utils.escapeHtml(cust.address?.city || '')}, ${Utils.escapeHtml(cust.address?.zip || '')}</p><p class="text-slate-900 font-black mt-1">${Utils.escapeHtml(cust.address?.country || '')}</p>`;
        }
        
        toggleTransportFields();

        const curr = curInp.value; 
        let baseTotal = 0;
        let servicesTotal = 0;
        
        // Zbirni prikaz logistike i supstituta
        let total20ft = 0, total40ft = 0;
        let subWarnHtml = '';
        let specsHtml = '';

        const tBody = document.getElementById('offer-table-body');
        tBody.innerHTML = '';

        currentItems.forEach(item => {
            const p = state.data.products.find(x => x.id === item.productId);
            if(!p) return;
            
            const itemTotal = item.price * item.quantity;
            baseTotal += itemTotal;
            
            // Logistika
            if (p.logistics?.cap20) total20ft += (item.quantity / p.logistics.cap20);
            if (p.logistics?.cap40) total40ft += (item.quantity / p.logistics.cap40);
            
            // Supstituti
            const source = item.isInventory ? p.inventory[item.invIndex] : p.supplyOffers[item.offerIndex];
            if (source && item.quantity > (source.qty || source.quantity || 0)) {
                const subs = state.data.products.filter(sub => sub.id !== p.id && sub.category === p.category);
                if (subs.length > 0) {
                    subWarnHtml += `<div class="mt-3 p-4 bg-orange-50 border border-orange-200 rounded-lg text-sm text-orange-800 shadow-sm"><strong class="uppercase text-xs flex items-center gap-2 mb-1">⚠️ ${tLang('Manjak na stanju:', 'Low Stock:')} ${p.name}</strong><ul class="list-disc pl-6 font-medium">${subs.map(s => `<li>Alt: ${Utils.escapeHtml(s.name)}</li>`).join('')}</ul></div>`;
                }
            }

            // Tabela
            tBody.insertAdjacentHTML('beforeend', `<tr class="border-b border-slate-200 hover:bg-slate-50"><td class="p-4"><strong class="text-slate-900 text-lg">${Utils.escapeHtml(p.name)}</strong></td><td class="p-4 text-right font-black text-slate-800">${item.quantity} ${item.unit}</td><td class="p-4 text-right font-black text-slate-800">${Utils.formatCurrency(item.price, curr)}</td><td class="p-4 text-right font-black text-slate-900 text-xl">${Utils.formatCurrency(itemTotal, curr)}</td></tr>`);

            // Specs Preview
            specsHtml += `
            <div class="mb-6">
                <div class="grid grid-cols-2 md:grid-cols-4 gap-6 text-sm bg-slate-50 p-6 rounded-t-xl border border-slate-200">
                    <div class="col-span-2"><strong class="block text-[10px] text-slate-400 uppercase tracking-widest mb-1">${Utils.t('fields.productName')}:</strong> <span class="text-lg font-black text-slate-900">${Utils.escapeHtml(p.name)}</span></div>
                    <div><strong class="block text-[10px] text-slate-400 uppercase tracking-widest mb-1">${Utils.t('fields.hsCode')}:</strong> <span class="font-bold text-slate-700 font-mono">${Utils.escapeHtml(p.hsCode || 'N/A')}</span></div>
                    <div><strong class="block text-[10px] text-slate-400 uppercase tracking-widest mb-1">${Utils.t('fields.origin')}:</strong> <span class="font-bold text-slate-700">${Utils.escapeHtml(source?.country || source?.location || 'N/A')}</span></div>
                </div>
                ${p.coaParams && p.coaParams.length > 0 ? `<div class="text-sm bg-blue-50/50 p-4 border-x border-slate-200"><div class="grid grid-cols-2 gap-3">${p.coaParams.map(c => `<div><span class="font-black text-slate-500 uppercase text-[10px] tracking-widest">${Utils.escapeHtml(c.name)}:</span> <span class="font-bold text-slate-900 ml-2">${Utils.escapeHtml(c.value)}</span></div>`).join('')}</div></div>` : ''}
                ${p.detailedSpec ? `<div class="text-sm bg-white p-4 border-x border-b border-slate-200 rounded-b-xl"><p class="whitespace-pre-wrap font-medium text-slate-700 leading-relaxed text-xs">${Utils.escapeHtml(p.detailedSpec)}</p></div>` : '<div class="border-b border-slate-200 rounded-b-xl"></div>'}
            </div>`;
        });

        document.getElementById('preview-specs-container').innerHTML = specsHtml;
        
        let logMsg = '';
        if (total20ft > 0 || total40ft > 0) {
            logMsg = `📦 <span class="uppercase tracking-widest text-[10px] text-slate-400 mr-2">${tLang('Ukupan kontejnerski zahtev:', 'Total Container Load:')}</span> `;
            if(total20ft > 0) logMsg += `<span class="${!Number.isInteger(total20ft) ? 'text-orange-700 bg-orange-100' : 'text-emerald-700 bg-emerald-100'} px-2 py-0.5 rounded font-black border border-transparent shadow-sm">${total20ft.toFixed(2)} x 20'ft</span> `;
            if(total40ft > 0) logMsg += ` <span class="${!Number.isInteger(total40ft) ? 'text-orange-700 bg-orange-100' : 'text-emerald-700 bg-emerald-100'} px-2 py-0.5 rounded font-black border border-transparent shadow-sm">${total40ft.toFixed(2)} x 40'ft</span>`;
        }
        document.getElementById('logistics-calc').innerHTML = logMsg;
        document.getElementById('substitutes-warning-container').innerHTML = subWarnHtml;

        document.getElementById('disp-off-incoterm').innerText = document.getElementById('off-incoterm').value || 'TBA';
        document.getElementById('disp-off-tax-clause').innerText = document.getElementById('off-tax-clause').value || 'N/A';
        document.getElementById('disp-off-packaging').innerText = document.getElementById('off-packaging').value || 'N/A';
        document.getElementById('disp-off-pol').innerText = document.getElementById('off-pol').value || 'N/A';
        document.getElementById('disp-off-pod').innerText = document.getElementById('off-pod').value || 'N/A';
        document.getElementById('disp-off-vessel').innerText = document.getElementById('off-vessel').value || 'N/A';
        document.getElementById('disp-off-container').innerText = document.getElementById('off-container').value || 'N/A';
        document.getElementById('disp-off-lead').innerText = document.getElementById('off-lead').value || 'N/A';
        document.getElementById('disp-off-pay-terms').innerText = document.getElementById('off-pay-terms').value || 'TBA';
        document.getElementById('disp-off-notes').innerHTML = Utils.escapeHtml(document.getElementById('off-notes').value).replace(/\n/g, '<br>');
        
        document.querySelectorAll('.off-svc-item').forEach(el => {
           const sName = el.querySelector('.svc-name').value; const sPrice = parseFloat(el.querySelector('.svc-price').value) || 0;
           if(sName && sPrice > 0) {
                servicesTotal += sPrice;
                tBody.insertAdjacentHTML('beforeend', `<tr class="border-b border-slate-100 bg-slate-50"><td class="p-4 font-bold text-slate-700">${Utils.escapeHtml(sName)}</td><td class="p-4 text-right font-bold text-slate-700">1</td><td class="p-4 text-right font-bold text-slate-700">${Utils.formatCurrency(sPrice, curr)}</td><td class="p-4 text-right font-black text-slate-900">${Utils.formatCurrency(sPrice, curr)}</td></tr>`);
            }
        });
        
        document.getElementById('offer-grand-total').innerText = Utils.formatCurrency(baseTotal + servicesTotal, curr);
        
        const validInp = document.getElementById('offer-valid-until');
        if(validInp.value) {
            document.getElementById('offer-valid-row').classList.remove('hidden');
            document.getElementById('offer-display-valid').innerText = new Date(validInp.value).toLocaleDateString(currentLang);
        } else {
            document.getElementById('offer-valid-row').classList.add('hidden');
        }
    };

    // ZAŠTIĆENO ČUVANJE (Preuzima i fiksira broj)
    async function saveOfferToDB() {
        const custId = custSel.value;
        if (!custId) { alert(Utils.t('misc.selectCustomerAlert')); return false; }
        if (currentItems.length === 0) { alert(tLang('Dodajte barem jedan proizvod.', 'Add at least one product.')); return false; }
        
        if (!savedOfferId) {
            state.settings.lastOfferNumber = (state.settings.lastOfferNumber || 0) + 1;
            displayOfferNum = state.settings.lastOfferNumber + '/' + new Date().getFullYear();
            await saveToStorage('settings');
            document.getElementById('preview-offer-no').innerText = displayOfferNum;
        }

        const svcs = [];
        document.querySelectorAll('.off-svc-item').forEach(el => {
            const sName = el.querySelector('.svc-name').value; const sPrice = parseFloat(el.querySelector('.svc-price').value) || 0;
            if(sName && sPrice > 0) svcs.push({name: sName, price: sPrice});
        });
        
        const offerObj = {
            id: savedOfferId || Utils.generateId(),
            offerNo: displayOfferNum,
            date: savedOfferData.date || new Date().toISOString(),
            validUntil: document.getElementById('offer-valid-until').value,
            customerId: custId,
            items: currentItems, 
            productId: currentItems[0].productId, // Zadržano zbog kompatibilnosti
            quantity: currentItems[0].quantity,
            sellingPrice: currentItems[0].price,
            unit: currentItems[0].unit,
            currency: curInp.value,
            incoterm: document.getElementById('off-incoterm').value,
            taxClause: document.getElementById('off-tax-clause').value,
            packaging: document.getElementById('off-packaging').value,
            pol: document.getElementById('off-pol').value,
            pod: document.getElementById('off-pod').value,
            vessel: document.getElementById('off-vessel').value,
            containerNo: document.getElementById('off-container').value,
            leadTime: document.getElementById('off-lead').value,
            paymentTerms: document.getElementById('off-pay-terms').value,
            notes: document.getElementById('off-notes').value,
            services: svcs,
            ownerId: savedOfferData.ownerId || state.user?.id || 'SYSTEM',
            sharedWith: savedOfferData.sharedWith || []
        };
        
        if (!state.data.offers) state.data.offers = [];
        if (savedOfferId) {
            state.data.offers[state.data.offers.findIndex(o => o.id === savedOfferId)] = offerObj;
        } else {
            state.data.offers.push(offerObj);
            savedOfferId = offerObj.id; // Fiksiramo ID
            savedOfferData = offerObj;
        }
        await saveSingleItem('offers', offerObj);

        // Automatski generiši PDF na serveru i sačuvaj u vault (klijent u portalu
        // odmah može da preuzme). Radi se u pozadini — greška ne blokira save.
        try {
            fetch(`/api/offers/${offerObj.id}/generate_pdf`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            }).then(r => r.ok ? r.json() : null).then(d => {
                if (d && d.documentId) {
                    offerObj.documentId = d.documentId;
                    // Osveži tabelu ako je vidljiva
                    if (typeof renderOffersView === 'function' && document.getElementById('offers-container-marker')) renderOffersView();
                }
            }).catch(() => {});
        } catch (e) { /* silent */ }

        if (typeof renderOffersView === 'function' && document.getElementById('offers-container-marker')) renderOffersView();
        return true;
    }

    // GENERISANJE PDF PODATAKA
    function buildPdfData() {
        const curr = curInp.value;
        let baseTotal = 0;
        let servicesTotal = 0;
        
        const pdfItems = currentItems.map(item => {
            const p = state.data.products.find(x => x.id === item.productId);
            const total = item.price * item.quantity;
            baseTotal += total;
            return { 
                desc: p ? p.name : 'Unknown', 
                hsCode: p ? (p.hsCode || '') : '',
                qty: item.quantity, 
                unit: item.unit, 
                price: item.price, 
                total: total 
            };
        });

        document.querySelectorAll('.off-svc-item').forEach(el => {
            const sName = el.querySelector('.svc-name').value; 
            const sPrice = parseFloat(el.querySelector('.svc-price').value) || 0;
            if(sName && sPrice > 0) {
                servicesTotal += sPrice;
                pdfItems.push({desc: sName, hsCode: '', qty: 1, unit: 'srv', price: sPrice, total: sPrice});
            }
        });
        
        let specCombined = '';
        currentItems.forEach(item => {
            const p = state.data.products.find(x => x.id === item.productId);
            if(p) {
                specCombined += `\n--- ${p.name} ---\n`;
                if(p.detailedSpec) specCombined += p.detailedSpec + '\n';
                if(p.coaParams && p.coaParams.length > 0) specCombined += 'COA:\n' + p.coaParams.map(c => `- ${c.name}: ${c.value}`).join('\n') + '\n';
            }
        });
        
        return {
            type: 'offer',
            documentNo: displayOfferNum,
            date: savedOfferData.date || new Date().toISOString(),
            validUntil: document.getElementById('offer-valid-until').value,
            customer: state.data.partners.find(p => p.id === custSel.value),
            productName: currentItems.length > 1 ? 'Multiple Commodities' : (state.data.products.find(x => x.id === currentItems[0].productId)?.name || ''),
            hsCode: 'N/A', // Ostavljeno kao fallback, koristi se unutar items za svaki posebno
            detailedSpec: specCombined.trim(),
            currency: curr,
            logistics: {
                origin: 'Various',
                incoterm: document.getElementById('off-incoterm').value || 'TBA',
                pol: document.getElementById('off-pol').value || 'N/A',
                pod: document.getElementById('off-pod').value || 'N/A',
                vessel: document.getElementById('off-vessel').value || 'N/A',
                containerNo: document.getElementById('off-container').value || 'N/A',
                packaging: document.getElementById('off-packaging').value || 'N/A',
                leadTime: document.getElementById('off-lead').value || 'N/A',
                paymentTerms: document.getElementById('off-pay-terms').value || 'N/A'
            },
            taxClause: document.getElementById('off-tax-clause').value || '',
            items: pdfItems,
            subtotal: baseTotal + servicesTotal,
            vat: 0,
            grandTotal: baseTotal + servicesTotal,
            bankDetails: '', 
            notes: document.getElementById('off-notes').value
        };
    }

    document.getElementById('off-incoterm').addEventListener('change', updateDetails);
    custSel.addEventListener('change', updateDetails); 
    curInp.addEventListener('change', updateDetails); 
    document.getElementById('offer-valid-until').addEventListener('change', updateDetails);
    document.querySelectorAll('#off-packaging, #off-tax-clause, #off-pol, #off-pod, #off-vessel, #off-container, #off-lead, #off-pay-terms, #off-notes').forEach(el => el.addEventListener('input', updateDetails));
    
    document.getElementById('add-offer-service-btn').addEventListener('click', (e) => {
       e.preventDefault();
       const id = Date.now();
       document.getElementById('offer-services-list').insertAdjacentHTML('beforeend', `<div class="flex gap-3 off-svc-item" id="off-svc-${id}"><input class="w-full bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm text-slate-800 outline-none focus:border-blue-500 svc-name" placeholder="${Utils.t('offer.service_name')}"><input type="number" step="0.01" class="w-32 bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm font-bold text-slate-800 outline-none focus:border-blue-500 svc-price" placeholder="${Utils.t('fields.price')}"><button class="bg-red-50 text-red-600 border border-red-200 hover:bg-red-600 hover:text-white px-3 rounded-lg font-black transition-colors" type="button" onclick="this.parentElement.remove(); document.getElementById('offer-currency').dispatchEvent(new Event('change'));">✕</button></div>`);
       document.querySelectorAll('.svc-name, .svc-price').forEach(el => el.addEventListener('input', updateDetails));
    });
    
    document.getElementById('save-offer-btn').addEventListener('click', async (e) => {
        e.preventDefault();
        const success = await saveOfferToDB();
        if (success) alert(tLang('Ponuda je uspešno sačuvana.', 'Offer saved successfully.'));
    });
    
    document.getElementById('print-offer-btn').addEventListener('click', async (e) => {
        e.preventDefault();
        const btn = document.getElementById('print-offer-btn');
        const originalText = btn.innerHTML;
        btn.innerHTML = `⏳ ${tLang('GENERIŠEM PDF...', 'GENERATING PDF...')}`;
        btn.disabled = true;
        
        // UVIJEK SAČUVAJ PRIJE ŠTAMPE
        const saved = await saveOfferToDB();
        if (!saved) { btn.innerHTML = originalText; btn.disabled = false; return; }
        
        const filename = `Offer_${displayOfferNum.replace(/\//g,'_')}.pdf`;
        if(typeof generateNativePDF === 'function') {
            await generateNativePDF(buildPdfData(), filename, 'download');
        } else alert("PDF module not loaded.");
        
        btn.innerHTML = originalText;
        btn.disabled = false;
    });

    document.getElementById('email-offer-btn').addEventListener('click', async (e) => {
        e.preventDefault();
        const btn = document.getElementById('email-offer-btn');
        const originalText = btn.innerHTML;
        btn.innerHTML = `⏳ ${tLang('PRIPREMA...', 'PREPARING...')}`;
        btn.disabled = true;

        // UVIJEK SAČUVAJ PRIJE SLANJA E-MAILA
        const saved = await saveOfferToDB();
        if (!saved) { btn.innerHTML = originalText; btn.disabled = false; return; }

        const filename = `Offer_${displayOfferNum.replace(/\//g,'_')}.pdf`;
        if(typeof generateNativePDF === 'function') {
            // Slanje komande 'send' pokreće Comms.showSendModal iz tvog pdf_generator.js fajla
            await generateNativePDF(buildPdfData(), filename, 'send'); 
        } else alert("PDF module not loaded.");

        btn.innerHTML = originalText;
        btn.disabled = false;
    });
    
    renderItemsList();
    updateDetails();

    // AUTOMATIZACIJA: ako je modal otvoren iz potražnje (RFQ), pred-popuni kupca.
    if (prefillCustomerId && custSel) {
        const hasOption = Array.from(custSel.options).some(o => o.value === prefillCustomerId);
        if (hasOption) {
            custSel.value = prefillCustomerId;
            custSel.dispatchEvent(new Event('change'));
        }
    }
}

function renderOffersView() {
    const main = document.getElementById('main-content'); 
    if(!main) return;
    main.innerHTML = '';
    const header = Utils.createViewHeader(Utils.t('misc.savedOffersTitle') || 'Sačuvane Ponude', '', null); 
    if (header.querySelector('button')) header.querySelector('button').remove(); 
    main.appendChild(header);
    
    if (!state.data.offers) state.data.offers = [];
    const currentLang = Utils.getLang() === 'sr' ? 'sr-RS' : 'en-US';
    const container = document.createElement('div'); 
    container.id = 'offers-container-marker';
    container.className = 'bg-white rounded-2xl shadow-xl border border-slate-200 overflow-x-auto mt-6 mb-10';
    
    container.innerHTML = `
    <table class="w-full text-left border-collapse">
        <thead>
            <tr class="border-b border-slate-200 bg-slate-50 text-slate-500">
              <th class="p-5 uppercase text-[10px] font-black tracking-widest">${Utils.t('misc.offerNoTable') || 'Broj Ponude'}</th>
              <th class="p-5 uppercase text-[10px] font-black tracking-widest">${Utils.t('misc.offerDateTable') || 'Datum'}</th>
              <th class="p-5 uppercase text-[10px] font-black tracking-widest">${Utils.t('misc.offerCustomerTable') || 'Kupac'}</th>
              <th class="p-5 uppercase text-[10px] font-black tracking-widest">${Utils.t('misc.offerProductTable') || 'Proizvod'}</th>
              <th class="p-5 uppercase text-[10px] font-black tracking-widest">${Utils.t('misc.offerValueTable') || 'Vrednost'}</th>
              <th class="p-5 text-right uppercase text-[10px] font-black tracking-widest">${Utils.t('misc.offerActionsTable') || 'Akcije'}</th>
            </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
            ${state.data.offers.map(offer => {
                let prodName = '';
                let totalVal = 0;
                
                if (offer.items && offer.items.length > 0) {
                    prodName = offer.items.length > 1 ? `Multiple (${offer.items.length})` : Utils.getProductNameById(offer.items[0].productId);
                    totalVal = offer.items.reduce((s, i) => s + (i.price * i.quantity), 0) + (offer.services || []).reduce((s, c) => s + c.price, 0);
                } else {
                    prodName = Utils.getProductNameById(offer.productId);
                    totalVal = (offer.sellingPrice * offer.quantity) + (offer.services || []).reduce((s, c) => s + c.price, 0);
                }

                const custName = Utils.getPartnerNameById(offer.customerId);
                const canConvert = (typeof hasPerm === 'function') ? hasPerm('offers_to_deal', 'view') || (state.user && state.user.permissions && state.user.permissions.offers_to_deal) : true;
                const canForce = (typeof hasPerm === 'function') ? (state.user && (state.user.role === 'admin' || (state.user.permissions && state.user.permissions.offers_to_deal_force))) : true;
                const clientAccepted = offer.clientStatus === 'accepted';
                const converted = !!offer.convertedDealId;

                let statusBadge = '';
                if (converted) statusBadge = `<span class="text-[10px] px-2 py-0.5 rounded-full bg-blue-100 text-blue-800 border border-blue-200 font-semibold ml-2">→ DEAL</span>`;
                else if (clientAccepted) statusBadge = `<span class="text-[10px] px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800 border border-emerald-200 font-semibold ml-2">✓ ACCEPTED</span>`;
                else if (offer.clientStatus === 'declined') statusBadge = `<span class="text-[10px] px-2 py-0.5 rounded-full bg-red-100 text-red-800 border border-red-200 font-semibold ml-2">✕ DECLINED</span>`;

                let convertBtn = '';
                if (!converted && (state.user?.role === 'admin' || (state.user?.permissions && state.user.permissions.offers_to_deal))) {
                    if (clientAccepted) {
                        convertBtn = `<button class="bg-emerald-600 hover:bg-emerald-700 text-white font-bold px-4 py-2 rounded-lg text-xs shadow-sm transition-all mr-2" data-convert-offer="${offer.id}" data-force="false">➜ ${Utils.t('offer.createDeal') || 'Kreiraj dil'}</button>`;
                    } else if (canForce) {
                        convertBtn = `<button class="bg-amber-500 hover:bg-amber-600 text-white font-bold px-4 py-2 rounded-lg text-xs shadow-sm transition-all mr-2" data-convert-offer="${offer.id}" data-force="true">➜ ${Utils.t('offer.createDealForce') || 'Kreiraj dil (bez portala)'}</button>`;
                    }
                }

                return `
                <tr class="hover:bg-slate-50 transition-colors">
                  <td class="p-5 font-black text-slate-900">${Utils.escapeHtml(offer.offerNo)}${statusBadge}</td>
                  <td class="p-5 text-slate-500 font-bold">${new Date(offer.date).toLocaleDateString(currentLang)}</td>
                  <td class="p-5 font-black text-blue-700">${Utils.escapeHtml(custName)}</td>
                  <td class="p-5 font-bold text-slate-600">${Utils.escapeHtml(prodName)}</td>
                  <td class="p-5 font-black text-emerald-600 text-lg">${Utils.formatCurrency(totalVal, offer.currency)}</td>
                  <td class="p-5 text-right whitespace-nowrap">
                      ${convertBtn}
                      <button class="bg-white hover:bg-slate-100 border border-slate-300 text-slate-800 font-bold px-4 py-2 rounded-lg text-xs shadow-sm transition-all mr-2" onclick="document.dispatchEvent(new CustomEvent('createCustomerOffer', {detail: {savedOfferId: '${offer.id}'}}))">Otvori / Uredi</button>
                      <button class="bg-red-50 hover:bg-red-100 text-red-600 border border-red-100 font-bold px-4 py-2 rounded-lg text-xs shadow-sm transition-colors" onclick="if(confirm('${Utils.t('misc.confirmDelete') || 'Obrisati?'}')){ deleteItemFromServer('offers', '${offer.id}').then(() => { state.data.offers = state.data.offers.filter(o=>o.id!=='${offer.id}'); renderOffersView(); }) }">🗑️</button>
                  </td>
                </tr>`;
            }).join('') || `<tr><td colspan="6" class="p-10 text-center text-slate-400 font-bold border-dashed border-2 border-slate-200 text-sm">${Utils.t('misc.noOffersStored') || 'Nema sačuvanih ponuda.'}</td></tr>`}
        </tbody>
    </table>`;
    main.appendChild(container);

    // Konverzija ponude u dil (poziva backend)
    container.querySelectorAll('[data-convert-offer]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const offerId = e.currentTarget.dataset.convertOffer;
            const force = e.currentTarget.dataset.force === 'true';
            const srLang = Utils.getLang() === 'sr';
            const msg = force
                ? (srLang ? 'PAŽNJA: Klijent nije potvrdio ovu ponudu preko portala.\nDa li ste sigurni da želite da napravite dil bez njegove potvrde?' : 'WARNING: Client has not confirmed via portal.\nAre you sure you want to create the deal without client confirmation?')
                : (srLang ? 'Kreirati dil iz ove ponude?' : 'Create deal from this offer?');
            if (!confirm(msg)) return;
            e.currentTarget.disabled = true;
            try {
                const res = await fetch(`/api/deals/from_offer/${offerId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ force })
                });
                const data = await res.json();
                if (res.ok) {
                    alert((srLang ? 'Dil kreiran uspešno. ID: ' : 'Deal created successfully. ID: ') + data.dealId);
                    if (typeof loadFromStorage === 'function') await loadFromStorage();
                    renderOffersView();
                } else if (data.error === 'CLIENT_HAS_NOT_ACCEPTED') {
                    alert(srLang ? 'Klijent nije prihvatio ponudu preko portala. Zatražite ga da uđe u portal i klikne "Prihvatam", ili koristite "Bez portala" opciju (traži posebnu dozvolu).' : 'Client has not accepted via portal.');
                    e.currentTarget.disabled = false;
                } else if (data.error === 'ALREADY_CONVERTED') {
                    alert(srLang ? 'Ova ponuda je već konvertovana u dil.' : 'This offer is already converted to a deal.');
                } else {
                    alert('Error: ' + (data.error || 'Unknown'));
                    e.currentTarget.disabled = false;
                }
            } catch (err) {
                alert('Network error');
                e.currentTarget.disabled = false;
            }
        });
    });
}