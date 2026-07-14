const dict = {
    'en': {
        // Loading / header
        'loading': 'Loading Secure Portal…', 'b2b': 'B2B Client Portal', 'welcome': 'Welcome', 'logout': 'Logout',
        // Tabs
        'dashboard': 'Dashboard', 'shipments': 'Shipments', 'offers': 'Offers', 'rfq': 'My RFQs',
        'kyc_tab': 'KYC / Compliance', 'goods': 'My Products', 'profile': 'Profile', 'documents': 'Documents',
        // Dashboard
        'stat_shipments': 'Active Shipments', 'stat_offers': 'Open Offers', 'stat_rfqs': 'Pending RFQs', 'stat_kyc': 'KYC Status',
        'recent_offers': 'Recent Offers', 'recent_docs': 'Recent Documents', 'view_all_offers': 'View all →', 'view_all_docs': 'View all →',
        // Shipments
        'no_deals': 'No active shipments.', 'contract': 'Contract No.', 'vessel': 'Vessel', 'bl': 'B/L Number', 'pol': 'Port of Loading', 'pod': 'Port of Discharge',
        // Offers
        'no_offers': 'No active offers.', 'offer_no': 'Offer No.', 'valid': 'Valid Until', 'incoterm': 'Incoterm', 'qty': 'Quantity',
        'offer_accepted': 'Accepted', 'offer_declined': 'Declined', 'hs_code': 'HS Code', 'packaging': 'Packaging', 'payment_terms': 'Payment Terms',
        'shipping': 'Shipping', 'specification': 'Specification', 'view_details': 'View Details', 'offer_decline': 'Decline', 'offer_accept': 'Accept Offer',
        'confirm_accept_offer': 'Confirm acceptance of this offer? Your account manager will contact you to finalize the deal.',
        'confirm_decline_offer': 'Are you sure you want to decline this offer?', 'decline_reason': 'Optional reason (visible to admin):',
        'msg_offer_accepted': 'Offer accepted. Thank you!', 'msg_offer_declined': 'Offer declined.',
        'price': 'Price', 'origin': 'Origin', 'lead_time': 'Lead Time', 'notes': 'Notes', 'close': 'Close',
        // RFQ
        'no_rfq': 'No RFQs submitted yet.', 'rfq_main_title': 'Requests for Quotation', 'rfq_main_desc': 'Submit and track your purchasing requests.',
        'rfq_modal_title': 'Submit New RFQ', 'rfq_prod': 'Product / Commodity', 'rfq_qty': 'Target Quantity',
        'rfq_price': 'Target Price (Optional)', 'rfq_notes': 'Notes / Specifications', 'btn_new_rfq': '+ New RFQ', 'btn_rfq_send': 'Send Request', 'btn_rfq_cancel': 'Cancel',
        // Documents
        'docs_title': 'Document Vault', 'docs_desc': 'Download your official offers, invoices and contracts. Every download is logged.',
        'th_doc_date': 'Date', 'th_doc_type': 'Type', 'th_doc_name': 'File', 'th_doc_action': 'Action',
        'no_docs': 'No documents available yet.', 'btn_download': 'Download', 'btn_download_pdf': 'Download PDF',
        // KYC
        'kyc_title': 'Compliance & KYC', 'kyc_desc': 'Securely submit corporate data. Encrypted in an air-gapped vault.',
        'kyc_current_status': 'Current Status',
        'kyc_status_pending': 'Pending Review', 'kyc_status_approved': 'Approved', 'kyc_status_rejected': 'Rejected',
        'kyc_status_update_requested': 'Update Requested', 'kyc_status_expired': 'Expired',
        'update_req_note_prefix': 'Administrator requested the following updates: ',
        'kyc_sec1': '1. Company Details', 'kyc_sec2': '2. Banking & Financial Profile', 'kyc_sec3': '3. Directors & UBOs', 'kyc_sec4': '4. AML / CFT Compliance', 'kyc_sec5': '5. Corporate Documents',
        'reg_name': 'Registered Company Name', 'reg_no': 'Registration No.', 'tax_id': 'Tax ID / VAT', 'website': 'Website', 'industry': 'Industry / Activity',
        'reg_addr': 'Registered Address', 'op_addr': 'Operational Address',
        'bank_name': 'Bank Name', 'bank_iban': 'IBAN / Account No.', 'bank_swift': 'SWIFT / BIC', 'bank_addr': 'Bank Branch Address', 'corr_bank': 'Correspondent Bank',
        'turnover': 'Expected Annual Volume (USD)', 'sof': 'Source of Funds / Wealth',
        'dir_title': 'Directors / Managers', 'ubo_title': 'Ultimate Beneficial Owners (>25%)',
        'add_dir': '+ Add Director', 'add_ubo': '+ Add UBO', 'dir_name': 'Full Legal Name', 'dir_pass': 'Passport Number', 'dir_nat': 'Nationality',
        'pep': 'Any owner/director is a Politically Exposed Person (PEP)?',
        'sanctions': 'Subject to any UN/OFAC international sanctions?',
        'litigation': 'Involved in any AML/CFT litigation?',
        'dual_use': 'Trades in Dual-Use goods or military equipment?',
        'up_license': 'Trade License(s)', 'up_pass': 'Passports (Directors & UBOs)', 'up_inc': 'Cert. of Incorporation / MoA', 'upload_multi': 'You can select multiple files per category',
        'decl_title': 'Declaration & Consent',
        'consent': 'I declare the information is true and accurate, and consent to secure storage and processing.',
        'btn_submit': 'Submit Securely', 'update_req': 'Action Required: KYC Update', 'update_req_desc': 'Your documentation has expired or requires renewal.',
        // Products (my goods)
        'goods_title': 'My Products', 'goods_desc': 'Add products with full specifications. Admin will review and publish to the catalog.',
        'th_prod_name': 'Product', 'th_prod_price': 'Price', 'th_prod_specs': 'Specifications', 'th_prod_status': 'Status', 'th_prod_actions': 'Actions',
        'btn_new_product': '+ Add Product', 'btn_edit': 'Edit', 'no_products': 'No products submitted yet.',
        'ptab_general': 'General Info', 'ptab_commercial': 'Pricing & Terms', 'ptab_specs': 'Specs & Logistics', 'ptab_packaging': 'Packaging & Stock',
        'prod_modal_title': 'Product Details',
        // Profile
        'profile_title': 'Profile Settings', 'profile_desc': 'Request updates to your contact details. Admin approval is required for changes to apply.',
        'profile_email': 'Email', 'profile_phone': 'Phone', 'profile_person': 'Contact Person', 'profile_street': 'Street & No.', 'profile_city': 'City', 'profile_country': 'Country', 'profile_note': 'Note for admin (optional)',
        'btn_save_profile': 'Send for approval',
        'profile_history': 'Your Change Requests', 'no_profile_requests': 'No pending change requests.',
        // OTP
        'otp_title': 'Secure Portal Access', 'otp_desc': 'A one-time code has been sent to your email.',
        'enter_code': 'Enter 6-digit Code', 'verify_btn': 'Verify & Login', 'otp_sent': 'Code sent. Please check your email.', 'requesting': 'Requesting OTP…',
        'uploading': 'Encrypting & uploading…',
        // Messages / errors
        'msg_product_saved': 'Product submitted for approval.',
        'msg_rfq_sent': 'RFQ submitted successfully.',
        'msg_profile_sent': 'Change request sent to administrator.',
        'msg_kyc_saved': 'KYC data securely stored.',
        'msg_download_logged': 'Download logged.',
        'err_generic': 'Something went wrong. Please try again.',
        'err_network': 'Network error. Check your connection.',
        'err_bad_otp': 'Invalid or expired code.',
        'err_product_required': 'Product name and price are required.',
        'err_rfq_required': 'Product and quantity are required.',
        'err_invalid_email': 'Please enter a valid email address.',
        'err_no_changes': 'Please enter at least one field to change.',
        'err_doc_not_found': 'Document not found.',
        'err_doc_forbidden': 'You are not authorized for this document.',
        'err_access_denied': 'Access denied.'
    },
    'sr': {
        // Loading / header
        'loading': 'Učitavanje sigurnog portala…', 'b2b': 'B2B Portal za Klijente', 'welcome': 'Dobrodošli', 'logout': 'Odjava',
        // Tabs
        'dashboard': 'Pregled', 'shipments': 'Isporuke', 'offers': 'Ponude', 'rfq': 'Moji Upiti',
        'kyc_tab': 'KYC / Usklađenost', 'goods': 'Moji Proizvodi', 'profile': 'Profil', 'documents': 'Dokumenti',
        // Dashboard
        'stat_shipments': 'Aktivne Isporuke', 'stat_offers': 'Otvorene Ponude', 'stat_rfqs': 'Upiti na Čekanju', 'stat_kyc': 'KYC Status',
        'recent_offers': 'Nedavne Ponude', 'recent_docs': 'Nedavni Dokumenti', 'view_all_offers': 'Pogledaj sve →', 'view_all_docs': 'Pogledaj sve →',
        // Shipments
        'no_deals': 'Nema aktivnih isporuka.', 'contract': 'Broj Ugovora', 'vessel': 'Brod', 'bl': 'Broj Tovarnog Lista (B/L)', 'pol': 'Luka Ukrcaja', 'pod': 'Luka Iskrcaja',
        // Offers
        'no_offers': 'Nema aktivnih ponuda.', 'offer_no': 'Broj Ponude', 'valid': 'Važi Do', 'incoterm': 'Paritet (Incoterm)', 'qty': 'Količina',
        'offer_accepted': 'Prihvaćeno', 'offer_declined': 'Odbijeno', 'hs_code': 'HS Kod', 'packaging': 'Pakovanje', 'payment_terms': 'Uslovi Plaćanja',
        'shipping': 'Otprema', 'specification': 'Specifikacija', 'view_details': 'Pregledaj Detalje', 'offer_decline': 'Odbij', 'offer_accept': 'Prihvati Ponudu',
        'confirm_accept_offer': 'Potvrdite prihvatanje ove ponude? Vaš agent će vas kontaktirati radi finalizacije posla.',
        'confirm_decline_offer': 'Da li ste sigurni da želite da odbijete ovu ponudu?', 'decline_reason': 'Opciono obrazloženje (vidljivo administratoru):',
        'msg_offer_accepted': 'Ponuda je prihvaćena. Hvala Vam!', 'msg_offer_declined': 'Ponuda je odbijena.',
        'price': 'Cena', 'origin': 'Poreklo', 'lead_time': 'Vreme Isporuke', 'notes': 'Napomene', 'close': 'Zatvori',
        // RFQ
        'no_rfq': 'Još uvek nema poslatih upita.', 'rfq_main_title': 'Upiti za Ponudu (RFQ)', 'rfq_main_desc': 'Pošaljite i pratite vaše zahteve za nabavku.',
        'rfq_modal_title': 'Pošalji Novi Upit', 'rfq_prod': 'Proizvod / Roba', 'rfq_qty': 'Željena Količina',
        'rfq_price': 'Ciljna Cena (opciono)', 'rfq_notes': 'Napomene / Specifikacije', 'btn_new_rfq': '+ Novi Upit', 'btn_rfq_send': 'Pošalji Zahtev', 'btn_rfq_cancel': 'Otkaži',
        // Documents
        'docs_title': 'Trezor Dokumenata', 'docs_desc': 'Preuzmite svoje zvanične ponude, fakture i ugovore. Svako preuzimanje se evidentira.',
        'th_doc_date': 'Datum', 'th_doc_type': 'Tip', 'th_doc_name': 'Fajl', 'th_doc_action': 'Akcija',
        'no_docs': 'Trenutno nema dostupnih dokumenata.', 'btn_download': 'Preuzmi', 'btn_download_pdf': 'Preuzmi PDF',
        // KYC
        'kyc_title': 'Usklađenost i KYC', 'kyc_desc': 'Bezbedno pošaljite podatke o kompaniji. Šifrovano u izolovanom trezoru.',
        'kyc_current_status': 'Trenutni Status',
        'kyc_status_pending': 'Na Pregledu', 'kyc_status_approved': 'Odobreno', 'kyc_status_rejected': 'Odbijeno',
        'kyc_status_update_requested': 'Potrebna Dopuna', 'kyc_status_expired': 'Isteklo',
        'update_req_note_prefix': 'Administrator traži sledeće dopune: ',
        'kyc_sec1': '1. Podaci o Kompaniji', 'kyc_sec2': '2. Bankarski i Finansijski Profil', 'kyc_sec3': '3. Direktori i Stvarni Vlasnici', 'kyc_sec4': '4. AML / CFT Usklađenost', 'kyc_sec5': '5. Korporativna Dokumentacija',
        'reg_name': 'Registrovani Naziv Kompanije', 'reg_no': 'Matični Broj', 'tax_id': 'PIB / Poreski Broj', 'website': 'Veb Sajt', 'industry': 'Delatnost',
        'reg_addr': 'Registrovana Adresa', 'op_addr': 'Operativna Adresa',
        'bank_name': 'Naziv Banke', 'bank_iban': 'IBAN / Broj Računa', 'bank_swift': 'SWIFT / BIC', 'bank_addr': 'Adresa Filijale Banke', 'corr_bank': 'Korespondentna Banka',
        'turnover': 'Očekivani Godišnji Promet (USD)', 'sof': 'Poreklo Sredstava / Kapitala',
        'dir_title': 'Direktori / Rukovodioci', 'ubo_title': 'Stvarni Vlasnici (>25%)',
        'add_dir': '+ Dodaj Direktora', 'add_ubo': '+ Dodaj Vlasnika', 'dir_name': 'Puno Ime i Prezime', 'dir_pass': 'Broj Pasoša', 'dir_nat': 'Državljanstvo',
        'pep': 'Da li je bilo koji vlasnik/direktor politički izloženo lice (PEP)?',
        'sanctions': 'Da li podležete bilo kojim UN/OFAC međunarodnim sankcijama?',
        'litigation': 'Da li ste uključeni u bilo kakav AML/CFT sudski spor?',
        'dual_use': 'Da li trgujete robom dvostruke namene ili vojnom opremom?',
        'up_license': 'Trgovinska(e) Dozvola(e)', 'up_pass': 'Pasoši (Direktori i Vlasnici)', 'up_inc': 'Rešenje o Osnivanju / Osnivački Akt', 'upload_multi': 'Možete izabrati više fajlova po kategoriji',
        'decl_title': 'Izjava i Saglasnost',
        'consent': 'Izjavljujem da su podaci tačni i verodostojni, i saglasan/na sam sa bezbednim čuvanjem i obradom.',
        'btn_submit': 'Bezbedno Pošalji', 'update_req': 'Potrebna Akcija: Ažuriranje KYC Podataka', 'update_req_desc': 'Vaša dokumentacija je istekla ili zahteva obnovu.',
        // Products (my goods)
        'goods_title': 'Moji Proizvodi', 'goods_desc': 'Dodajte proizvode sa punom specifikacijom. Administrator će pregledati i objaviti u katalogu.',
        'th_prod_name': 'Proizvod', 'th_prod_price': 'Cena', 'th_prod_specs': 'Specifikacija', 'th_prod_status': 'Status', 'th_prod_actions': 'Akcije',
        'btn_new_product': '+ Dodaj Proizvod', 'btn_edit': 'Izmeni', 'no_products': 'Još uvek nema poslatih proizvoda.',
        'ptab_general': 'Osnovni Podaci', 'ptab_commercial': 'Cena i Uslovi', 'ptab_specs': 'Specifikacija i Logistika', 'ptab_packaging': 'Pakovanje i Zalihe',
        'prod_modal_title': 'Detalji Proizvoda',
        // Profile
        'profile_title': 'Podešavanja Profila', 'profile_desc': 'Zatražite izmenu vaših kontakt podataka. Za primenu izmena potrebno je odobrenje administratora.',
        'profile_email': 'Email', 'profile_phone': 'Telefon', 'profile_person': 'Kontakt Osoba', 'profile_street': 'Ulica i Broj', 'profile_city': 'Grad', 'profile_country': 'Država', 'profile_note': 'Napomena za administratora (opciono)',
        'btn_save_profile': 'Pošalji na Odobrenje',
        'profile_history': 'Vaši Zahtevi za Izmenu', 'no_profile_requests': 'Nema zahteva na čekanju.',
        // OTP
        'otp_title': 'Bezbedan Pristup Portalu', 'otp_desc': 'Jednokratni kod je poslat na vaš email.',
        'enter_code': 'Unesite 6-cifreni Kod', 'verify_btn': 'Potvrdi i Prijavi se', 'otp_sent': 'Kod je poslat. Proverite vaš email.', 'requesting': 'Zahtevanje OTP koda…',
        'uploading': 'Šifrovanje i otpremanje…',
        // Messages / errors
        'msg_product_saved': 'Proizvod je poslat na odobrenje.',
        'msg_rfq_sent': 'Upit je uspešno poslat.',
        'msg_profile_sent': 'Zahtev za izmenu je poslat administratoru.',
        'msg_kyc_saved': 'KYC podaci su bezbedno sačuvani.',
        'msg_download_logged': 'Preuzimanje je zabeleženo.',
        'err_generic': 'Došlo je do greške. Pokušajte ponovo.',
        'err_network': 'Greška u mreži. Proverite vašu konekciju.',
        'err_bad_otp': 'Neispravan ili istekao kod.',
        'err_product_required': 'Naziv proizvoda i cena su obavezni.',
        'err_rfq_required': 'Proizvod i količina su obavezni.',
        'err_invalid_email': 'Unesite ispravnu email adresu.',
        'err_no_changes': 'Unesite bar jedno polje za izmenu.',
        'err_doc_not_found': 'Dokument nije pronađen.',
        'err_doc_forbidden': 'Niste ovlašćeni za ovaj dokument.',
        'err_access_denied': 'Pristup odbijen.'
    }
};

