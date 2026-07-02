// static/js/modules/products/demands.js

function showDemandForm(id = null) {
    state.editingItem = id ? state.data.demands.find(d => d.id === id) : null; 
    const item = state.editingItem || {};
    const tLang = (srStr, enStr) => Utils.getLang() === 'sr' ? srStr : enStr;
    
    const isBuyer = (p) => (p.types || []).includes('buyer') || (p.types || []).includes('Kupac');
    const buyerOptions = state.data.partners.filter(isBuyer).map(p => `<option value="${p.id}" ${item.buyerId === p.id ? 'selected' : ''}>${Utils.escapeHtml(p.companyName)}</option>`).join('');
    const productOptions = state.data.products.map(p => `<option value="${p.id}" ${item.productId === p.id ? 'selected' : ''}>${Utils.escapeHtml(p.name)}</option>`).join('');
    
    const html = `
    <form id="demand-form" class="space-y-6">
      <div class="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
          <label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">${Utils.t('offer.customer')} <span class="text-red-500">*</span></label>
          <select name="buyerId" class="w-full bg-slate-50 border border-slate-300 rounded-lg px-4 py-3 text-sm font-bold text-slate-800 outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer shadow-sm" required>
              <option value="">${Utils.t('actions.select_buyer')}</option>
              ${buyerOptions}
          </select>
      </div>
      
      <div class="border border-slate-200 p-6 rounded-xl bg-slate-50 shadow-sm relative pt-8 mt-6">
          <div class="absolute -top-3 left-6 bg-slate-800 text-white text-[10px] font-black uppercase tracking-widest px-3 py-1 rounded shadow-sm">${Utils.t('fields.demandFor')}</div>
          
          <div class="flex gap-4 p-4 bg-white border border-slate-200 rounded-lg mb-6 shadow-sm">
              <label class="cursor-pointer inline-flex items-center text-sm font-bold text-slate-700 hover:text-blue-600 transition-colors">
                  <input type="radio" name="demandType" value="existing" class="mr-2 w-4 h-4 text-blue-600 focus:ring-blue-500 border-slate-300" ${!item.isNewProduct ? 'checked' : ''}>
                  📦 ${Utils.t('fields.existingProduct')}
              </label>
              <label class="cursor-pointer inline-flex items-center text-sm font-bold text-slate-700 hover:text-amber-600 transition-colors">
                  <input type="radio" name="demandType" value="new" class="mr-2 w-4 h-4 text-amber-500 focus:ring-amber-500 border-slate-300" ${item.isNewProduct ? 'checked' : ''}>
                  ✨ ${Utils.t('fields.newProduct')}
              </label>
          </div>
          
          <div id="existing-product-container" class="mb-4">
              <label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">${Utils.t('actions.select_product')}</label>
              <select name="productId" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-3 text-sm font-bold text-slate-800 outline-none focus:ring-2 focus:ring-blue-500 shadow-sm cursor-pointer">
                  <option value="">-- Izaberi --</option>
                  ${productOptions}
              </select>
          </div>
          
          <div id="new-product-container" class="hidden mb-4">
              <label class="block text-[10px] font-black text-amber-700 uppercase tracking-widest mb-2">${Utils.t('fields.newProductName')}</label>
              <input name="newProductName" class="w-full bg-white border border-amber-300 text-amber-900 rounded-lg px-4 py-3 text-sm font-bold outline-none focus:ring-2 focus:ring-amber-500 shadow-sm" value="${Utils.escapeHtml(item.productName || '')}" placeholder="${Utils.t('placeholders.newProduct')}"/>
          </div>
          
          <div class="mt-6 pt-6 border-t border-slate-200 grid grid-cols-2 gap-6">
              <div>
                  <label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">${Utils.t('fields.quantity')}</label>
                  <input name="quantity" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-3 text-lg font-black text-slate-900 outline-none focus:ring-2 focus:ring-blue-500 shadow-sm" value="${Utils.escapeHtml(item.quantity || '')}" placeholder="0.00"/>
              </div>
              <div>
                  <label class="block text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2">Status Zahteva</label>
                  <select name="status" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-3 text-sm font-bold text-slate-800 outline-none focus:ring-2 focus:ring-blue-500 shadow-sm cursor-pointer">
                      <option value="open" ${item.status === 'open' ? 'selected' : ''}>Otvoreno (Tražimo)</option>
                      <option value="sourced" ${item.status === 'sourced' ? 'selected' : ''}>Pronađen Dobavljač</option>
                      <option value="closed" ${item.status === 'closed' ? 'selected' : ''}>Završeno / Prodato</option>
                  </select>
              </div>
          </div>
      </div>
      
      <div class="flex justify-end pt-4 mt-6 border-t border-slate-200">
          <button class="bg-blue-600 hover:bg-blue-700 text-white font-black px-10 py-3 rounded-xl shadow-xl transition-transform transform hover:-translate-y-0.5 tracking-widest uppercase" type="submit">💾 ${Utils.t('actions.save')}</button>
      </div>
    </form>`;
    
    Utils.openModal(state.editingItem ? tLang('Izmena Zahteva', 'Edit Demand') : tLang('Novi Zahtev Kupca', 'New Client Demand'), html, async (fd) => {
        const id = state.editingItem?.id || Utils.generateId(); 
        const isNew = fd.get('demandType') === 'new';
        
        if (isNew && !fd.get('newProductName').trim()) { alert(tLang('Unesite naziv novog proizvoda.', 'Enter new product name.')); return; }
        if (!isNew && !fd.get('productId')) { alert(tLang('Izaberite postojeći proizvod.', 'Select existing product.')); return; }
        
        const demand = {
            id,
            buyerId: fd.get('buyerId'),
            productId: isNew ? null : fd.get('productId'),
            productName: isNew ? fd.get('newProductName') : Utils.getProductNameById(fd.get('productId')),
            quantity: fd.get('quantity'),
            status: fd.get('status') || 'open',
            isNewProduct: isNew,
            createdAt: state.editingItem?.createdAt || new Date().toISOString(),
            ownerId: state.editingItem?.ownerId || state.user?.id || 'SYSTEM',
            sharedWith: state.editingItem?.sharedWith || []
        };
        
        if(state.editingItem) {
           state.data.demands[state.data.demands.findIndex(d => d.id === id)] = demand; 
        } else {
           state.data.demands.push(demand);
        }
        
        await saveSingleItem('demands', demand); 
        Utils.closeModal(); 
        render(); 
        if(typeof checkAllNotifications === 'function') checkAllNotifications();
    });
    
    const form = document.getElementById('demand-form'); 
    const existingContainer = form.querySelector('#existing-product-container'); 
    const newContainer = form.querySelector('#new-product-container');
    
    const updateVisibility = () => { 
        if(form.querySelector('input[name="demandType"]:checked').value === 'new') {
           existingContainer.classList.add('hidden'); 
           newContainer.classList.remove('hidden');
       } else {
           existingContainer.classList.remove('hidden'); 
           newContainer.classList.add('hidden');
       }
    };
    
    form.querySelectorAll('input[name="demandType"]').forEach(input => input.addEventListener('change', updateVisibility)); 
    updateVisibility();
}

