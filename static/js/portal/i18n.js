const dict = {
    'en': {
        'loading': 'Loading Secure Vault...', 'b2b': 'B2B SECURE PORTAL', 'welcome': 'Welcome',
        'shipments': '🚢 Shipments', 'offers': '💎 Firm Offers', 'rfq': '📝 My RFQs', 'kyc_tab': '🛡️ KYC Vault', 'goods': '📦 Our Goods', 'profile': '⚙️ Profile Settings',
        'documents': '📁 My Documents', 
        'docs_title': 'Document Vault', 'docs_desc': 'Access and download your official invoices, contracts, and offers.', 
        'th_doc_date': 'Date', 'th_doc_type': 'Document Type', 'th_doc_name': 'File Name', 'th_doc_action': 'Action', 'no_docs': 'No official documents generated yet.', 'btn_download': 'DOWNLOAD',
        'no_deals': 'No active shipments found.', 'no_offers': 'No active offers found.', 'no_rfq': 'No active RFQs found.',
        'status': 'Status', 'vessel': 'Vessel / Voyage', 'bl': 'B/L Number', 'pol': 'Port of Loading', 'pod': 'Port of Discharge', 'ship_date': 'Ship Date', 'qty': 'Quantity', 'contract': 'Contract No.',
        'offer_no': 'Offer No.', 'valid': 'Valid Until', 'price': 'Price', 'incoterm': 'Incoterm',
        'rfq_main_title': 'Requests for Quotation', 'rfq_main_desc': 'Submit and track your purchasing demands.', 'btn_new_rfq': '+ New RFQ', 'rfq_modal_title': 'Submit New RFQ',
        'rfq_prod': 'Product Name / Commodity', 'rfq_qty': 'Target Quantity', 'rfq_price': 'Target Price (Optional)', 'rfq_notes': 'Notes / Specifications', 'btn_rfq_send': 'Send Request',
        'kyc_title': 'Compliance & KYC Form', 'kyc_desc': 'Securely submit your corporate data. Stored in an air-gapped vault.',
        'reg_name': 'Registered Company Name', 'reg_no': 'Registration / License No.', 'tax_id': 'Tax ID / VAT Number', 'website': 'Company Website', 'industry': 'Industry Sector / Business Activity',
        'reg_addr': 'Registered Address', 'op_addr': 'Operational Address (If different)',
        'bank_title': 'Primary Bank Account', 'bank_addr': 'Bank Branch Address', 'corr_bank': 'Correspondent Bank (Optional)',
        'turnover': 'Expected Annual Volume (USD)', 'sof': 'Main Source of Funds / Wealth',
        'dir_title': 'Company Directors / Managers', 'ubo_title': 'Ultimate Beneficial Owners (UBO > 25%)',
        'add_dir': '+ Add Director', 'add_ubo': '+ Add UBO', 'dir_name': 'Full Legal Name', 'dir_pass': 'Passport Number', 'dir_nat': 'Nationality',
        'aml_title': 'AML / CFT Compliance', 'pep': 'Is any owner or director a Politically Exposed Person (PEP)?', 'sanctions': 'Is the company subject to any UN/OFAC international sanctions?',
        'litigation': 'Has the company been involved in any AML/CFT litigation?', 'dual_use': 'Do you trade in Dual-Use goods or military equipment?',
        'up_license': 'Trade License(s)', 'up_pass': 'Passports (Directors & UBOs)', 'up_inc': 'Cert. of Incorporation / MoA', 'upload_multi': 'You can select multiple files per category',
        'decl_title': 'Declaration & Consent', 'consent': 'I declare that the information provided is true and accurate. I explicitly consent to the secure storage and processing of this data by Aspidus CRM.',
        'btn_submit': 'SECURE SUBMIT', 'update_req': 'ACTION REQUIRED: KYC EXPIRED', 'update_req_desc': 'Your documentation has expired or requires renewal.',
        'otp_title': 'Secure Portal Access', 'otp_desc': 'We have sent a 6-digit One-Time Password to your registered email.',
        'enter_code': 'Enter 6-digit Code', 'verify_btn': 'VERIFY & LOGIN', 'otp_sent': 'Code Generated. Please check your email or server console.', 'requesting': 'Requesting OTP...',
        'uploading': 'UPLOADING & ENCRYPTING DATA...'
    }
};

