// Aspidus B2B Portal — API calls and top-level flow

async function saveProductItem() {
    const nameEl = document.getElementById('form-product-name');
    const priceEl = document.getElementById('form-product-price');
    if (!nameEl.value || !priceEl.value) return showToast(t('err_product_required'), 'error');

    const fl = document.getElementById('full-loading'); if (fl) { fl.classList.remove('hidden'); fl.classList.add('flex'); }

    // Certificate uploads
    const fileEl = document.getElementById('form-product-certs');
    if (fileEl && fileEl.files.length > 0) {
        const fd = new FormData();
        for (let i = 0; i < fileEl.files.length; i++) fd.append('file', fileEl.files[i]);
        try {
            const uploadRes = await fetch(`/api/portal/upload/${TOKEN}`, { method: 'POST', headers: { 'X-Portal-Auth': authKey }, body: fd });
            const uploadData = await uploadRes.json();
            if (uploadRes.ok && uploadData.urls) uploadedCertUrls = uploadedCertUrls.concat(uploadData.urls);
        } catch (err) { /* silently */ }
    }

    const val = id => (document.getElementById(id)?.value || '').trim();
    const num = id => { const v = parseFloat(document.getElementById(id)?.value); return isNaN(v) ? null : v; };

    const payload = {
        id: document.getElementById('form-product-id').value || null,
        name: nameEl.value,
        category: val('form-product-category'),
        hsCode: val('form-product-hscode'),
        sku: val('form-product-sku'),
        brand: val('form-product-brand'),
        shortDescription: val('form-product-shortdesc'),
        detailedSpec: val('form-product-spec'),
        packaging: val('form-product-packaging'),
        packageWeight: num('form-product-package-weight'),
        unitsPerPallet: num('form-product-per-pallet'),
        availableStock: num('form-product-stock'),
        warehouseLocation: val('form-product-warehouse'),
        leadTime: val('form-product-leadtime'),
        logistics: {
            cap20: num('form-product-cap20'),
            cap40: num('form-product-cap40')
        },
        coaParams: activeCOAParams,
        supplyOffers: [{
            price: parseFloat(priceEl.value),
            currency: val('form-product-currency'),
            unit: val('form-product-unit'),
            moq: num('form-product-moq'),
            incoterm: val('form-product-incoterm'),
            country: val('form-product-origin'),
            validUntil: val('form-product-valid'),
            paymentTerms: val('form-product-payterms'),
            certificates: uploadedCertUrls.join(', ')
        }]
    };

    try {
        const res = await fetch(`/api/portal/products/submit/${TOKEN}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Portal-Auth': authKey },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            showToast(t('msg_product_saved'), 'success');
            closeProductModal();
            loadPortalData();
        } else {
            const err = await res.json().catch(() => ({}));
            showToast(err.error || t('err_generic'), 'error');
        }
    } catch (err) { showToast(t('err_network'), 'error'); }
    if (fl) { fl.classList.add('hidden'); fl.classList.remove('flex'); }
}

async function submitRFQ() {
    const prod = document.getElementById('rfq-product').value.trim();
    const qty = document.getElementById('rfq-qty').value;
    if (!prod || !qty) return showToast(t('err_rfq_required'), 'error');

    const payload = {
        productName: prod,
        quantity: qty,
        targetPrice: document.getElementById('rfq-price').value || null,
        notes: document.getElementById('rfq-notes').value || ""
    };

    const fl = document.getElementById('full-loading'); if (fl) { fl.classList.remove('hidden'); fl.classList.add('flex'); }
    try {
        const res = await fetch(`/api/portal/rfq/submit/${TOKEN}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Portal-Auth': authKey },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            showToast(t('msg_rfq_sent'), 'success');
            closeRFQModal();
            loadPortalData();
        } else { showToast(t('err_generic'), 'error'); }
    } catch (e) { showToast(t('err_network'), 'error'); }
    if (fl) { fl.classList.add('hidden'); fl.classList.remove('flex'); }
}

