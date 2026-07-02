// static/js/modules/deals/deals_form.js
function showDealForm({dealId = null, offerDetails = null} = {}) {
    state.editingItem = dealId ? state.data.deals.find(d => d.id === dealId) : null;
    const item = state.editingItem || (offerDetails ? { 
         productId: offerDetails.productId, supplierId: offerDetails.supplierId, 
         purchasePrice: offerDetails.price, purchaseCurrency: offerDetails.currency, 
         unit: offerDetails.unit, incoterm: offerDetails.incoterm 
     } : { 
         costs: [], bankCosts: 0, quantity: 1, unit: typeof UNITS !== 'undefined' ? UNITS[0] : 'MT', associates: [], 
         purchaseCurrency: state.settings?.currency || 'USD', sellingCurrency: state.settings?.currency || 'USD', 
         exchangeRate: 1, supplierBankDetails: '' 
     });
     
     item.associates = item.associates || [];
     
     // FIX: Bezbedna provera dozvola koristeći optional chaining
     const canViewCosts = state.user?.role === 'admin' || (state.user?.permissions && state.user.permissions['deals_view_costs']);
     
     const isSupplier = (p) => (p.types || []).includes('supplier') || (p.types || []).includes('Dobavljač');
     const isBuyer = (p) => (p.types || []).includes('buyer') || (p.types || []).includes('Kupac');
     const supplierOptions = `<option value="">${Utils.t('actions.select_supplier')}</option>` + (state.data.partners||[]).filter(isSupplier).map(p => `<option value="${p.id}" ${item.supplierId === p.id ? 'selected' : ''}>${Utils.escapeHtml(p.companyName)}</option>`).join('');
     const buyerOptions = `<option value="">${Utils.t('actions.select_buyer')}</option>` + (state.data.partners||[]).filter(isBuyer).map(p => `<option value="${p.id}" ${item.buyerId === p.id ? 'selected' : ''}>${Utils.escapeHtml(p.companyName)}</option>`).join('');
     const productOptions = `<option value="">${Utils.t('actions.select_product')}</option>` + (state.data.products||[]).map(pr => `<option value="${pr.id}" ${item.productId === pr.id ? 'selected' : ''}>${Utils.escapeHtml(pr.name)}</option>`).join('');
     
     const costsHtml = (item.costs || []).map((c, i) => `
        <div class="flex gap-2 mb-2 cost-item">
            <input name="costType_${i}" class="form-input" placeholder="${Utils.t('fields.costType')}" value="${Utils.escapeHtml(c.type || '')}" />
            <input name="costAmount_${i}" type="number" step="0.01" class="form-input" placeholder="${Utils.t('fields.costAmount')}" value="${Utils.escapeHtml(c.amount || '')}" />
            <button type="button" class="btn small bg-red-600 text-white remove-cost">🗑</button>
        </div>`).join('');
         
     const associatesHtml = item.associates.map((a, i) => typeof DealsCalculations !== 'undefined' ? DealsCalculations.renderAssociateRow(a, i) : '').join('');
     const getStatusSelected = (key, legacyKey) => (item.status === key || item.status === legacyKey) ? 'selected' : '';
     const currenciesList = typeof CURRENCIES !== 'undefined' ? CURRENCIES : ['USD', 'EUR', 'AED'];
     const unitsList = typeof UNITS !== 'undefined' ? UNITS : ['MT', 'kg', 'pcs'];
     const incotermsList = typeof INCOTERMS !== 'undefined' ? INCOTERMS : ['FOB', 'CIF', 'EXW'];

     const html = `
     <form id="deal-form" class="space-y-4 relative">
       <div id="live-profit-display" class="bg-[var(--panel)] p-4 border border-blue-500 rounded-lg text-center shadow-lg sticky top-0 z-10 ${canViewCosts ? '' : 'hidden'}">
          <span class="text-sm text-[var(--muted)]">${Utils.t('deals.expectedProfit')}</span>
          <div class="text-3xl font-extrabold text-green-500" id="live-profit-amount">0.00</div>
       </div>
       <div><label class="block text-sm font-medium text-main">${Utils.t('fields.dealId')}</label><input name="contractId" class="form-input mt-1" value="${Utils.escapeHtml(item.contractId || '')}" required /></div>
       
       <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
           ${canViewCosts ? `<div><label class="block text-sm font-medium text-main">${Utils.t('fields.supplier')}</label><select name="supplierId" class="form-input mt-1">${supplierOptions}</select></div>` : `<div class="bg-gray-100 p-2 rounded text-[var(--muted)] text-sm flex items-center justify-center border border-dashed">Supplier: *** HIDDEN ***</div>`}
           <div><label class="block text-sm font-medium text-main">${Utils.t('fields.buyer')}</label><select name="buyerId" class="form-input mt-1" required>${buyerOptions}</select></div>
           <div>
               <label class="block text-sm font-medium text-main">${Utils.t('fields.product')}</label>
               <select name="productId" class="form-input mt-1" ${offerDetails ? 'disabled': ''} required>${productOptions}</select>
               ${offerDetails ? `<input type="hidden" name="productId" value="${item.productId}">` : ''}
           </div>
       </div>
       
       <fieldset class="border border-[var(--border)] p-3 rounded-lg"><legend class="text-sm px-2 text-yellow-500 font-bold">${Utils.t('fields.currencySetup')}</legend>
         <div class="grid grid-cols-2 gap-4 border-b border-[var(--border)] pb-4 mb-4">
             ${canViewCosts ? `
             <div class="bg-[var(--panel)] p-3 rounded border border-[var(--border)]"><label class="block text-sm font-bold text-red-500">${Utils.t('deals.purchaseSupplier')}</label>
                <div class="grid grid-cols-2 gap-2 mt-2">
                    <input name="purchasePrice" type="number" step="0.01" class="form-input" placeholder="${Utils.t('fields.price')}" value="${Utils.escapeHtml(item.purchasePrice || '')}" ${offerDetails ? 'readonly': ''} />
                    <select name="purchaseCurrency" id="purchaseCurrency" class="form-input">${currenciesList.map(c => `<option value="${c}" ${(item.purchaseCurrency || item.currency || 'USD') === c ? 'selected' : ''}>${c}</option>`).join('')}</select>
                </div>
             </div>
             ` : `<div class="bg-[var(--panel)] p-3 rounded border border-dashed flex flex-col justify-center items-center text-red-500 font-bold text-sm">PURCHASE PRICES HIDDEN BY ADMIN</div>`}
             
             <div class="bg-[var(--panel)] p-3 rounded border border-[var(--border)] ${!canViewCosts ? 'col-span-2' : ''}"><label class="block text-sm font-bold text-green-500">${Utils.t('deals.saleBuyer')}</label>
                <div class="grid grid-cols-2 gap-2 mt-2">
                    <input name="sellingPrice" type="number" step="0.01" class="form-input" placeholder="${Utils.t('fields.price')}" value="${Utils.escapeHtml(item.sellingPrice || '')}" required/>
                    <select name="sellingCurrency" id="sellingCurrency" class="form-input">${currenciesList.map(c => `<option value="${c}" ${(item.sellingCurrency || item.currency || 'USD') === c ? 'selected' : ''}>${c}</option>`).join('')}</select>
                </div>
             </div>
         </div>
         
         ${canViewCosts ? `
         <div id="exchange-rate-container" class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-2 ${item.purchaseCurrency !== item.sellingCurrency ? '' : 'hidden'}">
             <div class="col-span-2 lg:col-span-4 bg-yellow-100 dark:bg-yellow-900/20 p-2 rounded flex items-center gap-2 border border-yellow-400 flex-wrap">
                <span class="text-sm font-bold text-main">${Utils.t('deals.exchangeRate')}: </span>
                <span class="text-main">1 <span id="rate-base-cur">USD</span> = </span>
                <input name="exchangeRate" type="number" step="0.000001" class="form-input w-32" value="${item.exchangeRate || 1}" />
                <span id="rate-target-cur" class="mr-4 text-main">AED</span>
                <button type="button" id="refresh-rate-btn" class="btn small bg-green-500 text-white ml-auto">${Utils.t('deals.exchangeRateFetch')}</button>
             </div>
         </div>
         <div class="flex items-center gap-4 mt-2">
             <input name="targetProfit" type="number" step="0.1" class="form-input w-32" placeholder="${Utils.t('fields.targetProfitPlaceholder')}">
             <button type="button" id="calculate-price-btn" class="btn bg-blue-500 text-white w-full">${Utils.t('deals.calcSellingPriceBtn')}</button>
         </div>` : ''}
       </fieldset>

       <div class="grid grid-cols-3 gap-4">
           <div><label class="block text-sm font-medium text-main">${Utils.t('fields.quantity')}</label><input name="quantity" type="number" step="0.0001" class="form-input mt-1" value="${Utils.escapeHtml(item.quantity || 1)}" required/></div>
           <div><label class="block text-sm font-medium text-main">${Utils.t('fields.unit')}</label><select name="unit" class="form-input mt-1">${unitsList.map(u => `<option value="${u}" ${item.unit === u ? 'selected' : ''}>${u}</option>`).join('')}</select></div>
           <div><label class="block text-sm font-medium text-main">${Utils.t('fields.incoterm')}</label><select name="incoterm" class="form-input mt-1"><option value="">${Utils.t('misc.textFieldPlaceholder')}</option>${incotermsList.map(i => `<option value="${i}" ${item.incoterm === i ? 'selected' : ''}>${i}</option>`).join('')}</select></div>
       </div>

       <fieldset class="border border-[var(--border)] p-3 rounded-lg"><legend class="text-sm px-2 text-blue-500 font-bold">${Utils.t('deals.logisticsAndPayments')}</legend>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-2 border-b border-[var(--border)] pb-4">
            <div><label class="block text-sm font-medium text-main">${Utils.t('fields.paymentAccount')}</label><select name="paymentAccountId" class="form-input mt-1"><option value="">${Utils.t('misc.textFieldPlaceholder')}</option>${(state.data.accounts||[]).map(a => `<option value="${a.id}" ${item.paymentAccountId === a.id ? 'selected' : ''}>${Utils.escapeHtml(a.name)} (${a.currency})</option>`).join('')}</select></div>
            ${canViewCosts ? `<div><label class="block text-sm font-medium text-red-500">${Utils.t('fields.supplierBankDetails')}</label><textarea name="supplierBankDetails" class="form-input mt-1" rows="3" placeholder="${Utils.t('invoice.bank')}:\n${Utils.t('invoice.account_no')}:\n${Utils.t('invoice.swift')}:">${Utils.escapeHtml(item.supplierBankDetails || '')}</textarea></div>` : ''}
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div><label class="block text-sm font-medium text-main">${Utils.t('fields.dealStartDate')}</label><input name="dealStartDate" type="date" class="form-input mt-1" value="${Utils.escapeHtml(item.dealStartDate || '')}"></div>
            <div><label class="block text-sm font-medium text-main">${Utils.t('fields.deliveryDate')}</label><input name="deliveryDate" type="date" class="form-input mt-1" value="${Utils.escapeHtml(item.deliveryDate || '')}"></div>
            <div class="md:col-span-2"><label class="block text-sm font-medium text-main">${Utils.t('fields.deliveryLocation')}</label><input name="deliveryLocation" class="form-input mt-1" value="${Utils.escapeHtml(item.deliveryLocation || '')}"></div>
          </div>
       </fieldset>

       ${canViewCosts ? `
       <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
           <fieldset class="border border-[var(--border)] p-3 rounded-lg"><legend class="text-sm px-2 text-red-500 font-bold">${Utils.t('fields.costs')}</legend>
               <div class="mb-4"><label class="block text-sm font-bold text-red-500">${Utils.t('fields.bankCosts')}</label><input name="bankCosts" type="number" step="0.01" class="form-input mt-1" value="${Utils.escapeHtml(item.bankCosts || 0)}" /></div>
               <div id="costs-container">${costsHtml}</div><button type="button" id="add-cost" class="btn small bg-blue-500 text-white mt-2">+ ${Utils.t('actions.add_cost')}</button>
           </fieldset>
           <fieldset class="border border-[var(--border)] p-3 rounded-lg"><legend class="text-sm px-2 text-main font-bold">${Utils.t('deals.associateCommissions')}</legend>
               <div id="associates-container">${associatesHtml}</div><button type="button" id="add-associate" class="btn small bg-blue-500 text-white mt-2">+ ${Utils.t('deals.addAssociate')}</button>
           </fieldset>
       </div>` : ''}
       
       <fieldset class="border border-[var(--border)] p-3 rounded-lg"><legend class="text-sm px-2 text-main font-bold">${Utils.t('fields.status')}</legend>
         <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
             <label class="block text-sm text-main">${Utils.t('fields.paymentDueDateBuyer')}<input name="buyerPaymentDate" type="date" class="form-input mt-1" value="${Utils.escapeHtml(item.paymentDates?.buyer || '')}" /></label>
             <label class="block text-sm text-main">${Utils.t('finances.paidOn')} (${Utils.t('finances.buyer')})<input name="buyerPaidOn" type="date" class="form-input mt-1" value="${Utils.escapeHtml(item.buyerPaidOn || '')}" /></label>
             
             ${canViewCosts ? `
             <label class="block text-sm text-main">${Utils.t('fields.paymentDueDateSupplier')}<input name="supplierPaymentDate" type="date" class="form-input mt-1" value="${Utils.escapeHtml(item.paymentDates?.supplier || '')}" /></label>
             <label class="block text-sm text-main">${Utils.t('finances.paidOn')} (${Utils.t('finances.supplier')})<input name="supplierPaidOn" type="date" class="form-input mt-1" value="${Utils.escapeHtml(item.supplierPaidOn || '')}" /></label>
             ` : ''}
         </div>
         <div class="mt-4">
             <label class="block text-sm font-medium text-main">${Utils.t('fields.status')}</label>
             <select name="status" class="form-input mt-1">
                 <option value="negotiation" ${getStatusSelected('negotiation', 'U pregovorima')}>${Utils.t('deals.status_negotiation')}</option>
                 <option value="signed" ${getStatusSelected('signed', 'Ugovor potpisan')}>${Utils.t('deals.status_signed')}</option>
                 <option value="payment_pending" ${getStatusSelected('payment_pending', 'Plaćanje u toku')}>${Utils.t('deals.status_payment')}</option>
                 <option value="completed" ${getStatusSelected('completed', 'Završeno')}>${Utils.t('deals.status_completed')}</option>
             </select>
         </div>
       </fieldset>
       <div class="flex justify-end"><button class="btn bg-blue-500 text-white px-8 py-2" type="submit">${Utils.t('actions.save')}</button></div>
     </form>`;
     
     Utils.openModal(state.editingItem ? Utils.t('actions.edit') : Utils.t('add.deal'), html, typeof handleSaveDeal === 'function' ? handleSaveDeal : ()=>{});
     
     if (canViewCosts && typeof DealsCalculations !== 'undefined') {
         DealsCalculations.initFormEvents(document.getElementById('deal-form'));
     }
}