function t(key) { return dict[currentLang][key] || key; }
function setText(id, text) { const el = document.getElementById(id); if(el) el.innerText = text; }

function updateStaticText() {
    setText('lbl-loading', t('loading')); setText('lbl-b2b', t('b2b')); setText('lbl-welcome', t('welcome'));
    setText('tab-btn-shipments', t('shipments')); setText('tab-btn-offers', t('offers')); setText('tab-btn-rfq', t('rfq')); setText('tab-btn-kyc', t('kyc_tab')); setText('tab-btn-goods', t('goods')); setText('tab-btn-profile', t('profile'));
    setText('tab-btn-docs', t('documents'));

    setText('lbl-docs-title', t('docs_title')); setText('lbl-docs-desc', t('docs_desc'));
    setText('th-doc-date', t('th_doc_date')); setText('th-doc-type', t('th_doc_type')); setText('th-doc-name', t('th_doc_name')); setText('th-doc-action', t('th_doc_action'));
    
    setText('lbl-rfq-main-title', t('rfq_main_title')); setText('lbl-rfq-main-desc', t('rfq_main_desc')); setText('btn-new-rfq', t('btn_new_rfq'));
    setText('lbl-rfq-modal-title', t('rfq_modal_title')); setText('lbl-rfq-prod', t('rfq_prod')); setText('lbl-rfq-qty', t('rfq_qty')); setText('lbl-rfq-price', t('rfq_price')); setText('lbl-rfq-notes', t('rfq_notes')); setText('btn-rfq-send', t('btn_rfq_send'));

    setText('lbl-kyc-title', t('kyc_title')); setText('lbl-kyc-desc', t('kyc_desc'));
    setText('lbl-reg-name', t('reg_name')); setText('lbl-reg-no', t('reg_no')); setText('lbl-tax-id', t('tax_id')); setText('lbl-website', t('website')); setText('lbl-industry', t('industry'));
    setText('lbl-reg-addr', t('reg_addr')); setText('lbl-op-addr', t('op_addr')); setText('lbl-bank-title', t('bank_title')); 
    const kba = document.getElementById('kyc-bank-addr'); if(kba) kba.placeholder = t('bank_addr'); 
    const kcb = document.getElementById('kyc-corr-bank'); if(kcb) kcb.placeholder = t('corr_bank');
    setText('lbl-turnover', t('turnover')); setText('lbl-sof', t('sof')); setText('lbl-dir-title', t('dir_title')); setText('lbl-ubo-title', t('ubo_title'));
    setText('btn-add-dir', t('add_dir')); setText('btn-add-ubo', t('add_ubo')); setText('lbl-aml-title', t('aml_title')); setText('lbl-pep', t('pep')); setText('lbl-sanctions', t('sanctions')); setText('lbl-litigation', t('litigation')); setText('lbl-dualuse', t('dual_use'));
    setText('lbl-up-license', t('up_license')); setText('lbl-up-pass', t('up_pass')); setText('lbl-up-inc', t('up_inc')); setText('lbl-upload-multi', t('upload_multi'));
    setText('lbl-decl-title', t('decl_title')); setText('lbl-consent', t('consent')); setText('btn-kyc-submit', t('btn_submit'));
    setText('lbl-update-req', t('update_req')); setText('lbl-update-req-desc', t('update_req_desc'));
    setText('lbl-otp-title', t('otp_title')); setText('lbl-otp-desc', t('otp_desc')); setText('lbl-enter-code', t('enter_code')); setText('btn-verify-otp', t('verify_btn'));
    setText('lbl-uploading-docs', t('uploading'));
}