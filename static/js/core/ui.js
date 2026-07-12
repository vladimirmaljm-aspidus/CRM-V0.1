// static/js/core/ui.js

// Čiste line-ikone (Heroicons stil) umesto emoji — profesionalniji, konzistentan izgled.
const NAV_ICONS = {
  deals: '<path d="M9 12h6m-6 4h6m2 5H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5.586a1 1 0 0 1 .707.293l5.414 5.414a1 1 0 0 1 .293.707V19a2 2 0 0 1-2 2z"/>',
  offers: '<path d="M12 3l2.09 4.26L19 8l-3.5 3.36L16.18 16 12 13.77 7.82 16l.68-4.64L5 8l4.91-.74L12 3z"/>',
  products: '<path d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-14L4 7m8 4v10M4 7v10l8 4"/>',
  demands: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
  product_search: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
  finances: '<path d="M12 3v18m5-14H9.5a2.5 2.5 0 0 0 0 5h5a2.5 2.5 0 0 1 0 5H7"/>',
  cashflow: '<path d="M3 12c2-3 4-3 6 0s4 3 6 0 4-3 6 0M3 18c2-3 4-3 6 0s4 3 6 0 4-3 6 0"/>',
  partners: '<circle cx="9" cy="8" r="3"/><path d="M3 20a6 6 0 0 1 12 0M16 6a3 3 0 0 1 0 6m5 8a5 5 0 0 0-4-4.9"/>',
  network: '<circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><path d="M6.7 7.5l4 8.5m6.6-8.5l-4 8.5M7 6h10"/>',
  users: '<circle cx="12" cy="8" r="3.2"/><path d="M5 20a7 7 0 0 1 14 0"/>',
  audit: '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z"/><path d="M9.5 12l1.8 1.8 3.2-3.6"/>'
};

let fullNavigationItems = [
  { view:'deals', icon:'deals', labelPath:'nav.deals', group: 'sales' },
  { view:'offers', icon:'offers', labelPath:'nav.offers', group: 'sales' },
  { view:'products', icon:'products', labelPath:'nav.products', group: 'sales' },
  { view:'demands', icon:'demands', labelPath:'nav.demands', group: 'sales' },
  { view:'product_search', icon:'product_search', labelPath:'nav.product_search', group: 'sales' },

  { view:'finances', icon:'finances', labelPath:'nav.finances', group: 'network' },
  { view:'cashflow', icon:'cashflow', labelPath:'nav.cashflow', group: 'network' },
  { view:'partners', icon:'partners', labelPath:'nav.partners', group: 'network' },
  { view:'network', icon:'network', labelPath:'nav.network', group: 'network' },

  { view:'users', icon:'users', labelPath:'users.manage', adminOnly: true, group: 'admin' },
  { view:'audit', icon:'audit', labelPath:'audit.title', adminOnly: true, permKey: 'audit_view', group: 'admin' }
];

function navIconSvg(key) {
  const path = NAV_ICONS[key] || NAV_ICONS.deals;
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" class="w-5 h-5">${path}</svg>`;
}

if (typeof DATA_KEYS !== 'undefined' && !DATA_KEYS.includes('offers')) {
    DATA_KEYS.push('offers');
}