function renderDemandsView() {
    const main = document.getElementById('main-content'); 
    if(!main) return;
    main.innerHTML = '';
    
    const tLang = (srStr, enStr) => Utils.getLang() === 'sr' ? srStr : enStr;
    const header = Utils.createViewHeader(tLang('Zahtevi Kupaca', 'Client Demands'), tLang('+ Novi Zahtev', '+ New Demand'), showDemandForm); 
    main.appendChild(header);
    
    const container = document.createElement('div'); 
    container.className = 'bg-white rounded-2xl shadow-xl border border-slate-200 overflow-x-auto mt-6 mb-10';
    
    const currentLang = Utils.getLang() === 'sr' ? 'sr-RS' : 'en-US';
    
    const stColors = {
        'open': 'bg-blue-100 text-blue-800 border-blue-300',
        'sourced': 'bg-emerald-100 text-emerald-800 border-emerald-300',
        'closed': 'bg-slate-200 text-slate-700 border-slate-300'
    };
    const stLabels = {
        'open': 'OTVORENO', 'sourced': 'PRONAĐENO', 'closed': 'ZAVRŠENO'
    };

    container.innerHTML = `
    <table class="w-full text-left border-collapse">
        <thead>
            <tr class="bg-slate-50 border-b border-slate-200">
                <th class="p-5 uppercase text-[10px] font-black tracking-widest text-slate-500">${Utils.t('offer.customer') || 'Kupac'}</th>
                <th class="p-5 uppercase text-[10px] font-black tracking-widest text-slate-500">${Utils.t('fields.productName') || 'Proizvod'}</th>
                <th class="p-5 uppercase text-[10px] font-black tracking-widest text-slate-500">${Utils.t('fields.quantity') || 'Količina'}</th>
                <th class="p-5 uppercase text-[10px] font-black tracking-widest text-slate-500">Status</th>
                <th class="p-5 uppercase text-[10px] font-black tracking-widest text-slate-500">${Utils.t('audit.time') || 'Datum'}</th>
                <th class="p-5 text-right uppercase text-[10px] font-black tracking-widest text-slate-500">${Utils.t('misc.offerActionsTable') || 'Akcije'}</th>
            </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
            ${state.data.demands.map(d => {
                const isNewIndicator = d.isNewProduct ? `<span class="bg-amber-100 text-amber-800 border border-amber-300 px-2 py-0.5 text-[9px] rounded-full font-black uppercase ml-2 shadow-sm whitespace-nowrap">✨ ${Utils.t('misc.newTag') || 'NOVO (VAN KATALOGA)'}</span>` : '';
                const bC = stColors[d.status || 'open'];
                const bL = stLabels[d.status || 'open'];
                
                return `
                <tr class="hover:bg-slate-50 transition-colors">
                    <td class="p-5 text-sm font-black text-slate-900">🏢 ${Utils.escapeHtml(Utils.getPartnerNameById(d.buyerId) || d.buyerName || '')}</td>
                    <td class="p-5 text-sm font-bold text-slate-700">${Utils.escapeHtml(d.productName || '')} ${isNewIndicator}</td>
                    <td class="p-5 font-black text-blue-700 text-lg">${Utils.escapeHtml(d.quantity || '')}</td>
                    <td class="p-5"><span class="px-2.5 py-1 rounded text-[10px] font-black uppercase tracking-wider border shadow-sm ${bC}">${bL}</span></td>
                    <td class="p-5 text-xs text-slate-500 font-bold">${d.createdAt ? new Date(d.createdAt).toLocaleDateString(currentLang) : 'N/A'}</td>
                    <td class="p-5 text-right whitespace-nowrap">
                        <button class="bg-white border border-slate-300 hover:bg-slate-100 text-slate-700 font-bold px-4 py-2 rounded-lg text-xs shadow-sm transition-colors mr-2 edit-demand" data-id="${d.id}">✏️ ${Utils.t('actions.edit') || 'Izmeni'}</button>
                        <button class="bg-red-50 hover:bg-red-100 text-red-600 border border-red-100 font-bold px-4 py-2 rounded-lg text-xs shadow-sm transition-colors del-demand" data-id="${d.id}">🗑️</button>
                    </td>
                </tr>`;
            }).join('') || `<tr><td colspan="6" class="p-10 text-center text-slate-400 font-bold border-dashed border-2 border-slate-200 text-sm">${Utils.t('product_search.noResults') || 'Nema zahteva.'}</td></tr>`}
        </tbody>
    </table>`;
    
    main.appendChild(container);
    container.querySelectorAll('.edit-demand').forEach(b => b.addEventListener('click', e => showDemandForm(e.currentTarget.dataset.id)));
    container.querySelectorAll('.del-demand').forEach(b => b.addEventListener('click', e => Utils.handleDelete('demands', e.currentTarget.dataset.id)));
}