// Profile change request (šalje adminu na odobrenje)
async function submitProfileChangeRequest() {
    const val = id => (document.getElementById(id)?.value || '').trim();
    const payload = {
        email: val('profile-email'),
        phone: val('profile-phone'),
        contactPerson: val('profile-person'),
        street: val('profile-street'),
        city: val('profile-city'),
        country: val('profile-country'),
        note: val('profile-note')
    };
    // Šalji samo polja koja klijent stvarno želi da promeni? — server će odbiti prazno
    const fl = document.getElementById('full-loading'); if (fl) { fl.classList.remove('hidden'); fl.classList.add('flex'); }
    try {
        const res = await fetch(`/api/portal/profile/update/${TOKEN}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Portal-Auth': authKey },
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            showToast(t('msg_profile_sent'), 'success');
            loadPortalData();
        } else {
            const map = {
                'INVALID_EMAIL_FORMAT': t('err_invalid_email'),
                'NO_CHANGES_PROVIDED': t('err_no_changes')
            };
            showToast(map[data.error] || data.error || t('err_generic'), 'error');
        }
    } catch (err) { showToast(t('err_network'), 'error'); }
    if (fl) { fl.classList.add('hidden'); fl.classList.remove('flex'); }
}

// Otvaranje dokumenta u portalu.
//
// Ranije: klik = trenutni download fajla. Sada: klik otvara preview modal sa
// PDF-om u iframe-u; korisnik može da PROČITA dokument pa tek onda odluči da
// li skida "hard copy" (Download dugme) ili štampa (Print). Preview endpoint
// koristi ?inline=1 pa server šalje Content-Disposition: inline umesto
// attachment, i (za ponude) regeneriše PDF u memoriji iz aktuelnih podataka
// bez pisanja na disk.
//
// Blob se drži u memoriji dok je modal otvoren i oslobađa se pri zatvaranju,
// tako da Print/Download koriste istu kopiju bez novog HTTP poziva.
let _previewBlobUrl = null;
let _previewFileName = null;
let _previewDocId = null;

async function downloadPortalDocument(docId) {
    if (!docId) return;
    try {
        const res = await fetch(`/api/portal/document/${TOKEN}/${encodeURIComponent(docId)}?inline=1`, {
            headers: { 'X-Portal-Auth': authKey }
        });
        if (!res.ok) {
            if (res.status === 404 || res.status === 410) return showToast(t('err_doc_not_found'), 'error');
            if (res.status === 403) return showToast(t('err_doc_forbidden'), 'error');
            return showToast(t('err_generic'), 'error');
        }
        const blob = await res.blob();
        const cd = res.headers.get('Content-Disposition') || '';
        const m = cd.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
        _previewFileName = m ? decodeURIComponent(m[1].replace(/"$/, '')) : `document_${docId}.pdf`;
        _previewDocId = docId;
        if (_previewBlobUrl) { URL.revokeObjectURL(_previewBlobUrl); }
        _previewBlobUrl = URL.createObjectURL(blob);

        const iframe = document.getElementById('pdf-preview-frame');
        const title = document.getElementById('pdf-preview-title');
        if (title) title.textContent = _previewFileName;
        if (iframe) iframe.src = _previewBlobUrl;
        const modal = document.getElementById('pdf-preview-modal');
        modal.classList.remove('hidden'); modal.classList.add('flex');
    } catch (e) { showToast(t('err_network'), 'error'); }
}

window.closePdfPreview = function() {
    const modal = document.getElementById('pdf-preview-modal');
    if (modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); }
    const iframe = document.getElementById('pdf-preview-frame');
    if (iframe) iframe.src = 'about:blank';
    if (_previewBlobUrl) { URL.revokeObjectURL(_previewBlobUrl); _previewBlobUrl = null; }
    _previewFileName = null; _previewDocId = null;
};

// Print: reuse blob URL, ask iframe to trigger native print dialog.
// This works in all major browsers where the built-in PDF viewer is loaded
// into the iframe; if the browser is locked down it falls back to
// window.open() so the user can print via browser controls.
window.printPdfPreview = function() {
    const iframe = document.getElementById('pdf-preview-frame');
    if (!iframe || !iframe.contentWindow) return;
    try {
        iframe.contentWindow.focus();
        iframe.contentWindow.print();
    } catch (e) {
        if (_previewBlobUrl) window.open(_previewBlobUrl, '_blank');
    }
};

// Download: trigger a new attachment fetch (proper Content-Disposition:
// attachment) so the browser Save dialog appears with the right filename.
// We do NOT reuse the inline blob because programmatic <a download="…">
// on some Safari builds ignores the download attribute for cross-origin
// blob URLs — a fresh attachment request is more portable.
window.downloadPdfPreview = async function() {
    if (!_previewDocId) return;
    try {
        const res = await fetch(`/api/portal/document/${TOKEN}/${encodeURIComponent(_previewDocId)}`, {
            headers: { 'X-Portal-Auth': authKey }
        });
        if (!res.ok) return showToast(t('err_generic'), 'error');
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a'); a.href = url; a.download = _previewFileName || 'document.pdf';
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
        showToast(t('msg_download_logged'), 'success');
    } catch (e) { showToast(t('err_network'), 'error'); }
};

async function requestOTP() {
    const msg = document.getElementById('otp-status-msg');
    if (msg) { msg.textContent = t('requesting'); msg.classList.remove('hidden'); }
    try {
        const res = await fetch(`/api/portal/auth/send_otp/${TOKEN}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            if (msg) msg.textContent = data.message || t('otp_sent');
            const oia = document.getElementById('otp-input-area'); if (oia) oia.classList.remove('hidden');
        } else {
            if (msg) msg.textContent = data.error || t('err_generic');
        }
    } catch (e) { if (msg) msg.textContent = t('err_network'); }
}

