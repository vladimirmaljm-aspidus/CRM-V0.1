// Osnovne globalne funkcije
function generateId() { return Date.now().toString(36) + Math.random().toString(36).slice(2,8); }

function applyTheme() {
  const body = document.body; const toggle = document.getElementById('theme-toggle');
  if (state.theme === 'light') { body.classList.add('light-theme'); body.classList.remove('dark-theme'); if(toggle) toggle.checked = false; }
  else { body.classList.add('dark-theme'); body.classList.remove('light-theme'); if(toggle) toggle.checked = true; }
}

function hasPerm(module, action) {
    if (!state.user) return false;
    if (state.user.role === 'admin') return true; 
    const perms = state.user.permissions || {};
    if (action === 'view') {
        return perms[`${module}_view_all`] || perms[`${module}_view_own`] || perms[`${module}_view`];
    }
    return !!perms[`${module}_${action}`];
}

function toggleTheme() {
  state.theme = state.theme === 'dark' ? 'light' : 'dark';
  saveToStorage('theme'); applyTheme();
}

function resetFilters() {
  state.activeFilters = { 
      partners:{ type:'', city:'', country:'' }, 
      deals:{ status:'', buyerId:'', supplierId:'' }, 
      finances: { range: 'thisMonth', startDate: '', endDate: '' }, 
      cashflow: { range: 'thisMonth', accountId: 'all', category: 'all', startDate: '', endDate: '' } 
  }; 
}

function handleFilterChange(view, name, value) { state.activeFilters[view][name] = value; render(); }

function createViewHeader(title, buttonText, onButtonClick) { 
  const header = document.createElement('div'); header.className = 'flex items-center justify-between mb-6'; 
  header.innerHTML = `<h2 class="text-3xl font-extrabold text-main">${title}</h2>${buttonText ? `<div class="flex items-center gap-4"><button class="btn bg-blue-600 text-white shadow" id="view-add-btn">${buttonText}</button></div>` : ''}`; 
  if (onButtonClick) header.querySelector('#view-add-btn').addEventListener('click', onButtonClick); 
  return header; 
}

function applyFiltersFor(view) { 
  let data = [...(state.data[view] || [])]; const f = state.activeFilters[view]; if(!f) return data; 
  if(view==='partners'){ 
      if(f.type) data = data.filter(p => (p.types||[]).includes(f.type)); 
      if(f.city) data = data.filter(p => (p.address?.city||'').toLowerCase().includes(f.city.toLowerCase())); 
      if(f.country) data = data.filter(p => (p.address?.country||'').toLowerCase().includes(f.country.toLowerCase())); 
  } 
  if(view==='deals'){ 
      if(f.status) data = data.filter(d => d.status === f.status); 
      if(f.buyerId) data = data.filter(d => d.buyerId === f.buyerId); 
      if(f.supplierId) data = data.filter(d => d.supplierId === f.supplierId); 
  } 
  return data; 
}

function openModal(title, innerHtml, onSubmit) { 
  const viewName = state.currentView;
  const isDetailsModal = title.includes(typeof t === 'function' ? t('actions.details') : 'Details');
  
  if (viewName !== 'settings' && viewName !== 'users' && !isDetailsModal && !hasPerm(viewName, 'edit')) {
      alert(typeof t === 'function' ? t('users.accessDeniedEdit') : 'Access Denied');
      return;
  }

  const md = document.getElementById('modal-backdrop'); const mb = document.getElementById('modal-body'); const mt = document.getElementById('modal-title'); 
  if(!md || !mb || !mt) return; // Sigurnosni check
  
  mt.innerText = title; mb.innerHTML = innerHtml; md.classList.remove('hidden'); md.classList.add('flex'); 
  const form = mb.querySelector('form'); 
  if(form && onSubmit){ 
      // Kloniranje forme sprečava dupliranje Event Listenera ako se modal brzo otvori/zatvori
      const newForm = form.cloneNode(true); form.parentNode.replaceChild(newForm, form); 
      newForm.addEventListener('submit', (e)=>{ 
          e.preventDefault(); 
          const btn = newForm.querySelector('button[type="submit"]');
          if(btn) { btn.disabled = true; btn.innerHTML = '⏳ ...'; }
          try {
              onSubmit(new FormData(newForm)); 
          } catch(err) {
              console.error(err);
              if(btn) { btn.disabled = false; btn.innerHTML = 'Greška! Pokušajte ponovo.'; }
          }
      }); 
  } 
}