function buildNavigation() {
  const nav = document.getElementById('navigation'); 
  if(!nav) return;
  nav.innerHTML = '';
  
  const groups = {
      'sales': { label: Utils.getLang() === 'sr' ? 'Prodaja i Inventar' : 'Sales & Inventory', items: [] },
      'network': { label: Utils.getLang() === 'sr' ? 'Finansije i Mreža' : 'Finances & Network', items: [] },
      'admin': { label: Utils.getLang() === 'sr' ? 'Administracija' : 'System Admin', items: [] }
  };

  const userPerms = (state.user && state.user.permissions) || {};
  fullNavigationItems.forEach(item => {
    if(item.adminOnly && (!state.user || state.user.role !== 'admin')) {
        // Izuzetak: stavke sa permKey su vidljive radniku kome je permisija dodeljena.
        if(!(item.permKey && userPerms[item.permKey])) return;
    }
    if(item.view === 'deals' && !hasPerm('deals', 'view')) return;
    if((item.view === 'finances' || item.view === 'cashflow') && !hasPerm('finances', 'view')) return;
    if(item.view === 'partners' && !hasPerm('partners', 'view')) return;
    if((item.view === 'products' || item.view === 'offers') && !hasPerm('products', 'view') && !hasPerm('offers', 'view')) return;
    groups[item.group].items.push(item);
  });

  Object.values(groups).forEach(grp => {
      if (grp.items.length === 0) return;
      
      const grpHeader = document.createElement('div');
      grpHeader.className = 'nav-group-title text-[10px] font-semibold text-slate-400 uppercase tracking-widest pl-3 mb-2 mt-6 transition-all duration-300';
      grpHeader.innerText = grp.label;
      nav.appendChild(grpHeader);

      grp.items.forEach(item => {
          const labelText = item.labelPath.startsWith('nav.') || item.labelPath.startsWith('users.') || item.labelPath.startsWith('audit.') ? t(item.labelPath) : item.labelPath;
          const isActive = state.currentView === item.view;

          const activeClasses = isActive
              ? 'bg-[var(--accent-soft)] text-[var(--accent)]'
              : 'text-[var(--muted)] hover:bg-[var(--hover-bg)] hover:text-[var(--text-color)]';

          const btn = document.createElement('button');
          btn.className = `nav-item group relative flex items-center w-full text-left px-2.5 py-2 mb-0.5 rounded-lg transition-colors duration-200 ${activeClasses}`;
          btn.title = labelText;

          btn.innerHTML = `
              ${isActive ? '<span class="absolute left-0 top-1.5 bottom-1.5 w-1 rounded-full bg-[var(--accent)]"></span>' : ''}
              <div class="flex items-center justify-center w-8 h-8 min-w-[2rem] ${isActive ? 'text-[var(--accent)]' : 'text-slate-400 group-hover:text-[var(--text-color)]'} transition-colors duration-200">
                  ${navIconSvg(item.icon)}
              </div>
              <span class="nav-text ml-2.5 text-sm ${isActive ? 'font-semibold' : 'font-medium'}">${labelText}</span>
          `;

          btn.addEventListener('click', () => { state.currentView = item.view; state.detailViewId = null; resetFilters(); render(); });
          nav.appendChild(btn);
      });
  });
}

function render() {
  buildNavigation();
  const main = document.getElementById('main-content'); 
  if(!main) return;
  main.innerHTML = '';
  
  if (!state.data.offers) state.data.offers = [];

  // Bezbedno renderovanje view-ova (sprečava pucanje ako modul nije učitan)
  try {
      if(state.currentView === 'deals' && hasPerm('deals', 'view')) { if(typeof renderDealsKanbanView==='function') renderDealsKanbanView(); }
      else if(state.currentView === 'network') { if(typeof renderNetworkView==='function') renderNetworkView(); }
      else if(state.currentView === 'finances' && hasPerm('finances', 'view')) { if(typeof renderFinanceView==='function') renderFinanceView(); }
      else if(state.currentView === 'cashflow' && hasPerm('finances', 'view')) { if(typeof renderCashFlowView==='function') renderCashFlowView(); }
      else if(state.currentView === 'product_search') { if(typeof renderProductSearchView==='function') renderProductSearchView(); }
      else if(state.currentView === 'partners' && hasPerm('partners', 'view')) { if(typeof renderPartnersView==='function') renderPartnersView(); }
      else if(state.currentView === 'partnerDetail' && hasPerm('partners', 'view')) { if(typeof renderPartnerDetailView==='function') renderPartnerDetailView(state.detailViewId); }
      else if(state.currentView === 'products' && hasPerm('products', 'view')) { if(typeof renderProductsView==='function') renderProductsView(); }
      else if(state.currentView === 'demands') { if(typeof renderDemandsView==='function') renderDemandsView(); }
      else if(state.currentView === 'offers' && hasPerm('offers', 'view')) { if(typeof renderOffersView==='function') renderOffersView(); }
      else if(state.currentView === 'users' && state.user.role === 'admin') { if(typeof renderUsersView==='function') renderUsersView(); } 
      else if(state.currentView === 'audit' && (state.user.role === 'admin' || (state.user.permissions && state.user.permissions.audit_view))) { if(typeof renderAuditLogView==='function') renderAuditLogView(); }
      else {
          main.innerHTML = `<div class="p-10 text-center"><h2 class="text-3xl text-red-500 font-bold mb-4">${t('users.accessDenied')}</h2><p class="text-gray-400">${t('users.accessDeniedMsg')}</p></div>`;
      }
  } catch(e) {
      console.error("Render Error:", e);
      main.innerHTML = `<div class="p-10 text-center"><h2 class="text-xl text-red-500 font-bold">Došlo je do greške u prikazu modula. Osvježite stranicu.</h2></div>`;
  }
  
  updateNotificationCounter();
}

