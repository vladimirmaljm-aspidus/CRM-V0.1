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
  audit: '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z"/><path d="M9.5 12l1.8 1.8 3.2-3.6"/>',
  portal_activity: '<path d="M3 3v18h18"/><path d="M7 15l4-4 4 3 5-6"/>',
  portal_preview: '<circle cx="12" cy="12" r="3"/><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/>',
  documents: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="15" x2="15" y2="15"/><line x1="9" y1="18" x2="15" y2="18"/>'
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
  { view:'audit', icon:'audit', labelPath:'audit.title', adminOnly: true, permKey: 'audit_view', group: 'admin' },
  { view:'portal_activity', icon:'portal_activity', labelPath:'portalActivity.navLabel', adminOnly: true, permKey: 'portal_activity_view', group: 'admin' },
  { view:'portal_preview', icon:'portal_preview', labelPath:'portalPreview.navLabel', adminOnly: true, permKey: 'portal_preview_manage', group: 'admin' },
  { view:'documents', icon:'documents', labelPath:'documents.navLabel', adminOnly: true, group: 'admin' }
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
          const labelText = item.labelPath.startsWith('nav.') || item.labelPath.startsWith('users.') || item.labelPath.startsWith('audit.') || item.labelPath.startsWith('portalActivity.') || item.labelPath.startsWith('portalPreview.') || item.labelPath.startsWith('documents.') ? t(item.labelPath) : item.labelPath;
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

// Sačuvaj trenutni view + detail id da bi browser refresh zadržao poziciju.
// Vezano za user id kako se sesije ne bi mešale ako se pridobe više korisnika.
function _viewPersistKey() {
    const uid = (state.user && state.user.id) || 'anon';
    return `crm_last_view_${uid}`;
}
function _persistCurrentView() {
    try {
        localStorage.setItem(_viewPersistKey(), JSON.stringify({
            view: state.currentView, detailViewId: state.detailViewId,
            at: Date.now()
        }));
    } catch (e) {}
}
window.restoreLastView = function() {
    try {
        const raw = localStorage.getItem(_viewPersistKey());
        if (!raw) return;
        const saved = JSON.parse(raw);
        // Ako je snimljeno pre više od 8h — ignoriši (verovatno stari session).
        if (!saved || (Date.now() - (saved.at || 0)) > 8 * 3600 * 1000) return;
        if (saved.view) state.currentView = saved.view;
        if (saved.detailViewId) state.detailViewId = saved.detailViewId;
    } catch (e) {}
};

function render() {
  buildNavigation();
  const main = document.getElementById('main-content');
  if(!main) return;
  main.innerHTML = '';
  _persistCurrentView();

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
      else if(state.currentView === 'portal_activity' && (state.user.role === 'admin' || (state.user.permissions && state.user.permissions.portal_activity_view))) { if(typeof renderPortalActivityView==='function') renderPortalActivityView(); }
      else if(state.currentView === 'portal_preview' && (state.user.role === 'admin' || (state.user.permissions && state.user.permissions.portal_preview_manage))) { if(typeof renderPortalPreviewView==='function') renderPortalPreviewView(); }
      else if(state.currentView === 'documents' && state.user.role === 'admin') { if(typeof renderDocumentManagerView==='function') renderDocumentManagerView(); }
      else {
          main.innerHTML = `<div class="p-10 text-center"><h2 class="text-3xl text-red-500 font-bold mb-4">${t('users.accessDenied')}</h2><p class="text-gray-400">${t('users.accessDeniedMsg')}</p></div>`;
      }
  } catch(e) {
      console.error("Render Error:", e);
      main.innerHTML = `<div class="p-10 text-center"><h2 class="text-xl text-red-500 font-bold">Došlo je do greške u prikazu modula. Osvježite stranicu.</h2></div>`;
  }
  
  applyPermissionUIGating();
  updateNotificationCounter();
}