function t(key) { return (dict[currentLang] && dict[currentLang][key]) || (dict.en && dict.en[key]) || key; }
function setText(id, text) { const el = document.getElementById(id); if (el) el.textContent = text; }

// Jezik se pamti po pregledaču (localStorage), podrazumevano engleski.
function initPortalLanguage() {
    let saved = null;
    try { saved = localStorage.getItem('aspidus_portal_lang'); } catch (e) {}
    currentLang = (saved === 'sr' || saved === 'en') ? saved : 'en';
    document.documentElement.lang = currentLang;
    updateLangToggleUI();
}

function setPortalLanguage(lang) {
    if (lang !== 'en' && lang !== 'sr') return;
    currentLang = lang;
    try { localStorage.setItem('aspidus_portal_lang', lang); } catch (e) {}
    document.documentElement.lang = lang;
    updateLangToggleUI();
    updateStaticText();
    // Ako su podaci već učitani, ponovo iscrtaj dinamički sadržaj na novom jeziku.
    if (typeof portalData !== 'undefined' && portalData) {
        if (typeof renderKycStatusLine === 'function') renderKycStatusLine();
        if (typeof renderUpdateRequestBanner === 'function') renderUpdateRequestBanner();
        if (typeof renderDashboard === 'function') renderDashboard();
        if (typeof renderDeals === 'function') renderDeals();
        if (typeof renderOffers === 'function') renderOffers();
        if (typeof renderRFQs === 'function') renderRFQs();
        if (typeof renderGoodsTable === 'function') renderGoodsTable();
        if (typeof renderDocuments === 'function') renderDocuments();
        if (typeof fillProfile === 'function') fillProfile();
    }
}

