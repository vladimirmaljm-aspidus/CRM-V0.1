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

  // Notifikacije o pending stavkama iz B2B portala (KYC, roba, izmene profila, RFQ).
  // Zove server-side endpoint i dopisuje u istu listu — vidljivo adminu i korisnicima sa partners_edit.
  if (state.user && (state.user.role === 'admin' || (state.user.permissions && state.user.permissions.partners_edit))) {
      fetch('/api/portal/admin/pending_counts').then(r => r.ok ? r.json() : null).then(counts => {
          if (!counts) return;
          const tLang = (sr, en) => Utils.getLang() === 'sr' ? sr : en;
          if (counts.kyc > 0)               state.notifications.push({ type: 'portal_kyc', message: tLang(`KYC prijave sa portala čekaju pregled: ${counts.kyc}`, `KYC submissions awaiting review: ${counts.kyc}`), goto: 'portal_kyc' });
          if (counts.products > 0)          state.notifications.push({ type: 'portal_products', message: tLang(`Nova roba sa portala na odobrenje: ${counts.products}`, `New products from partners awaiting approval: ${counts.products}`), goto: 'portal_products' });
          if (counts.profile_requests > 0)  state.notifications.push({ type: 'portal_profile', message: tLang(`Zahtevi za izmenu profila: ${counts.profile_requests}`, `Profile change requests: ${counts.profile_requests}`), goto: 'portal_profile' });
          if (counts.rfqs > 0)              state.notifications.push({ type: 'portal_rfq', message: tLang(`Novi RFQ zahtevi sa portala: ${counts.rfqs}`, `New RFQs from portal: ${counts.rfqs}`), goto: 'demands' });
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
      const cls = 'notification-item block p-3 rounded-lg hover:bg-[var(--hover-bg)] cursor-pointer border border-transparent hover:border-[var(--border)] transition-colors mb-1.5';
      let attrs = '';
      if (n.dealId) attrs = `data-deal-id="${escapeHtml(n.dealId)}"`;
      else if (n.goto) attrs = `data-goto="${escapeHtml(n.goto)}"`;
      const icon = { 'portal_kyc': '🛡️', 'portal_products': '📦', 'portal_profile': '👤', 'portal_rfq': '📝', 'payment': '💳', 'oldPartner': '⏳', 'oldDemand': '🔎', 'productAvailable': '🎯', 'recurring': '🔁' }[n.type] || '•';
      return `<div class="${cls}" ${attrs}>
        <span class="mr-2">${icon}</span><span class="text-sm text-main">${escapeHtml(n.message)}</span>
      </div>`;
  }).join('') : `<div class="text-[var(--muted)] p-6 text-center border-2 border-dashed border-[var(--border)] rounded-xl text-sm">${t('notifications.noNotifications')}</div>`;
  openModal(t('notifications.title'), `<div class="space-y-1">${listHtml}</div>`, null);
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