function showProfileModal() {
    const srLang = Utils.getLang() === 'sr';
    const sig = state.user.signature;
    const sigLabel = srLang ? 'Moj potpis (na dokumentima)' : 'My signature (on documents)';
    const sigHint = srLang ? 'Ovaj potpis se koristi ISKLJUČIVO na Vašim dokumentima. Preporuka: PNG sa providnom pozadinom.' : 'This signature is used ONLY on your own documents. Recommended: PNG with transparent background.';
    const removeTxt = srLang ? 'Ukloni potpis' : 'Remove signature';
    const pwHint = srLang ? '(ostavite prazno da ne menjate)' : '(leave blank to keep current)';

    const html = `
    <div class="p-6 space-y-6">
      <form id="profile-form" class="space-y-5">
        <div>
            <label class="block text-xs font-semibold text-[var(--muted)] uppercase tracking-wide mb-1.5">${t('users.usernameLabelFull')}</label>
            <input type="text" class="form-input" style="background:var(--hover-bg)" value="${escapeHtml(state.user.username)}" readonly disabled>
        </div>
        <div>
            <label class="block text-xs font-semibold text-[var(--muted)] uppercase tracking-wide mb-1.5">${t('users.newPassword')} <span class="text-[var(--muted)] normal-case font-normal">${pwHint}</span></label>
            <input type="password" name="new_password" class="form-input" placeholder="••••••••">
        </div>
        <div class="flex justify-end pt-2">
            <button type="submit" class="btn btn-primary">${t('actions.saveChanges')}</button>
        </div>
      </form>

      <div class="pt-5 border-t border-[var(--border)]">
        <label class="block text-xs font-semibold text-[var(--muted)] uppercase tracking-wide mb-1.5">${sigLabel}</label>
        <p class="text-xs text-[var(--muted)] mb-3">${sigHint}</p>
        <div class="flex items-center gap-4">
            <div id="sig-preview" class="w-44 h-24 rounded-lg border border-[var(--border)] bg-white flex items-center justify-center overflow-hidden">
                ${sig ? `<img src="${escapeHtml(sig)}" alt="signature" class="max-w-full max-h-full object-contain">` : `<span class="text-xs text-[var(--muted)]">—</span>`}
            </div>
            <div class="flex flex-col gap-2">
                <input type="file" id="sig-file" accept="image/png,image/jpeg" class="text-xs text-[var(--muted)]">
                <button type="button" id="sig-remove" class="btn btn-ghost small ${sig ? '' : 'hidden'}">${removeTxt}</button>
            </div>
        </div>
      </div>
    </div>`;

    openModal(t('misc.myProfile'), html, async (fd) => {
        const new_password = fd.get('new_password');
        if (!new_password) { closeModal(); return; }
        try {
            const res = await fetch('/api/auth/change_password', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({new_password})
            });
            if(res.ok) { alert(t('misc.saved')); closeModal(); }
            else { alert(t('misc.loginErrorMsg')); }
        } catch(e) { console.error(e); }
    });

    const saveSignature = async (url) => {
        try {
            const res = await fetch('/api/auth/signature', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ signatureUrl: url })
            });
            if (res.ok) {
                const d = await res.json();
                state.user.signature = d.signature || null;
                const prev = document.getElementById('sig-preview');
                if (prev) prev.innerHTML = state.user.signature ? `<img src="${state.user.signature}" class="max-w-full max-h-full object-contain">` : `<span class="text-xs text-[var(--muted)]">—</span>`;
                const rm = document.getElementById('sig-remove');
                if (rm) rm.classList.toggle('hidden', !state.user.signature);
            } else { alert(srLang ? 'Greška pri čuvanju potpisa.' : 'Error saving signature.'); }
        } catch(e) { console.error(e); }
    };

    const fileInput = document.getElementById('sig-file');
    if (fileInput) fileInput.addEventListener('change', async (e) => {
        const f = e.target.files[0]; if (!f) return;
        const url = await uploadFileToServer(f);
        if (url) await saveSignature(url); else alert(srLang ? 'Otpremanje nije uspelo.' : 'Upload failed.');
        e.target.value = '';
    });
    const rmBtn = document.getElementById('sig-remove');
    if (rmBtn) rmBtn.addEventListener('click', () => saveSignature(''));
}