function updateLangToggleUI() {
    const btnEn = document.getElementById('lang-btn-en');
    const btnSr = document.getElementById('lang-btn-sr');
    if (btnEn) btnEn.classList.toggle('active', currentLang === 'en');
    if (btnSr) btnSr.classList.toggle('active', currentLang === 'sr');
}

function updateStaticText() {
    const T = dict[currentLang] || dict['en'];
    // Loading / header
    setText('lbl-loading', T.loading); setText('lbl-b2b', T.b2b); setText('lbl-welcome', T.welcome); setText('lbl-logout', T.logout);
    // Tabs
    setText('lbl-tab-dashboard', T.dashboard); setText('lbl-tab-shipments', T.shipments); setText('lbl-tab-offers', T.offers);
    setText('lbl-tab-rfq', T.rfq); setText('lbl-tab-docs', T.documents); setText('lbl-tab-goods', T.goods);
    setText('lbl-tab-kyc', T.kyc_tab); setText('lbl-tab-profile', T.profile);
    // Dashboard
    setText('lbl-stat-shipments', T.stat_shipments); setText('lbl-stat-offers', T.stat_offers); setText('lbl-stat-rfqs', T.stat_rfqs); setText('lbl-stat-kyc', T.stat_kyc);
    setText('lbl-recent-offers', T.recent_offers); setText('lbl-recent-docs', T.recent_docs);
    setText('lbl-view-all-offers', T.view_all_offers); setText('lbl-view-all-docs', T.view_all_docs);
    // Documents
    setText('lbl-docs-title', T.docs_title); setText('lbl-docs-desc', T.docs_desc);
    setText('th-doc-date', T.th_doc_date); setText('th-doc-type', T.th_doc_type); setText('th-doc-name', T.th_doc_name); setText('th-doc-action', T.th_doc_action);
    // RFQ
    setText('lbl-rfq-main-title', T.rfq_main_title); setText('lbl-rfq-main-desc', T.rfq_main_desc); setText('btn-new-rfq', T.btn_new_rfq);
    setText('lbl-rfq-modal-title', T.rfq_modal_title); setText('lbl-rfq-prod', T.rfq_prod); setText('lbl-rfq-qty', T.rfq_qty); setText('lbl-rfq-price', T.rfq_price); setText('lbl-rfq-notes', T.rfq_notes);
    setText('btn-rfq-send', T.btn_rfq_send); setText('btn-rfq-cancel', T.btn_rfq_cancel);
    // KYC
    setText('lbl-kyc-title', T.kyc_title); setText('lbl-kyc-desc', T.kyc_desc);
    setText('lbl-kyc-sec1', T.kyc_sec1); setText('lbl-kyc-sec2', T.kyc_sec2); setText('lbl-kyc-sec3', T.kyc_sec3); setText('lbl-kyc-sec4', T.kyc_sec4); setText('lbl-kyc-sec5', T.kyc_sec5);
    setText('lbl-reg-name', T.reg_name); setText('lbl-reg-no', T.reg_no); setText('lbl-tax-id', T.tax_id); setText('lbl-website', T.website); setText('lbl-industry', T.industry);
    setText('lbl-reg-addr', T.reg_addr); setText('lbl-op-addr', T.op_addr);
    setText('lbl-bank-name', T.bank_name); setText('lbl-bank-iban', T.bank_iban); setText('lbl-bank-swift', T.bank_swift); setText('lbl-bank-addr', T.bank_addr); setText('lbl-corr-bank', T.corr_bank);
    setText('lbl-turnover', T.turnover); setText('lbl-sof', T.sof); setText('lbl-dir-title', T.dir_title); setText('lbl-ubo-title', T.ubo_title);
    setText('btn-add-dir', T.add_dir); setText('btn-add-ubo', T.add_ubo);
    setText('lbl-pep', T.pep); setText('lbl-sanctions', T.sanctions); setText('lbl-litigation', T.litigation); setText('lbl-dualuse', T.dual_use);
    setText('lbl-up-license', T.up_license); setText('lbl-up-pass', T.up_pass); setText('lbl-up-inc', T.up_inc); setText('lbl-upload-multi', T.upload_multi);
    setText('lbl-decl-title', T.decl_title); setText('lbl-consent', T.consent); setText('btn-kyc-submit', T.btn_submit);
    setText('lbl-update-req', T.update_req); setText('lbl-update-req-desc', T.update_req_desc);
    // Products
    setText('lbl-goods-title', T.goods_title); setText('lbl-goods-desc', T.goods_desc);
    setText('th-prod-name', T.th_prod_name); setText('th-prod-price', T.th_prod_price); setText('th-prod-specs', T.th_prod_specs); setText('th-prod-status', T.th_prod_status); setText('th-prod-actions', T.th_prod_actions);
    setText('btn-new-product', T.btn_new_product);
    setText('lbl-ptab-general', T.ptab_general); setText('lbl-ptab-commercial', T.ptab_commercial); setText('lbl-ptab-specs', T.ptab_specs); setText('lbl-ptab-packaging', T.ptab_packaging);
    setText('product-modal-title', T.prod_modal_title);
    // Profile
    setText('lbl-profile-title', T.profile_title); setText('lbl-profile-desc', T.profile_desc);
    setText('lbl-profile-email', T.profile_email); setText('lbl-profile-phone', T.profile_phone); setText('lbl-profile-person', T.profile_person);
    setText('lbl-profile-street', T.profile_street); setText('lbl-profile-city', T.profile_city); setText('lbl-profile-country', T.profile_country);
    setText('lbl-profile-note', T.profile_note); setText('btn-save-profile', T.btn_save_profile);
    setText('lbl-profile-history', T.profile_history);
    // OTP
    setText('lbl-otp-title', T.otp_title); setText('lbl-otp-desc', T.otp_desc); setText('lbl-enter-code', T.enter_code); setText('btn-verify-otp', T.verify_btn);
    setText('lbl-uploading-docs', T.uploading);
}
