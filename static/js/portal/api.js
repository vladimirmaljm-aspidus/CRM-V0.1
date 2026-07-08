async function saveProductItem() {
    const nameEl = document.getElementById('form-product-name'); const priceEl = document.getElementById('form-product-price');
    if(!nameEl.value || !priceEl.value) return alert("Product Name and Selling Price are mandatory.");
    
    const fl = document.getElementById('full-loading'); if(fl) fl.classList.remove('hidden');
    const fileEl = document.getElementById('form-product-certs');
    if(fileEl && fileEl.files.length > 0) {
        const fd = new FormData(); for(let i=0; i<fileEl.files.length; i++) fd.append('file', fileEl.files[i]);
        try {
            const uploadRes = await fetch(`/api/portal/upload/${TOKEN}`, { method: 'POST', headers: { 'X-Portal-Auth': authKey }, body: fd });
            const uploadData = await uploadRes.json();
            if(uploadRes.ok && uploadData.urls) uploadedCertUrls = uploadedCertUrls.concat(uploadData.urls);
        } catch(err) { }
    }

    const payload = {
        id: document.getElementById('form-product-id').value || null,
        name: nameEl.value,
        category: document.getElementById('form-product-category').value,
        hsCode: document.getElementById('form-product-hscode').value,
        sku: document.getElementById('form-product-sku').value,
        brand: document.getElementById('form-product-brand').value,
        detailedSpec: document.getElementById('form-product-spec').value,
        logistics: { cap20: parseFloat(document.getElementById('form-product-cap20').value) || null, cap40: parseFloat(document.getElementById('form-product-cap40').value) || null },
        coaParams: activeCOAParams,
        supplyOffers: [{
            price: parseFloat(priceEl.value),
            currency: document.getElementById('form-product-currency').value,
            unit: document.getElementById('form-product-unit').value,
            moq: parseFloat(document.getElementById('form-product-moq').value) || null,
            incoterm: document.getElementById('form-product-incoterm').value,
            country: document.getElementById('form-product-origin').value,
            validUntil: document.getElementById('form-product-valid').value,
            certificates: uploadedCertUrls.join(', ')
        }]
    };

    try {
        const res = await fetch(`/api/portal/products/submit/${TOKEN}`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Portal-Auth': authKey }, body: JSON.stringify(payload) });
        if(res.ok) { closeProductModal(); loadPortalData(); }
    } catch(err) { alert("Error saving product."); }
    if(fl) fl.classList.add('hidden');
}

async function submitRFQ() {
    const prod = document.getElementById('rfq-product').value;
    const qty = document.getElementById('rfq-qty').value;
    if(!prod || !qty) return alert("Product and Quantity are required.");
    
    const payload = {
        productName: prod,
        quantity: qty,
        targetPrice: document.getElementById('rfq-price').value || null,
        notes: document.getElementById('rfq-notes').value || ""
    };

    const fl = document.getElementById('full-loading'); if(fl) fl.classList.remove('hidden');
    try {
        const res = await fetch(`/api/portal/rfq/submit/${TOKEN}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Portal-Auth': authKey },
            body: JSON.stringify(payload)
        });
        if(res.ok) {
            closeRFQModal();
            loadPortalData();
        } else {
            alert("Error submitting RFQ. Please check connection.");
        }
    } catch(e) { alert("Network Error"); }
    if(fl) fl.classList.add('hidden');
}

async function updateProfileSettings() {
    const email = document.getElementById('profile-email').value; if(!email) return alert("Email required");
    const fl = document.getElementById('full-loading'); if(fl) fl.classList.remove('hidden');
    try {
        const res = await fetch(`/api/portal/profile/update/${TOKEN}`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Portal-Auth': authKey }, body: JSON.stringify({ email: email }) });
        if(res.ok) { alert("Profile updated successfully."); loadPortalData(); }
    } catch(err) { alert("Network error."); }
    if(fl) fl.classList.add('hidden');
}

async function requestOTP() {
    const msg = document.getElementById('otp-status-msg'); if(msg) { msg.innerText = t('requesting'); msg.classList.remove('hidden'); }
    try {
        const res = await fetch(`/api/portal/auth/send_otp/${TOKEN}`, { method: 'POST' });
        const data = await res.json();
        if(res.ok) { if(msg) msg.innerText = data.message || t('otp_sent'); const oia = document.getElementById('otp-input-area'); if(oia) oia.classList.remove('hidden'); }
        else { if(msg) msg.innerText = "Error: " + (data.error || "Network error"); }
    } catch(e) { if(msg) msg.innerText = "Network Connection Error"; }
}

async function verifyOTP() {
    const codeEl = document.getElementById('otp-code'); const code = codeEl ? codeEl.value : null;
    if(!code || code.length !== 6) return alert(t('enter_code'));
    try {
        const res = await fetch(`/api/portal/auth/verify_otp/${TOKEN}`, { method: 'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({otp: code}) });
        const data = await res.json();
        if(res.ok) { authKey = data.auth_key; sessionStorage.setItem(`portal_auth_${TOKEN}`, authKey); loadPortalData(); }
        else alert("Invalid or Expired OTP.");
    } catch(e) { alert("Network Error"); }
}

const kycForm = document.getElementById('kyc-form');
if(kycForm) {
    kycForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fl = document.getElementById('full-loading'); if(fl) fl.classList.remove('hidden');
        try {
            const extractPersons = (containerId) => {
                const arr = []; const cont = document.getElementById(containerId);
                if(cont) {
                    cont.querySelectorAll('.person-entry').forEach(row => {
                        const n = row.querySelector('.p-name')?.value; const p = row.querySelector('.p-pass')?.value; const nt = row.querySelector('.p-nat')?.value;
                        if(n && p) arr.push({name: n, passport: p, nationality: nt});
                    });
                }
                return arr;
            };

            // DODATO: Sekvencijalno otpremanje KYC fajlova pre slanja forme
            const uploadedFiles = {};
            const fileInputs = [
                { id: 'file-passport', key: 'passport' },
                { id: 'file-license', key: 'license' },
                { id: 'file-registry', key: 'registry' }
            ];

            for (const input of fileInputs) {
                const el = document.getElementById(input.id);
                if (el && el.files.length > 0) {
                    const fd = new FormData();
                    fd.append('file', el.files[0]);
                    try {
                        const uploadRes = await fetch(`/api/portal/upload/${TOKEN}`, { method: 'POST', headers: { 'X-Portal-Auth': authKey }, body: fd });
                        if (uploadRes.ok) {
                            const uploadData = await uploadRes.json();
                            if (uploadData.urls && uploadData.urls.length > 0) {
                                uploadedFiles[input.key] = uploadData.urls[0];
                            } else if (uploadData.url) {
                                uploadedFiles[input.key] = uploadData.url;
                            }
                        }
                    } catch(err) { console.error("Upload error", err); }
                }
            }

            const payload = {
                partner_id: portalData?.partner?.id, companyName: document.getElementById('kyc-comp-name')?.value, regNo: document.getElementById('kyc-reg-no')?.value, taxId: document.getElementById('kyc-tax-id')?.value, website: document.getElementById('kyc-website')?.value, industry: document.getElementById('kyc-industry')?.value, regAddr: document.getElementById('kyc-reg-addr')?.value, opAddr: document.getElementById('kyc-op-addr')?.value, bankName: document.getElementById('kyc-bank-name')?.value, bankIban: document.getElementById('kyc-bank-iban')?.value, bankSwift: document.getElementById('kyc-bank-swift')?.value, bankAddr: document.getElementById('kyc-bank-addr')?.value, corrBank: document.getElementById('kyc-corr-bank')?.value, turnover: document.getElementById('kyc-turnover')?.value, sourceOfFunds: document.getElementById('kyc-sof')?.value, directors: extractPersons('directors-container'), ubos: extractPersons('ubos-container'), aml: { isPEP: document.getElementById('kyc-pep')?.checked, isSanctioned: document.getElementById('kyc-sanctions')?.checked, litigation: document.getElementById('kyc-litigation')?.checked, dualUse: document.getElementById('kyc-dualuse')?.checked }, submitterName: document.getElementById('kyc-sub-name')?.value, submitterTitle: document.getElementById('kyc-sub-title')?.value, consent: document.getElementById('kyc-consent')?.checked, 
                files: uploadedFiles // Promenjeno sa {} na sakupljene fajlove
            };
            const res = await fetch(`/api/portal/kyc/submit/${TOKEN}`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Portal-Auth': authKey }, body: JSON.stringify(payload) });
            if(res.ok) { alert('Data securely encrypted and stored in Vault!'); loadPortalData(); }
        } catch(e) { alert("Error uploading data."); }
        if(fl) fl.classList.add('hidden');
    });
}

async function loadPortalData() {
    if(!authKey) {
        document.getElementById('loading-state').classList.add('hidden'); document.getElementById('otp-screen').classList.remove('hidden');
        if(!otpRequested) { otpRequested = true; requestOTP(); } return;
    }
    document.getElementById('otp-screen').classList.add('hidden'); document.getElementById('loading-state').classList.remove('hidden');
    try {
        const res = await fetch(`/api/portal/data/${TOKEN}`, { headers: { 'X-Portal-Auth': authKey } });
        if(res.status === 401) { authKey = null; sessionStorage.removeItem(`portal_auth_${TOKEN}`); loadPortalData(); return; }
        portalData = await res.json();
        
        document.getElementById('comp-name').innerText = portalData?.company?.name || 'Aspidus CRM';
        if(portalData?.company?.logoUrl) { const l = document.getElementById('comp-logo'); if(l) { l.src = portalData.company.logoUrl; l.classList.remove('hidden'); } }
        document.getElementById('partner-name').innerText = portalData?.partner?.companyName || 'Unknown Partner';
        
        if(portalData?.latest_kyc) {
            const lk = portalData.latest_kyc;
            if(document.getElementById('kyc-comp-name')) document.getElementById('kyc-comp-name').value = lk.companyName || '';
            if(document.getElementById('kyc-reg-no')) document.getElementById('kyc-reg-no').value = lk.regNo || '';
            if(document.getElementById('kyc-tax-id')) document.getElementById('kyc-tax-id').value = lk.taxId || '';
            if(document.getElementById('kyc-website')) document.getElementById('kyc-website').value = lk.website || '';
            if(document.getElementById('kyc-industry')) document.getElementById('kyc-industry').value = lk.industry || '';
            if(document.getElementById('kyc-reg-addr')) document.getElementById('kyc-reg-addr').value = lk.regAddr || '';
            if(document.getElementById('kyc-op-addr')) document.getElementById('kyc-op-addr').value = lk.opAddr || '';
            if(document.getElementById('kyc-bank-name')) document.getElementById('kyc-bank-name').value = lk.bankName || '';
            if(document.getElementById('kyc-bank-iban')) document.getElementById('kyc-bank-iban').value = lk.bankIban || '';
            if(document.getElementById('kyc-bank-swift')) document.getElementById('kyc-bank-swift').value = lk.bankSwift || '';
            if(document.getElementById('kyc-bank-addr')) document.getElementById('kyc-bank-addr').value = lk.bankAddr || '';
            if(document.getElementById('kyc-corr-bank')) document.getElementById('kyc-corr-bank').value = lk.corrBank || '';
            if(document.getElementById('kyc-turnover')) document.getElementById('kyc-turnover').value = lk.turnover || '';
            if(document.getElementById('kyc-sof')) document.getElementById('kyc-sof').value = lk.sourceOfFunds || '';
            if(document.getElementById('kyc-sub-name')) document.getElementById('kyc-sub-name').value = lk.submitterName || '';
            if(document.getElementById('kyc-sub-title')) document.getElementById('kyc-sub-title').value = lk.submitterTitle || '';
            if(lk.aml) {
                document.getElementById('kyc-pep').checked = !!lk.aml.isPEP;
                document.getElementById('kyc-sanctions').checked = !!lk.aml.isSanctioned;
                document.getElementById('kyc-litigation').checked = !!lk.aml.litigation;
                document.getElementById('kyc-dualuse').checked = !!lk.aml.dualUse;
            }
        } else {
            if(document.getElementById('kyc-comp-name')) document.getElementById('kyc-comp-name').value = portalData?.partner?.companyName || '';
        }

        if(document.getElementById('profile-email')) document.getElementById('profile-email').value = portalData?.partner?.email || '';

        if (portalData?.partner?.kycStatus === 'update_requested' || portalData?.partner?.kycStatus === 'expired') {
            const b = document.getElementById('update-request-banner'); if(b) b.classList.remove('hidden');
            switchTab('kyc');
        }
        
        const dc = document.getElementById('directors-container'); if(dc && dc.children.length === 0) addDirector();
        const uc = document.getElementById('ubos-container'); if(uc && uc.children.length === 0) addUBO();
        
        document.getElementById('loading-state').classList.add('hidden'); document.getElementById('portal-content').classList.remove('hidden');
        
        updateStaticText();
        renderDeals(); 
        renderOffers(); 
        renderRFQs(); 
        renderGoodsTable();
        renderDocuments(); 

        // DODATO: Primena permisija nakon učitavanja podataka
        if (typeof applyPermissions === 'function') {
            applyPermissions(portalData?.permissions || []);
        }

    } catch (e) {
        document.getElementById('loading-state').innerHTML = `<span class="text-5xl">🛑</span><p class="mt-4 text-red-600 font-black text-xl">ACCESS DENIED</p>`;
    }
}

updateStaticText(); loadPortalData();