function checkAllNotifications(){
  state.notifications = []; const now = new Date();
  const warningDays = state.settings.paymentWarningDays || 7;
  
  if(state.data.deals) {
      state.data.deals.forEach(d => {
        const checkDueDate = (dateStr, type) => {
            if(dateStr && (type === 'Kupac' ? !d.buyerPaidOn : !d.supplierPaidOn)){
              const dt = new Date(dateStr); const diff = Math.ceil((dt - now)/(1000*60*60*24));
              if(diff <= warningDays && diff >= 0) state.notifications.push({ type:'payment', message: `${t('notifications.paymentDue')} (${type === 'Kupac' ? t('finances.buyer') : t('finances.supplier')} ${type === 'Kupac' ? escapeHtml(d.buyerName) : escapeHtml(d.supplierName)}): ${escapeHtml(d.contractId || '')} (${diff}d)`, dealId: d.id });
            }
        }; checkDueDate(d.paymentDates?.buyer, 'Kupac'); checkDueDate(d.paymentDates?.supplier, 'Dobavljač');
      });
  }
  
  if(state.data.partners) {
      state.data.partners.forEach(p=>{ const lm = p.lastModified ? new Date(p.lastModified) : null; if(lm && Math.floor((now - lm)/(1000*60*60*24)) > 365) state.notifications.push({ type:'oldPartner', message: `${t('notifications.oldPartner')}: ${escapeHtml(p.companyName)}` }); });
  }
  
  if(state.data.demands) {
      state.data.demands.forEach(d=>{ 
          const cr = d.createdAt ? new Date(d.createdAt) : null; 
          if(cr && Math.floor((now - cr)/(1000*60*60*24)) > 30) state.notifications.push({ type:'oldDemand', message: `${t('notifications.oldDemand')}: ${escapeHtml(getPartnerNameById(d.buyerId) || '')}` }); 
          if(d.isNewProduct && d.productName && state.data.products && state.data.products.some(p => p.name.toLowerCase() === d.productName.toLowerCase())) state.notifications.push({ type:'productAvailable', message: `${t('notifications.productAvailable')}: ${escapeHtml(d.productName)}` }); 
      });
  }
  
  if(state.data.recurringExpenses) {
      state.data.recurringExpenses.forEach(re => { const diff = Math.ceil((new Date(now.getFullYear(), now.getMonth(), Math.min(re.dayOfMonth, 28)) - now)/(1000*60*60*24)); if(diff <= 3 && diff > 0) state.notifications.push({ type:'recurring', message: `${t('notifications.reminder')}: ${escapeHtml(re.description)} (${diff}d)` }); });
  }
  updateNotificationCounter();
}

function updateNotificationCounter(){ 
    const c = document.getElementById('notification-count');
    if(c) c.innerText = state.notifications.length || 0; 
    const l = document.getElementById('notif-label');
    if(l) l.innerText = typeof t === 'function' ? t('notifications.title') : 'Obaveštenja'; 
}