function closeModal() { 
  const md = document.getElementById('modal-backdrop');
  if(md) {
      md.classList.add('hidden'); 
      md.classList.remove('flex');
  }
  const mb = document.getElementById('modal-body');
  if(mb) mb.innerHTML=''; 
  state.editingItem = null; 
  
  const mdContent = document.querySelector('#modal-backdrop > div');
  if(mdContent) mdContent.id = 'modal-content';
}

async function handleDelete(key, id) { 
    if (key === 'partners') {
        const hasDeals = state.data.deals.some(d => d.buyerId === id || d.supplierId === id || (d.associates && d.associates.some(a => a.partnerId === id)));
        const hasOffers = state.data.offers && state.data.offers.some(o => o.customerId === id);
        if (hasDeals || hasOffers) { alert(typeof t === 'function' ? t('misc.rejectPartnerDelete') : 'Cannot delete partner.'); return; }
    }
    if (key === 'products') {
        const hasDeals = state.data.deals.some(d => d.productId === id);
        const hasOffers = state.data.offers && state.data.offers.some(o => o.productId === id);
        if (hasDeals || hasOffers) { alert(typeof t === 'function' ? t('misc.rejectProductDelete') : 'Cannot delete product.'); return; }
    }
    if (key === 'accounts') {
        const hasTxs = state.data.transactions.some(tx => tx.accountId === id || tx.fromAccountId === id || tx.toAccountId === id);
        if (hasTxs) { alert(typeof t === 'function' ? t('misc.rejectAccountDelete') : 'Cannot delete account.'); return; }
    }

    try {
        await deleteItemFromServer(key, id);
        state.data[key] = state.data[key].filter(it => it.id !== id); 
        render(); 
    } catch(e) {
        console.error("Delete failed", e);
    }
}

function getPartnerNameById(id) { 
    if(!id || !state.data || !state.data.partners) return 'N/A';
    return state.data.partners.find(p=>p.id===id)?.companyName || 'N/A'; 
}
function getProductNameById(id) { 
    if(!id || !state.data || !state.data.products) return 'N/A';
    return state.data.products.find(p=>p.id===id)?.name || 'N/A'; 
}

function formatCurrency(v, currencyCode = state.settings?.currency || 'USD') { 
  const cCode = currencyCode || 'USD'; 
  const numberFormat = new Intl.NumberFormat('en-US', { style: 'currency', currency: cCode, minimumFractionDigits: 2, maximumFractionDigits: 2 }); 
  if(isNaN(v) || v === null || v === undefined) return numberFormat.format(0); 
  return numberFormat.format(v); 
}

// Anti-XSS zaštita sa null check-om
function escapeHtml(s) { 
    if(s === null || s === undefined) return ''; 
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); 
}
function escapeFilename(n) { return (n||'file').replaceAll(/[^a-zA-Z0-9.\-_]/g,'_'); }

let LIVE_RATES = { ...GLOBAL_RATES }; 
async function fetchLiveExchangeRates() {
    try {
        const res = await fetch('https://open.er-api.com/v6/latest/USD');
        const data = await res.json();
        if(data && data.rates) {
            Object.assign(LIVE_RATES, data.rates);
            localStorage.setItem('cached_rates', JSON.stringify(LIVE_RATES));
        }
    } catch(e) { 
        const cached = localStorage.getItem('cached_rates');
        if(cached) Object.assign(LIVE_RATES, JSON.parse(cached));
    }
}

function convertCurrency(amount, fromCurrency, toCurrency) {
    if (fromCurrency === toCurrency) return amount;
    const rateFrom = LIVE_RATES[fromCurrency] || 1;
    const rateTo = LIVE_RATES[toCurrency] || 1;
    return (amount / rateFrom) * rateTo; 
}

async function uploadFileToServer(file) {
    const formData = new FormData();
    formData.append('file', file);
    try {
        const res = await fetch('/api/upload', { method: 'POST', body: formData });
        const data = await res.json();
        if (data.url) return data.url;
        throw new Error(data.error || 'Upload failed');
    } catch (err) {
        return null;
    }
}