async function verifyOTP() {
    const code = document.getElementById('otp-code')?.value;
    if (!code || code.length !== 6) return showToast(t('enter_code'), 'error');
    try {
        const res = await fetch(`/api/portal/auth/verify_otp/${TOKEN}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ otp: code })
        });
        const data = await res.json();
        if (res.ok) {
            authKey = data.auth_key;
            sessionStorage.setItem(`portal_auth_${TOKEN}`, authKey);
            loadPortalData();
        } else {
            showToast(t('err_bad_otp'), 'error');
        }
    } catch (e) { showToast(t('err_network'), 'error'); }
}

// KYC form submit
const kycForm = document.getElementById('kyc-form');
if (kycForm) {
    kycForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fl = document.getElementById('full-loading'); if (fl) { fl.classList.remove('hidden'); fl.classList.add('flex'); }
        try {
            const extractPersons = (containerId) => {
                const arr = []; const cont = document.getElementById(containerId);
                if (cont) cont.querySelectorAll('.person-entry').forEach(row => {
                    const n = row.querySelector('.p-name')?.value; const p = row.querySelector('.p-pass')?.value; const nt = row.querySelector('.p-nat')?.value;
                    if (n && p) arr.push({ name: n, passport: p, nationality: nt });
                });
                return arr;
            };

            const uploadedFiles = {};
            const fileInputs = [
                { id: 'file-passport', key: 'passport' },
                { id: 'file-license', key: 'license' },
                { id: 'file-inc', key: 'incorporation' }
            ];
            for (const input of fileInputs) {
                const el = document.getElementById(input.id);
                if (el && el.files.length > 0) {
                    const fd = new FormData();
                    for (let i = 0; i < el.files.length; i++) fd.append('file', el.files[i]);
                    try {
                        const r = await fetch(`/api/portal/upload/${TOKEN}`, { method: 'POST', headers: { 'X-Portal-Auth': authKey }, body: fd });
                        if (r.ok) {
                            const d = await r.json();
                            if (d.urls && d.urls.length > 0) uploadedFiles[input.key] = d.urls;
                        }
                    } catch (err) { console.error(err); }
                }
            }

            const g = id => document.getElementById(id)?.value || '';
            const c = id => !!document.getElementById(id)?.checked;
            const payload = {
                partner_id: portalData?.partner?.id,
                companyName: g('kyc-comp-name'), regNo: g('kyc-reg-no'), taxId: g('kyc-tax-id'),
                website: g('kyc-website'), industry: g('kyc-industry'),
                regAddr: g('kyc-reg-addr'), opAddr: g('kyc-op-addr'),
                bankName: g('kyc-bank-name'), bankIban: g('kyc-bank-iban'), bankSwift: g('kyc-bank-swift'),
                bankAddr: g('kyc-bank-addr'), corrBank: g('kyc-corr-bank'),
                turnover: g('kyc-turnover'), sourceOfFunds: g('kyc-sof'),
                directors: extractPersons('directors-container'),
                ubos: extractPersons('ubos-container'),
                aml: { isPEP: c('kyc-pep'), isSanctioned: c('kyc-sanctions'), litigation: c('kyc-litigation'), dualUse: c('kyc-dualuse') },
                submitterName: g('kyc-sub-name'), submitterTitle: g('kyc-sub-title'),
                consent: c('kyc-consent'),
                files: uploadedFiles
            };
            const res = await fetch(`/api/portal/kyc/submit/${TOKEN}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Portal-Auth': authKey },
                body: JSON.stringify(payload)
            });
            if (res.ok) {
                showToast(t('msg_kyc_saved'), 'success');
                loadPortalData();
            } else {
                const d = await res.json().catch(() => ({}));
                showToast(d.error || t('err_generic'), 'error');
            }
        } catch (e) { showToast(t('err_network'), 'error'); }
        if (fl) { fl.classList.add('hidden'); fl.classList.remove('flex'); }
    });
}

// KYC banner (update requested/expired) + prikaz razloga koji je admin uneo.
// Klijent mora jasno videti šta se traži, ne samo "action required".
function renderUpdateRequestBanner() {
    const b = document.getElementById('update-request-banner'); if (!b) return;
    const status = portalData?.partner?.kycStatus;
    if (status !== 'update_requested' && status !== 'expired') { b.classList.add('hidden'); return; }
    b.classList.remove('hidden');
    const note = portalData?.partner?.kycReviewNote;
    const descEl = document.getElementById('lbl-update-req-desc');
    if (descEl) {
        descEl.textContent = note ? (t('update_req_note_prefix') + note) : t('update_req_desc');
    }
}