// ==========================================================
//  UI Permission Gating
// ==========================================================
// Cilj: korisnik bez određene permisije NE VIDI dugmiće/opcije koje ne sme da koristi.
// Način rada:
//   1. Elementi u iscrtavanju mogu imati atribut data-req-perm="modul:action" (npr. "deals:edit").
//   2. Ova funkcija ih traži i skriva (display:none) ako trenutni korisnik nema tu permisiju.
//   3. Dodatno, sistematski krije klasične "edit"/"delete"/"add" tastere za module za koje user
//      nema odgovarajuću permisiju - detekcija po sadržaju view-a (currentView).
function applyPermissionUIGating() {
    if (!state.user) return;

    // 1. Elementi sa eksplicitnim data-req-perm
    document.querySelectorAll('[data-req-perm]').forEach(el => {
        const spec = el.getAttribute('data-req-perm') || '';
        // Format: "modul:action" ili "modul:action,modul:action" (bilo koji od navedenih)
        const parts = spec.split(',').map(s => s.trim()).filter(Boolean);
        let allowed = parts.length === 0;
        for (const p of parts) {
            const [mod, act] = p.split(':');
            if (mod && act && typeof hasPerm === 'function' && hasPerm(mod, act)) { allowed = true; break; }
            // Podržava i "flag:permName" za direktnu proveru state.user.permissions[permName]
            if (mod === 'flag' && act && state.user.permissions && state.user.permissions[act]) { allowed = true; break; }
        }
        el.style.display = allowed ? '' : 'none';
    });

    // 2. Ako korisnik NEMA odgovarajuće edit/delete/view-costs permisije za modul,
    //    ukloni klasične dugmiće iz kartica/tabele. Ovo je "fail-safe" u slučaju da
    //    modul ne označava dugmiće sa data-req-perm.
    const view = state.currentView;
    const gate = (selector, mod, act) => {
        if (typeof hasPerm === 'function' && !hasPerm(mod, act)) {
            document.querySelectorAll(selector).forEach(el => { el.style.display = 'none'; });
        }
    };

    if (view === 'deals') {
        gate('.edit-deal, [data-action="edit-deal"]', 'deals', 'edit');
        gate('.del-deal, [data-action="delete-deal"]', 'deals', 'delete');
    }
    if (view === 'products') {
        gate('.edit-product, [data-action="edit-product"]', 'products', 'edit');
        gate('.del-product, [data-action="delete-product"]', 'products', 'delete');
    }
    if (view === 'partners' || view === 'partnerDetail') {
        gate('.edit-partner, [data-action="edit-partner"]', 'partners', 'edit');
        gate('.del-partner, [data-action="delete-partner"]', 'partners', 'delete');
    }
    if (view === 'offers') {
        gate('.edit-offer, [data-action="edit-offer"]', 'offers', 'edit');
        gate('.del-offer, [data-action="delete-offer"]', 'offers', 'delete');
    }
    if (view === 'demands') {
        gate('.edit-demand, [data-action="edit-demand"]', 'products', 'edit');
        gate('.del-demand, [data-action="delete-demand"]', 'products', 'delete');
    }
}
// Izloži za dinamički kreirane modale
window.applyPermissionUIGating = applyPermissionUIGating;

// Reapply gating na svaki modal open (dodaje se posle openModal poziva)
(function() {
    const origOpen = window.openModal;
    if (typeof origOpen === 'function') {
        window.openModal = function() {
            const r = origOpen.apply(this, arguments);
            setTimeout(applyPermissionUIGating, 30);
            return r;
        };
    }
})();