function showNotificationsModal(){
  const listHtml = state.notifications.length ? state.notifications.map(n=> n.dealId ? `<li class="mb-2 hover:bg-[var(--hover-bg)] p-2 rounded transition-colors"><a href="#" class="notification-link font-medium text-blue-500" data-deal-id="${n.dealId}">${escapeHtml(n.message)}</a></li>` : `<li class="mb-2 p-2 border-b border-theme text-main">${escapeHtml(n.message)}</li>`).join('') : `<li class="text-muted p-4 text-center border-2 border-dashed rounded-lg">${t('notifications.noNotifications')}</li>`;
  openModal(t('notifications.title'), `<div><h4 class="font-bold mb-4 text-xl text-accent border-b border-theme pb-2">${t('notifications.title')}</h4><ul>${listHtml}</ul></div>`, null);
  document.querySelectorAll('.notification-link').forEach(link => { link.addEventListener('click', (e) => { e.preventDefault(); closeModal(); state.currentView = 'deals'; render(); if(typeof showDealForm==='function') showDealForm({dealId: e.currentTarget.dataset.dealId}); }); });
}

// === KREIRANJE OFFLINE BANERA ===
function showOfflineBanner() {
    let banner = document.getElementById('offline-warning-banner');
    if(!banner) {
        banner = document.createElement('div');
        banner.id = 'offline-warning-banner';
        banner.className = 'fixed top-0 left-0 w-full bg-red-600 text-white text-center py-2 text-xs font-black tracking-widest uppercase z-[9999] shadow-md flex items-center justify-center gap-3 transition-transform transform -translate-y-full duration-300';
        banner.innerHTML = `<span>⚠️ INTERNET KONEKCIJA JE PREKINUTA. PODACI SE NE ČUVAJU NA SERVERU.</span>`;
        document.body.appendChild(banner);
    }
    requestAnimationFrame(() => banner.classList.remove('-translate-y-full'));
}

function hideOfflineBanner() {
    const banner = document.getElementById('offline-warning-banner');
    if(banner) {
        banner.classList.add('-translate-y-full');
        setTimeout(() => banner.remove(), 300);
        UI.showNotification('Internet konekcija je uspostavljena.', 'success');
    }
}

function setupGlobalListeners(){
  // 1. Očuvanje podataka pri slučajnom zatvaranju prozora (Before Unload)
  window.addEventListener('beforeunload', function (e) {
      const modal = document.getElementById('modal-backdrop');
      // Ako je modal (forma) trenutno otvoren i nije skriven, spreči izlaz
      if (modal && !modal.classList.contains('hidden')) {
          e.preventDefault();
          e.returnValue = ''; // Standard za prikaz upozorenja u Chrome/Edge
      }
  });

  // 2. Nadzor nad internet konekcijom
  window.addEventListener('offline', showOfflineBanner);
  window.addEventListener('online', hideOfflineBanner);

  // 3. Global Error Boundary - Sprečava pad interfejsa
  window.onerror = function(msg, url, lineNo, columnNo, error) {
      console.error('CRITICAL UI ERROR CAUGHT:', msg, error);
      const btn = document.querySelector('#modal-body button[type="submit"]');
      if (btn && btn.disabled) {
          btn.disabled = false;
          btn.innerText = '⚠️ Greška! Pokušajte ponovo.';
      }
      return false; 
  };
  
  // 4. Zabrana slučajnog Enter tastera
  window.addEventListener('keydown', function(e) {
      if(e.key === 'Enter' && e.target.tagName !== 'TEXTAREA' && e.target.tagName !== 'BUTTON' && e.target.tagName !== 'A') {
          e.preventDefault();
          return false;
      }
  });

  const toggleBtn = document.getElementById('toggle-sidebar-btn');
  if(toggleBtn) {
      toggleBtn.addEventListener('click', () => {
          document.body.classList.toggle('sidebar-collapsed');
          localStorage.setItem('aspidus_sidebar_collapsed', document.body.classList.contains('sidebar-collapsed'));
      });
  }

  const sb = document.getElementById('settings-btn');
  if(sb) sb.addEventListener('click', () => { if(typeof SettingsManager !== 'undefined') SettingsManager.showModal(); });
  
  const mcb = document.getElementById('modal-close-btn');
  if(mcb) mcb.addEventListener('click', closeModal);
  
  const mbd = document.getElementById('modal-backdrop');
  if(mbd) mbd.addEventListener('click', (e)=> { if(e.target.id === 'modal-backdrop') { ['invoice-modal-content', 'offer-modal-content'].forEach(id => { const m = document.getElementById(id); if (m) m.id = 'modal-content'; }); closeModal(); } });
  
  document.addEventListener('keydown', (e)=> { if(e.key==='Escape') closeModal(); });
  
  const snb = document.getElementById('show-notifications-btn');
  if(snb) snb.addEventListener('click', showNotificationsModal);
  
  const ifi = document.getElementById('import-file-input');
  if(ifi) ifi.addEventListener('change', (e)=> { if(e.target.files.length > 0) importDatabase(e.target.files[0]); e.target.value = ''; });

  const ici = document.getElementById('import-csv-input');
  if(ici) ici.addEventListener('change', (e)=> { if(e.target.files.length > 0) importPartnersFromCSV(e.target.files[0]); e.target.value = ''; });

  document.addEventListener('createDealFromOffer', (e) => { const { productId, offerIndex } = e.detail; const product = state.data.products.find(p => p.id === productId); const offer = product?.supplyOffers[offerIndex]; if (product && offer && typeof showDealForm==='function') showDealForm({ offerDetails: { ...offer, productId: product.id, productName: product.name } }); });
  document.addEventListener('createCustomerOffer', (e) => { if(typeof showCustomerOfferModal==='function') showCustomerOfferModal(e.detail); });

  document.addEventListener('keyup', (e) => {
      if (e.key === 'PrintScreen') { if(typeof logClientEvent==='function') logClientEvent('SCREENSHOT', 'system', 'PrintScreen key pressed.'); }
  });
  
  const accountBlock = document.getElementById('user-profile-plate');
  if(accountBlock) { accountBlock.addEventListener('click', showProfileModal); }
}