function formatBytes(bytes) { if(!bytes || isNaN(bytes)) return ''; const sizes = ['B','KB','MB','GB']; const i = Math.floor(Math.log(bytes)/Math.log(1024)); return (bytes/Math.pow(1024,i)).toFixed(2)+' '+sizes[i]; }

function initAutocomplete(inp, getArrayCallback) {
  let currentFocus;
  inp.addEventListener("input", function(e) {
      let arr = typeof getArrayCallback === 'function' ? getArrayCallback() : getArrayCallback;
      let a, b, i, val = this.value; closeAllLists(); if (!val) { return false;}
      currentFocus = -1; a = document.createElement("DIV"); a.setAttribute("id", this.id + "autocomplete-list"); a.setAttribute("class", "autocomplete-items"); this.parentNode.appendChild(a);
      for (i = 0; i < arr.length; i++) {
          if (arr[i].toLowerCase().includes(val.toLowerCase())) {
              b = document.createElement("DIV"); let matchIndex = arr[i].toLowerCase().indexOf(val.toLowerCase());
              b.innerHTML = arr[i].substr(0, matchIndex); b.innerHTML += "<strong>" + arr[i].substr(matchIndex, val.length) + "</strong>"; b.innerHTML += arr[i].substr(matchIndex + val.length);
              b.innerHTML += "<input type='hidden' value='" + escapeHtml(arr[i]) + "'>";
              b.addEventListener("click", function(e) { inp.value = this.getElementsByTagName("input")[0].value; closeAllLists(); });
              a.appendChild(b);
          }
      }
  });
  
  inp.addEventListener("focus", function(e) {
      if(!this.value) {
          let arr = typeof getArrayCallback === 'function' ? getArrayCallback() : getArrayCallback;
          if(arr.length > 0 && arr.length <= 15) {
             let a, b, i; closeAllLists(); currentFocus = -1; 
             a = document.createElement("DIV"); a.setAttribute("id", this.id + "autocomplete-list"); a.setAttribute("class", "autocomplete-items"); this.parentNode.appendChild(a);
             for (i = 0; i < arr.length; i++) {
                 b = document.createElement("DIV"); b.innerHTML = arr[i]; b.innerHTML += "<input type='hidden' value='" + escapeHtml(arr[i]) + "'>";
                 b.addEventListener("click", function(e) { inp.value = this.getElementsByTagName("input")[0].value; closeAllLists(); });
                 a.appendChild(b);
             }
          }
      }
  });

  inp.addEventListener("keydown", function(e) {
      let x = document.getElementById(this.id + "autocomplete-list"); if (x) x = x.getElementsByTagName("div");
      if (e.keyCode == 40) { currentFocus++; addActive(x); } else if (e.keyCode == 38) { currentFocus--; addActive(x);
      } else if (e.keyCode == 13) { e.preventDefault(); if (currentFocus > -1) { if (x) x[currentFocus].click(); }
      } else if (e.key === "Escape") { closeAllLists(); }
  });
  function addActive(x) { if (!x) return false; removeActive(x); if (currentFocus >= x.length) currentFocus = 0; if (currentFocus < 0) currentFocus = (x.length - 1); x[currentFocus].classList.add("autocomplete-active"); }
  function removeActive(x) { for (let i = 0; i < x.length; i++) { x[i].classList.remove("autocomplete-active"); } }
  function closeAllLists(elmnt) { const x = document.getElementsByClassName("autocomplete-items"); for (let i = 0; i < x.length; i++) { if (elmnt != x[i] && elmnt != inp) { x[i].parentNode.removeChild(x[i]); } } }
  document.addEventListener("click", function (e) { closeAllLists(e.target); });
}

window.Utils = {
    generateId,
    applyTheme,
    hasPerm,
    toggleTheme,
    resetFilters,
    handleFilterChange,
    createViewHeader,
    applyFiltersFor,
    openModal,
    closeModal,
    handleDelete,
    getPartnerNameById,
    getProductNameById,
    formatCurrency,
    escapeHtml,
    escapeFilename,
    fetchLiveExchangeRates,
    convertCurrency,
    uploadFileToServer,
    formatBytes,
    initAutocomplete,
    t: (typeof t === 'function') ? t : function(str) { return str; },
    getLang: function() { return (typeof state !== 'undefined' && state.lang) ? state.lang : 'en'; }
};