function showProfileModal() {
    const srLang = Utils.getLang() === 'sr';
    const sig = state.user.signature;
    const sigLabel = srLang ? 'Moj potpis (na dokumentima)' : 'My signature (on documents)';
    const sigHint = srLang ? 'Ovaj potpis se koristi ISKLJUČIVO na Vašim dokumentima. Preporuka: PNG sa providnom pozadinom.' : 'This signature is used ONLY on your own documents. Recommended: PNG with transparent background.';
    const removeTxt = srLang ? 'Ukloni potpis' : 'Remove signature';
    const pwHint = srLang ? '(ostavite prazno da ne menjate)' : '(leave blank to keep current)';

    const html = `
    <div class="space-y-6">
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

// ==========================================================
//  DISMISSED NOTIFICATIONS — localStorage per user
// ==========================================================
// Notifikacije se izvode iz podataka pa "delete" u pravom smislu nema smisla
// (na sledeći render bi se ponovo pojavile). Umesto toga pratimo koje su dismiss-ovane
// u localStorage; sledeći put ih preskačemo dok se stanje ne promeni (deal se plati,
// partner osvezi itd.). Ključ se veže za trenutnog korisnika.
function _dismissKey() {
    const uid = (state.user && state.user.id) || 'anon';
    return `crm_dismissed_notifs_${uid}`;
}
function _loadDismissed() {
    try { return new Set(JSON.parse(localStorage.getItem(_dismissKey()) || '[]')); } catch (e) { return new Set(); }
}
function _saveDismissed(setObj) {
    try { localStorage.setItem(_dismissKey(), JSON.stringify([...setObj].slice(-500))); } catch (e) {}
}
function _notifId(n) {
    // Stabilan potpis notifikacije za dismiss listu (bez datuma jer se ponavlja).
    return `${n.type}:${n.dealId || n.goto || ''}:${(n.message || '').replace(/\d+d\)/g, 'Nd)').slice(0, 120)}`;
}
window.dismissNotification = function(id) {
    const set = _loadDismissed(); set.add(id); _saveDismissed(set);
    // Ponovo iscrtaj listu bez ove
    state.notifications = state.notifications.filter(n => _notifId(n) !== id);
    updateNotificationCounter();
    if (typeof showNotificationsModal === 'function') showNotificationsModal();
};
window.dismissAllNotifications = function() {
    const set = _loadDismissed();
    state.notifications.forEach(n => set.add(_notifId(n)));
    _saveDismissed(set);
    state.notifications = [];
    updateNotificationCounter();
    if (typeof closeModal === 'function') closeModal();
    if (typeof showToast === 'function') showToast('All notifications dismissed.', 'success');
};
window.resetDismissedNotifications = function() {
    try { localStorage.removeItem(_dismissKey()); } catch (e) {}
    if (typeof checkAllNotifications === 'function') checkAllNotifications();
    if (typeof showToast === 'function') showToast('Notifications reset.', 'info');
};

function checkAllNotifications(){
  state.notifications = []; const now = new Date();
  const _dismissed = _loadDismissed();
  const _push = (n) => { if (!_dismissed.has(_notifId(n))) _push(n); };
  const warningDays = state.settings.paymentWarningDays || 7;
  
  if(state.data.deals) {
      state.data.deals.forEach(d => {
        const checkDueDate = (dateStr, type) => {
            if(dateStr && (type === 'Kupac' ? !d.buyerPaidOn : !d.supplierPaidOn)){
              const dt = new Date(dateStr); const diff = Math.ceil((dt - now)/(1000*60*60*24));
              if(diff <= warningDays && diff >= 0) _push({ type:'payment', message: `${t('notifications.paymentDue')} (${type === 'Kupac' ? t('finances.buyer') : t('finances.supplier')} ${type === 'Kupac' ? escapeHtml(d.buyerName) : escapeHtml(d.supplierName)}): ${escapeHtml(d.contractId || '')} (${diff}d)`, dealId: d.id });
            }
        }; checkDueDate(d.paymentDates?.buyer, 'Kupac'); checkDueDate(d.paymentDates?.supplier, 'Dobavljač');
      });
  }
  
  if(state.data.partners) {
      state.data.partners.forEach(p=>{ const lm = p.lastModified ? new Date(p.lastModified) : null; if(lm && Math.floor((now - lm)/(1000*60*60*24)) > 365) _push({ type:'oldPartner', message: `${t('notifications.oldPartner')}: ${escapeHtml(p.companyName)}` }); });
  }
  
  if(state.data.demands) {
      state.data.demands.forEach(d=>{ 
          const cr = d.createdAt ? new Date(d.createdAt) : null; 
          if(cr && Math.floor((now - cr)/(1000*60*60*24)) > 30) _push({ type:'oldDemand', message: `${t('notifications.oldDemand')}: ${escapeHtml(getPartnerNameById(d.buyerId) || '')}` }); 
          if(d.isNewProduct && d.productName && state.data.products && state.data.products.some(p => p.name.toLowerCase() === d.productName.toLowerCase())) _push({ type:'productAvailable', message: `${t('notifications.productAvailable')}: ${escapeHtml(d.productName)}` }); 
      });
  }
  
  if(state.data.recurringExpenses) {
      state.data.recurringExpenses.forEach(re => { const diff = Math.ceil((new Date(now.getFullYear(), now.getMonth(), Math.min(re.dayOfMonth, 28)) - now)/(1000*60*60*24)); if(diff <= 3 && diff > 0) _push({ type:'recurring', message: `${t('notifications.reminder')}: ${escapeHtml(re.description)} (${diff}d)` }); });
  }

  // Notifikacije o pending stavkama iz B2B portala (KYC, roba, izmene profila, RFQ).
  // Zove server-side endpoint i dopisuje u istu listu — vidljivo adminu i korisnicima sa partners_edit.
  if (state.user && (state.user.role === 'admin' || (state.user.permissions && state.user.permissions.partners_edit))) {
      fetch('/api/portal/admin/pending_counts').then(r => r.ok ? r.json() : null).then(counts => {
          if (!counts) return;
          const tLang = (sr, en) => Utils.getLang() === 'sr' ? sr : en;
          if (counts.kyc > 0)               _push({ type: 'portal_kyc', message: tLang(`KYC prijave sa portala čekaju pregled: ${counts.kyc}`, `KYC submissions awaiting review: ${counts.kyc}`), goto: 'portal_kyc' });
          if (counts.products > 0)          _push({ type: 'portal_products', message: tLang(`Nova roba sa portala na odobrenje: ${counts.products}`, `New products from partners awaiting approval: ${counts.products}`), goto: 'portal_products' });
          if (counts.profile_requests > 0)  _push({ type: 'portal_profile', message: tLang(`Zahtevi za izmenu profila: ${counts.profile_requests}`, `Profile change requests: ${counts.profile_requests}`), goto: 'portal_profile' });
          if (counts.rfqs > 0)              _push({ type: 'portal_rfq', message: tLang(`Novi RFQ zahtevi sa portala: ${counts.rfqs}`, `New RFQs from portal: ${counts.rfqs}`), goto: 'demands' });
          // Client accept/decline odgovori — svaki nepregledan odgovor je posebna
          // notifikacija sa razlogom (za decline) i akcijom 'Otvori ponudu'.
          (counts.offer_responses_detail || []).forEach(r => {
              const icon = r.status === 'accepted' ? '✅' : '❌';
              const label = r.status === 'accepted'
                  ? tLang(`${icon} ${r.client_name} je PRIHVATIO ponudu ${r.offer_no}`, `${icon} ${r.client_name} ACCEPTED offer ${r.offer_no}`)
                  : tLang(`${icon} ${r.client_name} je ODBIO ponudu ${r.offer_no}${r.note ? ' — Razlog: ' + r.note : ''}`, `${icon} ${r.client_name} DECLINED offer ${r.offer_no}${r.note ? ' — Reason: ' + r.note : ''}`);
              _push({ type: r.status === 'accepted' ? 'offer_accepted' : 'offer_declined',
                      message: label, goto: 'offers', offerId: r.offer_id });
          });
          updateNotificationCounter();
      }).catch(() => {});
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
  const listHtml = state.notifications.length ? state.notifications.map(n => {
      const nid = _notifId(n);
      const cls = 'notification-item group flex items-center justify-between gap-2 p-3 rounded-lg hover:bg-[var(--hover-bg)] border border-transparent hover:border-[var(--border)] transition-colors mb-1.5';
      let attrs = '';
      if (n.dealId) attrs = `data-deal-id="${escapeHtml(n.dealId)}"`;
      else if (n.goto) attrs = `data-goto="${escapeHtml(n.goto)}"`;
      const icon = { 'portal_kyc': '🛡️', 'portal_products': '📦', 'portal_profile': '👤', 'portal_rfq': '📝', 'payment': '💳', 'oldPartner': '⏳', 'oldDemand': '🔎', 'productAvailable': '🎯', 'recurring': '🔁', 'offer_accepted': '✅', 'offer_declined': '❌' }[n.type] || '•';
      return `<div class="${cls}" ${attrs}>
        <button class="flex items-center flex-1 min-w-0 text-left cursor-pointer notif-open-btn">
          <span class="mr-2">${icon}</span><span class="text-sm text-main break-words">${escapeHtml(n.message)}</span>
        </button>
        <button class="notif-dismiss-btn text-[var(--muted)] hover:text-red-600 opacity-70 group-hover:opacity-100 text-lg leading-none flex-shrink-0 w-7 h-7 rounded hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                data-nid="${escapeHtml(nid)}" title="Dismiss">×</button>
      </div>`;
  }).join('') : `<div class="text-[var(--muted)] p-6 text-center border-2 border-dashed border-[var(--border)] rounded-xl text-sm">${t('notifications.noNotifications')}</div>`;
  const actionsBar = state.notifications.length ? `
    <div class="flex items-center justify-between gap-2 mb-3 pb-3 border-b border-[var(--border)]">
      <span class="text-xs text-[var(--muted)]">${state.notifications.length} ${state.notifications.length === 1 ? 'notification' : 'notifications'}</span>
      <div class="flex gap-2">
        <button onclick="window.dismissAllNotifications()" class="text-xs font-bold text-red-600 hover:text-red-700 hover:underline">Dismiss all</button>
        <button onclick="window.resetDismissedNotifications()" class="text-xs font-medium text-blue-600 hover:underline">Reset hidden</button>
      </div>
    </div>` : '';
  openModal(t('notifications.title'), `<div>${actionsBar}<div class="space-y-1">${listHtml}</div></div>`, null);
  // Dismiss buttons
  document.querySelectorAll('.notif-dismiss-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
          e.stopPropagation();
          window.dismissNotification(btn.dataset.nid);
      });
  });
  document.querySelectorAll('.notification-item').forEach(link => {
      link.addEventListener('click', (e) => {
          const dealId = e.currentTarget.dataset.dealId;
          const goto = e.currentTarget.dataset.goto;
          closeModal();
          if (dealId) {
              state.currentView = 'deals'; render();
              if (typeof showDealForm === 'function') showDealForm({ dealId });
          } else if (goto === 'demands') {
              state.currentView = 'demands'; render();
          } else if (goto === 'portal_profile' && typeof showPortalPendingModal === 'function') {
              showPortalPendingModal('profile_requests');
          } else if (goto === 'portal_kyc' && typeof showPortalPendingModal === 'function') {
              showPortalPendingModal('kyc');
          } else if (goto === 'portal_products' && typeof showPortalPendingModal === 'function') {
              showPortalPendingModal('products');
          } else if (goto === 'offers') {
              state.currentView = 'offers'; render();
              // Ako je bio offer_response tip, obeleži server-side da je admin pregledao,
              // a zatim ako postoji funkcija za otvaranje offer detalja pokrenemo je.
              const offerId = e.currentTarget.dataset.offerId;
              if (offerId) {
                  fetch(`/api/portal/admin/offers/mark_seen/${offerId}`, { method: 'POST' })
                      .catch(() => {});
                  setTimeout(() => {
                      if (typeof showOfferDetail === 'function') showOfferDetail(offerId);
                  }, 100);
              }
          }
      });
  });
}

// ==========================================================
//  ADMIN: pregled i odobravanje pending stavki iz portala
// ==========================================================
async function showPortalPendingModal(kind) {
    const srLang = Utils.getLang() === 'sr';
    const titles = {
        'profile_requests': srLang ? '👤 Zahtevi za izmenu profila (Portal)' : '👤 Profile Change Requests (Portal)',
        'kyc': srLang ? '🛡️ KYC prijave sa portala' : '🛡️ KYC Submissions (Portal)',
        'products': srLang ? '📦 Nova roba sa portala' : '📦 New Products from Portal'
    };
    openModal(titles[kind] || 'Portal', `<div class="p-8 text-center text-[var(--muted)] text-sm">${srLang ? 'Učitavanje…' : 'Loading…'}</div>`, null);

    let html = '';
    if (kind === 'profile_requests') {
        const res = await fetch('/api/portal/admin/profile_requests?status=pending');
        const list = res.ok ? await res.json() : [];
        if (list.length === 0) {
            html = `<div class="p-8 text-center text-[var(--muted)] text-sm">${srLang ? 'Nema zahteva na čekanju.' : 'No pending requests.'}</div>`;
        } else {
            html = list.map(r => {
                const changes = Object.entries(r.changes || {}).filter(([k]) => k !== 'note').map(([k, v]) => {
                    const oldKey = 'current' + k.charAt(0).toUpperCase() + k.slice(1);
                    const oldVal = r.current?.[oldKey] || '—';
                    return `<div class="text-xs text-slate-600 mb-1"><span class="font-semibold text-slate-800">${escapeHtml(k)}:</span> <s class="text-red-500">${escapeHtml(oldVal)}</s> → <strong class="text-emerald-700">${escapeHtml(v)}</strong></div>`;
                }).join('');
                return `<div class="p-4 border border-[var(--border)] rounded-lg mb-3 bg-white">
                    <div class="flex justify-between items-start mb-2">
                        <div><h4 class="font-semibold text-sm text-main">${escapeHtml(r.partner_name)}</h4><p class="text-[10px] text-[var(--muted)] uppercase tracking-wide">${r.submitted_at ? new Date(r.submitted_at).toLocaleString() : ''}</p></div>
                        <span class="text-[10px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 border border-amber-200 font-semibold">${srLang ? 'NA ČEKANJU' : 'PENDING'}</span>
                    </div>
                    ${changes}
                    ${r.changes?.note ? `<div class="text-xs italic text-slate-500 mt-2 p-2 bg-slate-50 rounded">"${escapeHtml(r.changes.note)}"</div>` : ''}
                    <div class="flex gap-2 mt-3 justify-end">
                        <button class="btn btn-ghost small text-xs" data-req="${escapeHtml(r.id)}" data-action="reject">${srLang ? 'Odbij' : 'Reject'}</button>
                        <button class="btn btn-primary small text-xs" data-req="${escapeHtml(r.id)}" data-action="approve">${srLang ? 'Odobri i primeni' : 'Approve & apply'}</button>
                    </div>
                </div>`;
            }).join('');
        }
    } else if (kind === 'kyc') {
        // Postojeci endpoint /api/portal/admin/submissions/all
        const res = await fetch('/api/portal/admin/submissions/all');
        const list = res.ok ? await res.json() : [];
        if (list.length === 0) {
            html = `<div class="p-8 text-center text-[var(--muted)] text-sm">${srLang ? 'Nema KYC prijava.' : 'No KYC submissions.'}</div>`;
        } else {
            html = list.map(s => `
                <div class="p-4 border border-[var(--border)] rounded-lg mb-3 bg-white">
                    <div class="flex justify-between items-start mb-1">
                        <div><h4 class="font-semibold text-sm text-main">${escapeHtml(s.partner_name)}</h4><p class="text-[10px] text-[var(--muted)] uppercase">${s.submitted_at ? new Date(s.submitted_at).toLocaleString() : ''}</p></div>
                        <div class="flex gap-2">
                            <button class="btn btn-primary small text-xs" data-kyc="${escapeHtml(s.id)}" data-partner="${escapeHtml(s.partner_id)}">${srLang ? 'Otvori u KYC modulu' : 'Open in KYC'}</button>
                        </div>
                    </div>
                    <div class="text-xs text-slate-600">${escapeHtml(s.data?.companyName || '')} · ${escapeHtml(s.data?.taxId || '')}</div>
                </div>
            `).join('');
        }
    } else if (kind === 'products') {
        const res = await fetch('/api/portal/admin/products');
        const list = res.ok ? await res.json() : [];
        const pending = list.filter(p => p.status === 'pending');
        if (pending.length === 0) {
            html = `<div class="p-8 text-center text-[var(--muted)] text-sm">${srLang ? 'Nema robe na čekanju.' : 'No products awaiting approval.'}</div>`;
        } else {
            html = pending.map(p => {
                const off = (p.data.supplyOffers && p.data.supplyOffers[0]) || {};
                return `
                <div class="p-4 border border-[var(--border)] rounded-lg mb-3 bg-white">
                    <div class="flex justify-between items-start">
                        <div>
                            <h4 class="font-semibold text-sm text-main">${escapeHtml(p.data.name || '')}</h4>
                            <p class="text-xs text-[var(--muted)] mt-0.5">${escapeHtml(p.partner_name)} · ${escapeHtml(p.data.sku || '')}</p>
                            <p class="text-xs text-emerald-700 mt-1 font-semibold">${off.price || 0} ${escapeHtml(off.currency || '')} / ${escapeHtml(off.unit || '')}</p>
                        </div>
                        <div class="flex gap-2">
                            <button class="btn btn-ghost small text-xs" data-prod="${escapeHtml(p.id)}" data-action="reject">${srLang ? 'Odbij' : 'Reject'}</button>
                            <button class="btn btn-primary small text-xs" data-prod="${escapeHtml(p.id)}" data-action="approve">${srLang ? 'Odobri' : 'Approve'}</button>
                        </div>
                    </div>
                </div>`;
            }).join('');
        }
    }

    // Ubaci HTML u modal body
    const body = document.getElementById('modal-body');
    if (body) body.innerHTML = `<div>${html}</div>`;

    // Event handlers
    document.querySelectorAll('[data-req]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const req = e.currentTarget.dataset.req;
            const action = e.currentTarget.dataset.action;
            if (!confirm(action === 'approve' ? (srLang ? 'Odobriti i primeniti izmene?' : 'Approve and apply changes?') : (srLang ? 'Odbiti zahtev?' : 'Reject request?'))) return;
            const r = await fetch(`/api/portal/admin/profile_requests/${req}/review`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ action }) });
            if (r.ok) {
                showPortalPendingModal('profile_requests');
                if (typeof loadFromStorage === 'function') await loadFromStorage();
                checkAllNotifications();
            } else {
                alert(srLang ? 'Greška.' : 'Error.');
            }
        });
    });
    document.querySelectorAll('[data-prod]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const pid = e.currentTarget.dataset.prod;
            const action = e.currentTarget.dataset.action;
            if (!confirm(action === 'approve' ? (srLang ? 'Odobriti ovaj proizvod?' : 'Approve this product?') : (srLang ? 'Odbiti proizvod?' : 'Reject product?'))) return;
            const r = await fetch(`/api/portal/admin/products/review/${pid}`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ action }) });
            if (r.ok) {
                showPortalPendingModal('products');
                if (typeof loadFromStorage === 'function') await loadFromStorage();
                checkAllNotifications();
            }
        });
    });
    document.querySelectorAll('[data-kyc]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const partnerId = e.currentTarget.dataset.partner;
            closeModal();
            if (typeof reviewKYC === 'function') { reviewKYC(partnerId); }
            else if (partnerId) { state.currentView = 'partnerDetail'; state.detailViewId = partnerId; render(); }
        });
    });
}
window.showPortalPendingModal = showPortalPendingModal;

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

  // REFRESH — čuva state.currentView i re-učitava sve podatke sa servera.
  // Ne diramo view/detailViewId, samo osvežimo podatke i re-renderujemo.
  async function refreshData(sourceEl) {
      const btn = sourceEl || document.getElementById('crm-refresh-btn');
      if (btn) btn.classList.add('animate-spin');   // rotira ikonu dok se učitava
      if (typeof showLoader === 'function') showLoader((typeof t === 'function' ? (t('loader.refreshing') || 'Refreshing…') : 'Refreshing…'));
      try {
          await loadFromStorage();
          if (typeof fetchLiveExchangeRates === 'function') await fetchLiveExchangeRates();
          checkAllNotifications();
          render();
          if (typeof showToast === 'function') showToast((typeof t === 'function' ? (t('loader.refreshed') || 'Data refreshed.') : 'Data refreshed.'), 'success', 2500);
      } catch (e) {
          if (typeof showToast === 'function') showToast('Refresh failed: ' + (e.message || 'unknown'), 'error');
      } finally {
          if (typeof hideLoader === 'function') hideLoader();
          if (btn) btn.classList.remove('animate-spin');
      }
  }
  const cr = document.getElementById('crm-refresh-btn');
  if (cr) cr.addEventListener('click', () => refreshData(cr.querySelector('svg') || cr));
  const mcr = document.getElementById('mobile-refresh-btn');
  if (mcr) mcr.addEventListener('click', () => refreshData(mcr.querySelector('svg') || mcr));
  // Keyboard shortcut: Ctrl+R prihvata browser refresh, ali dodajmo alt+R kao brz alias
  document.addEventListener('keydown', (e) => {
      if (e.altKey && (e.key === 'r' || e.key === 'R')) { e.preventDefault(); refreshData(); }
  });
  window.refreshCrmData = refreshData;   // dostupno drugim modulima
  
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

  // Prikaži profesionalan full-screen loader dok se povlače svi podaci —
  // korisnik jasno vidi da aplikacija radi umesto praznog belog ekrana.
  if (typeof showLoader === 'function') showLoader(typeof t === 'function' ? (t('loader.loadingData') || 'Loading your workspace…') : 'Loading your workspace…');
  try {
      await loadFromStorage();
      if (!state.data.offers) state.data.offers = [];
      await fetchLiveExchangeRates();
      if (state.user.role === 'admin' && typeof applyRecurringExpenses === 'function') {
          await applyRecurringExpenses();
      }
  } finally {
      if (typeof hideLoader === 'function') hideLoader();
  }

  // Vrati poslednji view koji je korisnik gledao (localStorage) — tako da
  // browser refresh (F5) ne baca korisnika na 'deals' početnu stranu.
  if (typeof restoreLastView === 'function') restoreLastView();

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