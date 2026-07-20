// static/js/modules/partners/partners_form.js
function showPartnerForm(id=null){
    state.editingItem = id ? state.data.partners.find(p => p.id === id) : null;
    const item = state.editingItem || {};
    const social = item.social || {};
    const tagsStr = (item.tags || []).join(', ');
    
    const tLang = (srStr, enStr) => Utils.getLang() === 'sr' ? srStr : enStr;
    
    const rolesData = [
        { key: 'buyer', legacy: 'Kupac', label: Utils.t('finances.buyer') },
        { key: 'supplier', legacy: 'Dobavljač', label: Utils.t('finances.supplier') },
        { key: 'associate', legacy: 'Saradnik', label: 'Saradnik / Associate' }
    ];
    const typesHtml = rolesData.map(role => {
        const isChecked = (item.types || []).includes(role.key) || (item.types || []).includes(role.legacy);
        return `<label class="inline-flex items-center mr-4 p-2 bg-[var(--panel)] border border-[var(--border)] rounded cursor-pointer hover:bg-[var(--hover-bg)] transition-colors"><input type="checkbox" name="types" value="${role.key}" ${isChecked ? 'checked':''} class="mr-2 w-4 h-4 text-blue-600"><span class="text-main font-medium">${role.label}</span></label>`;
    }).join('');
    
    const entityTypeHtml = `
      <div class="flex gap-4 mb-4 border-b border-[var(--border)] pb-4">
        <label class="inline-flex items-center cursor-pointer p-2 bg-[var(--panel)] rounded border border-[var(--border)] hover:bg-[var(--hover-bg)]"><input type="radio" name="entityType" value="company" ${item.entityType !== 'person' ? 'checked' : ''} class="mr-2 w-4 h-4 text-blue-600"> <span class="text-main font-bold">🏢 ${Utils.t('fields.company')}</span></label>
        <label class="inline-flex items-center cursor-pointer p-2 bg-[var(--panel)] rounded border border-[var(--border)] hover:bg-[var(--hover-bg)]"><input type="radio" name="entityType" value="person" ${item.entityType === 'person' ? 'checked' : ''} class="mr-2 w-4 h-4 text-blue-600"> <span class="text-main font-bold">👤 ${Utils.t('fields.person')}</span></label>
      </div>
    `;
    const linkedCompanyOptions = `<option value="">${Utils.t('fields.noLink')}</option>` + state.data.partners.filter(p => p.entityType !== 'person' && p.id !== item.id).map(p => `<option value="${p.id}" ${item.linkedCompanyId === p.id ? 'selected' : ''}>${Utils.escapeHtml(p.companyName)}</option>`).join('');
    
    const html = `
    <form id="partner-form" class="space-y-4">
      ${entityTypeHtml}
      
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4 border-b border-[var(--border)] pb-4">
          <div class="md:col-span-2">
              <label id="name-label" class="block text-sm font-bold text-main">${item.entityType === 'person' ? Utils.t('fields.fullName') : Utils.t('fields.companyName')}</label>
              <input name="companyName" class="form-input mt-1 border-blue-400" value="${Utils.escapeHtml(item.companyName || '')}" required />
          </div>
          <div>
              <label class="block text-sm font-bold text-main">${Utils.t('fields.statusLabel')}</label>
              <select name="status" class="form-input mt-1 font-bold">
                  <option value="active" ${item.status === 'active' || !item.status ? 'selected' : ''}>${Utils.t('fields.active')}</option>
                  <option value="inactive" ${item.status === 'inactive' ? 'selected' : ''}>${Utils.t('fields.inactive')}</option>
                  <option value="blacklisted" ${item.status === 'blacklisted' ? 'selected' : ''}>${Utils.t('fields.blacklisted')}</option>
              </select>
          </div>
      </div>
      <div id="linked-company-wrapper" class="${item.entityType === 'person' ? '' : 'hidden'} mt-2 mb-4 bg-blue-50 dark:bg-blue-900/10 p-4 rounded-lg border border-blue-200 shadow-inner">
          <label class="block text-sm font-bold text-blue-700 dark:text-blue-400 mb-1">${Utils.t('fields.linkedCompanyEntity')}</label>
          <select name="linkedCompanyId" class="form-input border-blue-300">${linkedCompanyOptions}</select>
      </div>
      
      <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div>
              <label class="block text-sm font-bold text-main">${Utils.t('fields.taxId')}</label>
              <div class="flex gap-1 mt-1">
                  <input name="taxId" id="partner-tax-id" class="form-input flex-1" value="${Utils.escapeHtml(item.taxId || '')}" placeholder="e.g. DE123456789" />
                  <button type="button" id="partner-vies-check" class="bg-blue-500 hover:bg-blue-600 text-white text-xs font-bold px-2 py-1 rounded" title="Validate on EU VIES">🇪🇺</button>
              </div>
              <div id="partner-vies-status" class="text-xs mt-1" style="min-height:1em;"></div>
          </div>
          <div><label class="block text-sm font-bold text-main">${Utils.t('fields.regNumber')}</label><input name="regNumber" class="form-input mt-1" value="${Utils.escapeHtml(item.regNumber || '')}" /></div>
          <div>
              <label class="block text-sm font-bold text-main">${tLang('Izvor / Preporuka', 'Lead Source')}</label>
              <input name="leadSource" class="form-input mt-1" value="${Utils.escapeHtml(item.leadSource || '')}" placeholder="${tLang('npr. LinkedIn...', 'e.g. LinkedIn...')}" />
          </div>
          <div>
              <label class="block text-sm font-bold text-main">${Utils.t('fields.ratingLabel')}</label>
              <select name="rating" class="form-input mt-1 text-yellow-600 font-bold">
                  <option value="0" ${item.rating == 0 || !item.rating ? 'selected' : ''}>${Utils.t('fields.ratingNone')}</option>
                  <option value="1" ${item.rating == 1 ? 'selected' : ''}>${Utils.t('fields.rating1')}</option>
                  <option value="2" ${item.rating == 2 ? 'selected' : ''}>${Utils.t('fields.rating2')}</option>
                  <option value="3" ${item.rating == 3 ? 'selected' : ''}>${Utils.t('fields.rating3')}</option>
                  <option value="4" ${item.rating == 4 ? 'selected' : ''}>${Utils.t('fields.rating4')}</option>
                  <option value="5" ${item.rating == 5 ? 'selected' : ''}>${Utils.t('fields.rating5')}</option>
              </select>
          </div>
      </div>
      
      <div>
          <label class="block text-sm font-bold text-main mt-2">🏷️ ${tLang('Tagovi (Oznake) - Odvojeni zarezom', 'Tags - Comma separated')}</label>
          <input name="tags" class="form-input mt-1 border-indigo-300" value="${Utils.escapeHtml(tagsStr)}" placeholder="VIP, Wholesale, ..."/>
      </div>
      
      <fieldset class="border border-[var(--border)] p-4 rounded-xl bg-[var(--card)] mt-4"><legend class="text-sm px-2 font-black text-accent uppercase tracking-wider">${Utils.t('fields.addressInfo')}</legend>
        <div class="space-y-4">
            <div><label class="block text-sm font-bold text-main">${Utils.t('fields.street')}</label><input name="street" class="form-input mt-1" value="${Utils.escapeHtml(item.address?.street || '')}" /></div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div><label class="block text-sm font-bold text-main">${Utils.t('fields.country')}</label><div class="autocomplete-container"><input name="country" id="country-input" class="form-input mt-1" value="${Utils.escapeHtml(item.address?.country || '')}" autocomplete="off" /></div></div>
                <div><label class="block text-sm font-bold text-main">${Utils.t('fields.city')}</label><div class="autocomplete-container"><input name="city" id="city-input" class="form-input mt-1" value="${Utils.escapeHtml(item.address?.city || '')}" autocomplete="off" /></div></div>
                <div><label class="block text-sm font-bold text-main">${Utils.t('fields.zip')}</label><input name="zip" class="form-input mt-1" value="${Utils.escapeHtml(item.address?.zip || '')}" /></div>
            </div>
        </div>
      </fieldset>
      
      <fieldset class="border border-[var(--border)] p-4 rounded-xl bg-[var(--card)] mt-4"><legend class="text-sm px-2 font-black text-green-500 uppercase tracking-wider">${Utils.t('fields.contactInfo')}</legend>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div><label class="block text-sm font-bold text-main">${Utils.t('fields.contactPerson')}</label><input name="contactPerson" class="form-input mt-1" value="${Utils.escapeHtml(item.contact?.person || '')}" /></div>
            <div><label class="block text-sm font-bold text-main">${Utils.t('fields.contactEmail')}</label><input name="contactEmail" type="email" class="form-input mt-1" value="${Utils.escapeHtml(item.contact?.email || '')}" /></div>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div><label class="block text-sm font-bold text-main">${Utils.t('fields.phone')}</label><input name="phone" class="form-input mt-1 border-green-300" value="${Utils.escapeHtml(item.contact?.phone || '')}" /></div>
            <div><label class="block text-sm font-bold text-main">${Utils.t('fields.whatsapp')}</label><input name="whatsapp" class="form-input mt-1 border-green-300" placeholder="+..." value="${Utils.escapeHtml(item.contact?.whatsapp || '')}" /></div>
            <div><label class="block text-sm font-bold text-main">${Utils.t('fields.website')}</label><input name="website" class="form-input mt-1" placeholder="www..." value="${Utils.escapeHtml(item.contact?.website || '')}" /></div>
        </div>
      </fieldset>

      <fieldset class="border border-[var(--border)] p-4 rounded-xl bg-[var(--card)] mt-4">
          <legend class="text-sm px-2 font-black text-pink-500 uppercase tracking-wider">🌐 ${tLang('Društvene Mreže', 'Social Media')}</legend>
          <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div><label class="block text-xs font-bold text-[var(--muted)] uppercase mb-1">LinkedIn</label><input name="social_linkedin" class="form-input text-sm" value="${Utils.escapeHtml(social.linkedin || '')}" placeholder="https://linkedin.com/..." /></div>
              <div><label class="block text-xs font-bold text-[var(--muted)] uppercase mb-1">Facebook</label><input name="social_facebook" class="form-input text-sm" value="${Utils.escapeHtml(social.facebook || '')}" placeholder="https://facebook.com/..." /></div>
              <div><label class="block text-xs font-bold text-[var(--muted)] uppercase mb-1">Twitter / X</label><input name="social_twitter" class="form-input text-sm" value="${Utils.escapeHtml(social.twitter || '')}" placeholder="https://twitter.com/..." /></div>
          </div>
      </fieldset>
      
      <fieldset class="border border-[var(--border)] p-4 rounded-xl bg-[var(--card)] mt-4"><legend class="text-sm px-2 font-black text-yellow-500 uppercase tracking-wider">${Utils.t('fields.bankInfo')}</legend>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div><label class="block text-sm font-bold text-main">${Utils.t('fields.bankName')}</label><input name="bankName" class="form-input mt-1" value="${Utils.escapeHtml(item.bank?.name || '')}" /></div>
            <div><label class="block text-sm font-bold text-main">${Utils.t('fields.accountNumber')}</label><input name="accountNumber" class="form-input mt-1" value="${Utils.escapeHtml(item.bank?.accountNumber || '')}" /></div>
            <div><label class="block text-sm font-bold text-main">${Utils.t('fields.swift')}</label><input name="swift" class="form-input mt-1" value="${Utils.escapeHtml(item.bank?.swift || '')}" /></div>
        </div>
      </fieldset>
      
      <div class="bg-[var(--card)] p-4 rounded-xl border border-[var(--border)] mt-4"><label class="block text-sm font-black text-main mb-3 uppercase tracking-wider">${Utils.t('fields.types')}</label><div class="flex flex-wrap gap-2">${typesHtml}</div></div>
      
      <div class="mt-4"><label class="block text-sm font-bold text-main">${Utils.t('fields.notes')}</label><textarea name="notes" class="form-input mt-1" rows="3">${Utils.escapeHtml(item.notes || '')}</textarea></div>
      
      <div class="flex justify-end pt-2 mt-4"><button class="btn bg-accent text-white shadow-lg text-lg px-8 py-2 w-full sm:w-auto" type="submit">${Utils.t('actions.saveChanges')}</button></div>
    </form>`;
    
    Utils.openModal(state.editingItem ? Utils.t('actions.edit') : Utils.t('add.partner'), html, async (fd) => {
        const id = state.editingItem?.id || Utils.generateId();

        // HARD BLOCK: ako je zemlja EU i taxId je popunjen, MORA da prođe VIES.
        // Ako sistem nije uspeo da dokaže da je VAT validan (nikad kliknut check
        // ili je VIES vratio invalid), zaustavljamo save. Time se sprečava da
        // radnik sačuva partnera sa neispravnim/lažnim VAT brojem koji bi kasnije
        // pokvario invoice sa VIES-nekonzistentnim podacima.
        const taxIdVal = String(fd.get('taxId') || '').replace(/\s|-/g, '').toUpperCase();
        const EU_ISO2 = ['AT','BE','BG','CY','CZ','DE','DK','EE','EL','ES','FI','FR','HR','HU',
                         'IE','IT','LT','LU','LV','MT','NL','PL','PT','RO','SE','SI','SK','GR','XI'];
        const looksEuVat = taxIdVal.length >= 4 && EU_ISO2.includes(taxIdVal.slice(0,2));
        if (looksEuVat) {
            const viesStatus = document.getElementById('partner-vies-status');
            // Pokreni proveru automatski (bez klika) ako je user preskočio
            const alreadyValid = viesStatus && viesStatus.textContent.startsWith('✓ Valid');
            if (!alreadyValid) {
                if (viesStatus) { viesStatus.textContent = '⏳ Running VIES validation before save…'; viesStatus.style.color = '#6b7280'; }
                let viesRes;
                try {
                    const r = await fetch('/api/geo/vat/validate', {
                        method: 'POST', headers: {'Content-Type':'application/json'},
                        body: JSON.stringify({vat_number: taxIdVal})
                    });
                    viesRes = await r.json();
                } catch (e) {
                    viesRes = null;
                }
                if (!viesRes || (!viesRes.valid && viesRes.error !== 'service_unavailable')) {
                    if (viesStatus) {
                        viesStatus.textContent = '✗ ' + (viesRes?.message || 'VIES rejected this VAT — cannot save partner');
                        viesStatus.style.color = '#dc2626';
                    }
                    const inp = document.getElementById('partner-tax-id');
                    if (inp) {
                        inp.scrollIntoView({behavior:'smooth', block:'center'});
                        inp.style.borderColor = '#dc2626';
                        inp.style.boxShadow = '0 0 0 3px rgba(220,38,38,.15)';
                        setTimeout(()=>{ inp.style.borderColor=''; inp.style.boxShadow=''; }, 4000);
                        inp.focus();
                    }
                    if (typeof showToast === 'function')
                        showToast(`✗ VAT ${taxIdVal} rejected by EU VIES. Fix or clear the tax ID.`, 'error', 8000);
                    return; // BLOKIRA save
                }
                if (viesRes.error === 'service_unavailable' && typeof showToast === 'function') {
                    showToast('⚠ VIES service temporarily down — VAT could not be verified. Saving without validation.', 'warning', 6000);
                }
                if (viesRes.valid && viesStatus) {
                    viesStatus.textContent = `✓ Valid — ${viesRes.name || ''}`;
                    viesStatus.style.color = '#059669';
                }
            }
        }

        const tagsInput = fd.get('tags') || '';
        const tagsArr = tagsInput.split(',').map(t => t.trim()).filter(t => t !== '');

        const partner = {
            id,
            companyName: fd.get('companyName'),
            entityType: fd.get('entityType') || 'company',
            linkedCompanyId: fd.get('linkedCompanyId') || null,
            status: fd.get('status') || 'active',
            rating: parseInt(fd.get('rating') || 0, 10),
            taxId: fd.get('taxId'),
            regNumber: fd.get('regNumber'),
            leadSource: fd.get('leadSource'),
            tags: tagsArr,
            address: { street: fd.get('street'), city: fd.get('city'), zip: fd.get('zip'), country: fd.get('country') },
            contact: { person: fd.get('contactPerson'), email: fd.get('contactEmail'), phone: fd.get('phone'), whatsapp: fd.get('whatsapp'), website: fd.get('website') },
            bank: { name: fd.get('bankName'), accountNumber: fd.get('accountNumber'), swift: fd.get('swift') },
            social: { linkedin: fd.get('social_linkedin'), facebook: fd.get('social_facebook'), twitter: fd.get('social_twitter') },
            types: fd.getAll('types'),
            notes: fd.get('notes'),
            
            kyc: state.editingItem?.kyc || { status: 'pending', riskLevel: 'low', documents: [] },
            metadata: state.editingItem?.metadata || { createdAt: new Date().toISOString() },
            documents: state.editingItem?.documents || [],
            activities: state.editingItem?.activities || [],
            ownerId: state.editingItem?.ownerId || state.user?.id || 'SYSTEM',
            sharedWith: state.editingItem?.sharedWith || [],
            lastModified: new Date().toISOString()
        };
        
        if(state.editingItem) {
            state.data.partners[state.data.partners.findIndex(p => p.id === id)] = partner;
        } else {
            state.data.partners.push(partner);
        }
        
        await saveSingleItem('partners', partner);
        Utils.closeModal();
        state.currentView = 'partnerDetail';
        state.detailViewId = id;
        render();
    });
    
    const form = document.getElementById('partner-form');

    // VIES VAT validation — button trigger + auto-run on blur when it looks like EU VAT
    const viesBtn = document.getElementById('partner-vies-check');
    const taxInp = document.getElementById('partner-tax-id');
    const viesStatus = document.getElementById('partner-vies-status');
    if (viesBtn && taxInp && viesStatus) {
        const _EU = ['AT','BE','BG','CY','CZ','DE','DK','EE','EL','ES','FI','FR','HR','HU',
                     'IE','IT','LT','LU','LV','MT','NL','PL','PT','RO','SE','SI','SK','GR','XI'];
        // Auto-run on blur ako izgleda kao EU VAT — korisnik ne mora nikad ni da klikne
        taxInp.addEventListener('blur', () => {
            const v = (taxInp.value || '').replace(/\s|-/g,'').toUpperCase();
            if (v.length >= 4 && _EU.includes(v.slice(0,2))) {
                viesBtn.click();
            }
        });
        viesBtn.addEventListener('click', async () => {
            const raw = (taxInp.value || '').trim();
            if (!raw) { viesStatus.textContent = 'Enter VAT number first (e.g. DE123456789)'; viesStatus.style.color = '#dc2626'; return; }
            viesBtn.disabled = true;
            viesStatus.textContent = '⏳ Querying VIES…';
            viesStatus.style.color = '#6b7280';
            try {
                const res = await fetch('/api/geo/vat/validate', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({vat_number: raw}),
                });
                const j = await res.json();
                if (j.valid) {
                    viesStatus.innerHTML = `✓ <strong>Valid</strong> — ${Utils.escapeHtml(j.name || '')} · ${Utils.escapeHtml(j.address || '')}`;
                    viesStatus.style.color = '#059669';
                    // Auto-fill company name if empty
                    const nameInp = form.querySelector('[name="companyName"]');
                    if (nameInp && !nameInp.value && j.name) nameInp.value = j.name;
                } else if (j.error === 'service_unavailable') {
                    viesStatus.textContent = '⚠ VIES temporarily unreachable — try again in a moment';
                    viesStatus.style.color = '#a16207';
                } else if (j.reason === 'non_eu_country') {
                    viesStatus.textContent = `ℹ ${j.message}`;
                    viesStatus.style.color = '#a16207';
                } else if (j.reason === 'bad_format') {
                    viesStatus.textContent = `✗ ${j.message}`;
                    viesStatus.style.color = '#dc2626';
                } else {
                    viesStatus.textContent = '✗ VAT number not found in VIES';
                    viesStatus.style.color = '#dc2626';
                }
            } catch (e) {
                viesStatus.textContent = '✗ Network error: ' + (e.message || e);
                viesStatus.style.color = '#dc2626';
            }
            viesBtn.disabled = false;
        });
    }

    form.querySelectorAll('input[name="entityType"]').forEach(r => r.addEventListener('change', (e) => {
        if(e.target.value === 'person') {
            document.getElementById('linked-company-wrapper').classList.remove('hidden');
            document.getElementById('name-label').innerText = Utils.t('fields.fullName');
        } else {
            document.getElementById('linked-company-wrapper').classList.add('hidden');
            document.getElementById('name-label').innerText = Utils.t('fields.companyName');
            form.querySelector('[name="linkedCompanyId"]').value = '';
        }
    }));
    
    if (typeof Utils.initAutocomplete === 'function') {
        const countryInp = document.getElementById('country-input');
        if (typeof COUNTRIES !== 'undefined') Utils.initAutocomplete(countryInp, COUNTRIES);
        
        Utils.initAutocomplete(document.getElementById('city-input'), () => {
            const selectedCountry = countryInp.value;
            if (selectedCountry && typeof CITIES_BY_COUNTRY !== 'undefined' && CITIES_BY_COUNTRY[selectedCountry]) return CITIES_BY_COUNTRY[selectedCountry];
            return typeof ALL_CITIES_FLAT !== 'undefined' ? ALL_CITIES_FLAT : [];
        });
    }
}