function logClientEvent(action, module, details) {
    fetch('/api/audit/event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, module, details })
    }).catch(e => console.warn('Log error:', e));
}

async function checkAuth() {
    try {
        const res = await fetch('/api/auth/me');
        if (res.ok) { const data = await res.json(); state.user = data.user; return true; }
    } catch (e) {} return false;
}

function localizeStaticHTML() {
    if(typeof t !== 'function') return;
    const setTxt = (id, key) => { const el = document.getElementById(id); if(el) el.innerText = t(key); };
    setTxt('nav-account-label', 'misc.accountLabel');
    setTxt('company-settings-label', 'misc.companySettingsLabel');
    setTxt('logout-btn-txt', 'misc.logoutLabel');
    setTxt('notif-label', 'notifications.title');
}

async function initialize(){
  if(localStorage.getItem('aspidus_sidebar_collapsed') === 'true') {
      document.body.classList.add('sidebar-collapsed');
  }

  const isAuth = await checkAuth();
  if(!isAuth) {
      const ls = document.getElementById('login-screen');
      if(ls) ls.classList.remove('hidden');
      localizeStaticHTML(); return; 
  }
  
  const aw = document.getElementById('app-wrapper');
  if(aw) aw.classList.remove('hidden');
  
  const roleText = state.user.role === 'admin' ? (typeof t === 'function' ? t('users.adminRole').replace('👑 ', '') : 'ADMIN') : (typeof t === 'function' ? t('users.workerRole').replace('👷 ', '') : 'WORKER');
  const cud = document.getElementById('current-user-display');
  if(cud) cud.innerText = `${state.user.username.toUpperCase()} [${roleText.toUpperCase()}]`;
  
  if(state.user.role !== 'admin') {
      const sb = document.getElementById('settings-btn');
      if(sb) sb.classList.add('hidden');
  }

  await loadFromStorage();
  if (!state.data.offers) state.data.offers = [];
  await fetchLiveExchangeRates();
  
  if (state.user.role === 'admin' && typeof applyRecurringExpenses === 'function') {
      await applyRecurringExpenses();
  }
  
  localizeStaticHTML(); 
  resetFilters(); render(); setupGlobalListeners(); checkAllNotifications();
  setInterval(checkAllNotifications, 1000*60*5);
}

document.addEventListener('DOMContentLoaded', () => {
    const lgBtn = document.getElementById('logout-btn');
    if(lgBtn) lgBtn.addEventListener('click', async () => {
        await fetch('/api/auth/logout', { method: 'POST' });
        window.location.reload();
    });
    initialize();
});