async function loadPortalData() {
    if (!authKey) {
        document.getElementById('loading-state').classList.add('hidden');
        document.getElementById('otp-screen').classList.remove('hidden');
        if (!otpRequested) { otpRequested = true; requestOTP(); }
        return;
    }
    document.getElementById('otp-screen').classList.add('hidden');
    document.getElementById('loading-state').classList.remove('hidden');
    try {
        const res = await fetch(`/api/portal/data/${TOKEN}`, { headers: { 'X-Portal-Auth': authKey } });
        if (res.status === 401) {
            authKey = null; sessionStorage.removeItem(`portal_auth_${TOKEN}`);
            loadPortalData(); return;
        }
        if (!res.ok) {
            document.getElementById('loading-state').innerHTML = `<div class="text-center"><p class="text-red-600 font-semibold">${t('err_access_denied')}</p><p class="text-xs text-slate-500 mt-2">HTTP ${res.status}</p></div>`;
            return;
        }
        portalData = await res.json();

        // Header
        document.getElementById('comp-name').textContent = portalData?.company?.name || 'Aspidus';
        if (portalData?.company?.logoUrl) {
            const l = document.getElementById('comp-logo'); if (l) { l.src = portalData.company.logoUrl; l.classList.remove('hidden'); }
        }
        document.getElementById('partner-name').textContent = portalData?.partner?.companyName || '—';

        // Prilagodljiv brending: firma podešava svoju primarnu boju u company.brandColor.
        // Primenjuje se na CSS varijablu koju koriste dugmad i tab-ovi.
        if (portalData?.company?.brandColor) {
            const bc = portalData.company.brandColor;
            const root = document.documentElement;
            root.style.setProperty('--p-accent', bc);
            // Blago tamnija hover varijanta - approksimacija
            root.style.setProperty('--p-accent-hover', bc);
            // Naslov browsera
            try { document.title = `${portalData.company.name || 'Aspidus'} — B2B Portal`; } catch(e) {}
        }

        // KYC form pre-fill from latest submission
        if (portalData?.latest_kyc) {
            const lk = portalData.latest_kyc;
            const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };
            set('kyc-comp-name', lk.companyName); set('kyc-reg-no', lk.regNo); set('kyc-tax-id', lk.taxId);
            set('kyc-website', lk.website); set('kyc-industry', lk.industry);
            set('kyc-reg-addr', lk.regAddr); set('kyc-op-addr', lk.opAddr);
            set('kyc-bank-name', lk.bankName); set('kyc-bank-iban', lk.bankIban); set('kyc-bank-swift', lk.bankSwift);
            set('kyc-bank-addr', lk.bankAddr); set('kyc-corr-bank', lk.corrBank);
            set('kyc-turnover', lk.turnover); set('kyc-sof', lk.sourceOfFunds);
            set('kyc-sub-name', lk.submitterName); set('kyc-sub-title', lk.submitterTitle);
            if (lk.aml) {
                const chk = (id, v) => { const el = document.getElementById(id); if (el) el.checked = !!v; };
                chk('kyc-pep', lk.aml.isPEP); chk('kyc-sanctions', lk.aml.isSanctioned);
                chk('kyc-litigation', lk.aml.litigation); chk('kyc-dualuse', lk.aml.dualUse);
            }
        } else {
            const el = document.getElementById('kyc-comp-name'); if (el && !el.value) el.value = portalData?.partner?.companyName || '';
        }

        // Directors/UBOs — ensure at least one row exists
        const dc = document.getElementById('directors-container'); if (dc && dc.children.length === 0) addDirector();
        const uc = document.getElementById('ubos-container'); if (uc && uc.children.length === 0) addUBO();

        // Show content
        document.getElementById('loading-state').classList.add('hidden');
        document.getElementById('portal-content').classList.remove('hidden');

        updateStaticText();
        renderKycStatusLine();
        renderUpdateRequestBanner();
        renderDashboard();
        renderDeals();
        renderOffers();
        renderRFQs();
        renderGoodsTable();
        renderDocuments();
        fillProfile();
        renderNotifications();

        if (typeof applyPermissions === 'function') applyPermissions(portalData?.permissions || []);
    } catch (e) {
        console.error(e);
        document.getElementById('loading-state').innerHTML = `<div class="text-center"><p class="text-red-600 font-semibold">${t('err_access_denied')}</p></div>`;
    }
}

// Boot
updateStaticText();
loadPortalData();
