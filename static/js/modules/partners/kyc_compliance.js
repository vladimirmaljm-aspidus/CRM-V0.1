// static/js/modules/partners/kyc_compliance.js

async function renderKycComplianceView() {
    const main = document.getElementById('main-content');
    if (!main) return;
    
    main.innerHTML = '';
    const header = Utils.createViewHeader(Utils.t('kyc.vaultTitle') || 'KYC Vault & Compliance', '', null);
    if (header.querySelector('button')) header.querySelector('button').remove();
    main.appendChild(header);

    const container = document.createElement('div');
    container.className = 'bg-white rounded-2xl shadow-xl border border-slate-200 overflow-hidden mt-6 mb-10';
    container.innerHTML = `<div class="p-10 text-center"><div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto"></div><p class="mt-4 text-slate-500 font-bold">${Utils.t('kyc.loading') || 'Fetching isolated vault data...'}</p></div>`;
    main.appendChild(container);

    try {
        const fetchFn = typeof fetchWithRetry === 'function' ? fetchWithRetry : fetch;
        const res = await fetchFn('/api/portal/admin/submissions/all');
        const submissions = await res.json();

        if (submissions.length === 0) {
            container.innerHTML = `<div class="p-10 text-center text-slate-400 font-bold">${Utils.t('kyc.noSubmissions') || 'No KYC submissions found.'}</div>`;
            return;
        }

        const rows = submissions.map(sub => {
            const isApproved = sub.data.status === 'approved';
            const badge = isApproved 
                ? `<span class="bg-green-100 text-green-800 text-[10px] font-black uppercase px-2 py-0.5 rounded border border-green-300">APPROVED</span>` 
                : `<span class="bg-amber-100 text-amber-800 text-[10px] font-black uppercase px-2 py-0.5 rounded border border-amber-300">PENDING</span>`;

            return `
            <tr class="hover:bg-slate-50 border-b border-slate-100 transition-colors">
                <td class="p-5 font-black text-slate-900">${Utils.escapeHtml(sub.partner_name)}</td>
                <td class="p-5 text-slate-500 font-bold text-xs">${new Date(sub.submitted_at).toLocaleString()}</td>
                <td class="p-5 font-mono text-slate-600 text-xs">${Utils.escapeHtml(sub.data.taxId || 'N/A')}</td>
                <td class="p-5">${badge}</td>
                <td class="p-5 text-right">
                    <button class="bg-white hover:bg-slate-100 border border-slate-300 text-slate-700 font-bold px-3 py-1.5 rounded-lg text-xs shadow-sm transition-colors mr-2" onclick="viewKycSubmission('${sub.id}')">${Utils.t('actions.view') || 'Review'}</button>
                    ${!isApproved ? `<button class="bg-blue-600 hover:bg-blue-700 text-white font-black uppercase tracking-widest px-3 py-1.5 rounded-lg text-[10px] shadow-sm transition-colors" onclick="approveAndMergeKyc('${sub.id}')">${Utils.t('kyc.approveAndMerge') || 'Approve & Merge'}</button>` : ''}
                </td>
            </tr>`;
        }).join('');

        container.innerHTML = `
        <table class="w-full text-left border-collapse">
            <thead>
                <tr class="bg-slate-50 text-slate-500 border-b border-slate-200">
                    <th class="p-5 uppercase text-[10px] font-black tracking-widest">${Utils.t('fields.company') || 'Company'}</th>
                    <th class="p-5 uppercase text-[10px] font-black tracking-widest">${Utils.t('fields.date') || 'Date Submitted'}</th>
                    <th class="p-5 uppercase text-[10px] font-black tracking-widest">Tax ID / VAT</th>
                    <th class="p-5 uppercase text-[10px] font-black tracking-widest">Status</th>
                    <th class="p-5 text-right uppercase text-[10px] font-black tracking-widest">${Utils.t('fields.actions') || 'Actions'}</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>`;
        
        window.currentKycSubmissions = submissions;

    } catch (err) {
        container.innerHTML = `<div class="p-10 text-center text-red-500 font-bold">Error loading Vault connection.</div>`;
    }
}

window.viewKycSubmission = function(subId) {
    const sub = window.currentKycSubmissions.find(s => s.id === subId);
    if (!sub) return;

    const d = sub.data;
    
    const dirsHtml = (d.directors || []).map(dir => `<div class="bg-white p-3 border rounded text-xs mb-2"><strong>${Utils.escapeHtml(dir.name)}</strong> - Passport: ${Utils.escapeHtml(dir.passport)} (${Utils.escapeHtml(dir.nationality)})</div>`).join('');
    const amlHtml = d.aml ? `
        <ul class="list-disc pl-5 text-xs font-bold text-slate-700 space-y-1">
            <li class="${d.aml.isPEP ? 'text-red-600' : 'text-emerald-600'}">PEP Status: ${d.aml.isPEP ? 'YES' : 'NO'}</li>
            <li class="${d.aml.isSanctioned ? 'text-red-600' : 'text-emerald-600'}">Sanctions: ${d.aml.isSanctioned ? 'YES' : 'NO'}</li>
            <li class="${d.aml.litigation ? 'text-red-600' : 'text-emerald-600'}">Litigation: ${d.aml.litigation ? 'YES' : 'NO'}</li>
            <li class="${d.aml.dualUse ? 'text-orange-600' : 'text-emerald-600'}">Dual Use Goods: ${d.aml.dualUse ? 'YES' : 'NO'}</li>
        </ul>` : 'N/A';

    const html = `
    <div class="space-y-6">
        <div class="grid grid-cols-2 gap-4 bg-slate-50 p-6 rounded-xl border border-slate-200">
            <div><p class="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1">${Utils.t('kyc.regName') || 'Company Name'}</p><p class="font-black text-slate-900">${Utils.escapeHtml(d.companyName)}</p></div>
            <div><p class="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1">${Utils.t('kyc.taxId') || 'Tax ID / VAT'}</p><p class="font-mono font-bold text-slate-700">${Utils.escapeHtml(d.taxId)}</p></div>
            <div><p class="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1">${Utils.t('kyc.regNo') || 'Registration No'}</p><p class="font-mono font-bold text-slate-700">${Utils.escapeHtml(d.regNo)}</p></div>
            <div><p class="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1">${Utils.t('kyc.regAddr') || 'Address'}</p><p class="font-bold text-slate-700">${Utils.escapeHtml(d.regAddr)}</p></div>
        </div>
        
        <div class="bg-blue-50 p-6 rounded-xl border border-blue-100">
            <h4 class="font-black text-blue-900 uppercase text-[10px] tracking-widest mb-3">${Utils.t('kyc.bankingDetails') || 'Banking Information'}</h4>
            <p class="text-sm"><strong>Bank:</strong> ${Utils.escapeHtml(d.bankName)}</p>
            <p class="text-sm font-mono mt-1"><strong>IBAN:</strong> ${Utils.escapeHtml(d.bankIban)}</p>
            <p class="text-sm font-mono mt-1"><strong>SWIFT:</strong> ${Utils.escapeHtml(d.bankSwift)}</p>
        </div>

        <div class="grid grid-cols-2 gap-6">
            <div class="bg-slate-50 p-6 rounded-xl border border-slate-200">
                <h4 class="font-black text-slate-800 uppercase text-[10px] tracking-widest mb-3">${Utils.t('kyc.directors') || 'Directors / Management'}</h4>
                ${dirsHtml || `<p class="text-xs text-slate-500 italic">${Utils.t('kyc.notProvided') || 'None specified'}</p>`}
            </div>
            <div class="bg-slate-50 p-6 rounded-xl border border-slate-200">
                <h4 class="font-black text-slate-800 uppercase text-[10px] tracking-widest mb-3">AML / CFT Flags</h4>
                ${amlHtml}
            </div>
        </div>
    </div>`;

    Utils.openModal(`KYC Review: ${d.companyName}`, html, 'max-w-3xl');
};

window.approveAndMergeKyc = async function(subId) {
    if (!confirm(Utils.t('kyc.confirmMerge') || 'Are you sure you want to merge this KYC data into the official CRM partner profile?')) return;

    try {
        const fetchFn = typeof fetchWithRetry === 'function' ? fetchWithRetry : fetch;
        const res = await fetchFn(`/api/portal/admin/submissions/approve/${subId}`, { method: 'POST' });
        const result = await res.json();
        
        if (res.ok) {
            alert(Utils.t('misc.success') + ": " + result.message);
            loadData(); // Re-loads overall state.data to reflect changes
            renderKycComplianceView(); // Refresh list
        } else {
            alert("Error: " + result.error);
        }
    } catch (err) {
        alert(Utils.t('api.serverError') || "Network communication error.");
    }
};

window.reviewKYC = async function(partnerId) {
    const partner = state.data.partners.find(p => p.id === partnerId);
    
    if (!partner) {
        alert(Utils.t('kyc.notFound'));
        return;
    }

    Utils.openModal(
        Utils.t('kyc.loading'), 
        `<div class="p-16 text-center">
            <div class="animate-spin rounded-full h-12 w-12 border-b-4 border-blue-600 mx-auto mb-4"></div>
            <p class="text-slate-500 font-bold uppercase tracking-widest text-xs">${Utils.t('kyc.connecting')}</p>
        </div>`, 
        null
    );

    try {
        let submissions = [];
        const fetchFn = typeof fetchWithRetry === 'function' ? fetchWithRetry : fetch;
        // Povezivanje na backend rutu
        const res = await fetchFn(`/api/portal/admin/submissions/${partnerId}`);
        
        if (res.ok) {
            const jsonRes = await res.json();
            if (Array.isArray(jsonRes)) {
                submissions = jsonRes;
            }
        } else {
            console.warn("API Error:", res.status);
        }

        if (!Array.isArray(submissions) || submissions.length === 0) {
            const noDataHtml = `
            <div class="p-12 text-center bg-slate-50 rounded-xl border border-slate-200 shadow-inner">
                <span class="text-5xl block mb-4">📭</span>
                <h3 class="text-xl font-black text-slate-900 uppercase tracking-widest">${Utils.t('kyc.noSubmissions')}</h3>
                <p class="text-slate-500 font-bold mt-2 text-sm">${Utils.t('kyc.noSubmissionsDesc')}</p>
            </div>`;
            Utils.openModal(Utils.t('kyc.reviewTitle'), noDataHtml, null);
            return;
        }

        const latest = submissions[0];
        const data = latest.data || {};
        const aml = data.aml || {};
        const files = data.files || {};

        const renderFiles = (fileData, label) => {
            if (!fileData) return '';
            let fileArr = Array.isArray(fileData) ? fileData : [fileData];
            fileArr = fileArr.filter(f => f); 
            if (fileArr.length === 0) return '';
            
            return fileArr.map((url, i) => `
                <a href="${url}" target="_blank" class="inline-flex items-center gap-2 bg-white border border-slate-300 hover:bg-blue-50 hover:border-blue-300 text-slate-700 hover:text-blue-700 font-bold px-3 py-1.5 rounded-lg text-[10px] uppercase tracking-wider transition-colors shadow-sm mb-2 mr-2">
                    📄 ${label} ${fileArr.length > 1 ? (i+1) : ''}
                </a>
            `).join('');
        };

        const renderPersons = (persons) => {
            if (!persons || !Array.isArray(persons) || persons.length === 0) return `<p class="text-[10px] text-slate-400 italic">${Utils.t('kyc.notProvided')}</p>`;
            return `
            <table class="w-full text-left border-collapse mt-2">
                <thead>
                    <tr class="bg-slate-100 border-b border-slate-200">
                        <th class="p-2 text-[9px] font-black text-slate-500 uppercase tracking-widest">${Utils.t('kyc.fullName')}</th>
                        <th class="p-2 text-[9px] font-black text-slate-500 uppercase tracking-widest">${Utils.t('kyc.passport')}</th>
                        <th class="p-2 text-[9px] font-black text-slate-500 uppercase tracking-widest">${Utils.t('kyc.nationality')}</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-slate-100 bg-white">
                    ${persons.map(p => `
                    <tr class="hover:bg-slate-50 transition-colors">
                        <td class="p-2 text-sm font-bold text-slate-800">${Utils.escapeHtml(p.name || p.dirName || p.uboName)}</td>
                        <td class="p-2 text-xs font-mono text-slate-600">${Utils.escapeHtml(p.passport || p.dirPassport || p.uboPassport)}</td>
                        <td class="p-2 text-xs font-bold text-slate-600">${Utils.escapeHtml(p.nationality || p.dirNat || p.uboNat)}</td>
                    </tr>
                    `).join('')}
                </tbody>
            </table>`;
        };

        const renderAmlBadge = (val, trueText, falseText) => {
            if (val) return `<span class="bg-red-50 text-red-700 border border-red-200 px-2 py-1 rounded text-[10px] font-black uppercase tracking-widest shadow-sm">⚠️ ${trueText}</span>`;
            return `<span class="bg-emerald-50 text-emerald-700 border border-emerald-200 px-2 py-1 rounded text-[10px] font-black uppercase tracking-widest shadow-sm">✅ ${falseText}</span>`;
        };

        const currentKyc = partner.kyc || {};
        const subDate = latest.submitted_at ? new Date(latest.submitted_at).toLocaleString(Utils.getLang() === 'sr' ? 'sr-RS' : 'en-US') : 'Unknown';

        const html = `
        <div class="flex flex-col md:flex-row h-[85vh] bg-slate-50 rounded-b-2xl overflow-hidden">
            <div class="flex-1 overflow-y-auto custom-scrollbar p-6 md:p-8 border-r border-slate-200">
                <div class="flex justify-between items-center mb-8 border-b border-slate-200 pb-5">
                    <div>
                        <h3 class="text-2xl font-black text-slate-900 tracking-tight">${Utils.t('kyc.dossier')}</h3>
                        <p class="text-xs font-bold text-slate-500 uppercase tracking-widest mt-1">${Utils.escapeHtml(partner.companyName)}</p>
                    </div>
                    <div class="text-right bg-white p-3 rounded-lg border border-slate-200 shadow-sm">
                        <p class="text-[9px] font-black text-slate-400 uppercase tracking-widest">${Utils.t('kyc.submissionDate')}</p>
                        <p class="text-sm font-bold text-blue-600">${subDate}</p>
                    </div>
                </div>

                <div class="bg-white border border-slate-200 rounded-xl p-6 shadow-sm mb-6">
                    <h4 class="font-black text-slate-800 uppercase text-[10px] tracking-widest mb-5 pb-3 border-b border-slate-100 flex items-center gap-2">🏢 ${Utils.t('kyc.corpData')}</h4>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-5">
                        <div class="col-span-2"><span class="block text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">${Utils.t('kyc.regName')}</span><strong class="text-sm text-slate-800">${Utils.escapeHtml(data.companyName)}</strong></div>
                        <div class="col-span-2"><span class="block text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">${Utils.t('kyc.industry')}</span><strong class="text-sm text-slate-800">${Utils.escapeHtml(data.industry || 'N/A')}</strong></div>
                        
                        <div class="col-span-2 md:col-span-1"><span class="block text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">${Utils.t('kyc.regNo')}</span><strong class="text-sm font-mono text-slate-800">${Utils.escapeHtml(data.regNo)}</strong></div>
                        <div class="col-span-2 md:col-span-1"><span class="block text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">${Utils.t('kyc.taxId')}</span><strong class="text-sm font-mono text-slate-800">${Utils.escapeHtml(data.taxId || 'N/A')}</strong></div>
                        <div class="col-span-2"><span class="block text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">${Utils.t('kyc.website')}</span><a href="${Utils.escapeHtml(data.website || '#')}" target="_blank" class="text-sm font-bold text-blue-600 hover:underline">${Utils.escapeHtml(data.website || 'N/A')}</a></div>

                        <div class="col-span-2 md:col-span-4"><span class="block text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">${Utils.t('kyc.regAddr')}</span><strong class="text-sm text-slate-800">${Utils.escapeHtml(data.regAddr)}</strong></div>
                        <div class="col-span-2 md:col-span-4"><span class="block text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">${Utils.t('kyc.opAddr')}</span><strong class="text-sm text-slate-800">${Utils.escapeHtml(data.opAddr || Utils.t('kyc.sameAsReg'))}</strong></div>
                    </div>
                </div>

                <div class="bg-white border border-slate-200 rounded-xl p-6 shadow-sm mb-6">
                    <h4 class="font-black text-slate-800 uppercase text-[10px] tracking-widest mb-5 pb-3 border-b border-slate-100 flex items-center gap-2">💰 ${Utils.t('kyc.finProfile')}</h4>
                    <div class="grid grid-cols-2 gap-5 mb-5">
                        <div><span class="block text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">${Utils.t('kyc.turnover')}</span><strong class="text-base text-emerald-700 font-black">${Utils.formatCurrency(data.turnover, 'USD')}</strong></div>
                        <div><span class="block text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">${Utils.t('kyc.sourceOfFunds')}</span><strong class="text-sm text-slate-800">${Utils.escapeHtml(data.sourceOfFunds)}</strong></div>
                    </div>
                    <div class="bg-slate-50 border border-slate-200 p-5 rounded-lg shadow-inner">
                        <span class="block text-[10px] font-black text-blue-800 uppercase tracking-widest mb-3">${Utils.t('kyc.bankingDetails')}</span>
                        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <div class="md:col-span-3"><strong class="text-sm text-slate-900 block">${Utils.escapeHtml(data.bankName)}</strong><span class="text-xs text-slate-500">${Utils.escapeHtml(data.bankAddr || '')}</span></div>
                            <div><span class="block text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">IBAN / ACC</span><strong class="text-sm font-mono text-slate-800">${Utils.escapeHtml(data.bankIban)}</strong></div>
                            <div><span class="block text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">SWIFT</span><strong class="text-sm font-mono text-slate-800">${Utils.escapeHtml(data.bankSwift)}</strong></div>
                            <div><span class="block text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">${Utils.t('kyc.corrBank')}</span><strong class="text-sm text-slate-800">${Utils.escapeHtml(data.corrBank || 'N/A')}</strong></div>
                        </div>
                    </div>
                </div>

                <div class="bg-white border border-slate-200 rounded-xl p-6 shadow-sm mb-6">
                    <h4 class="font-black text-slate-800 uppercase text-[10px] tracking-widest mb-5 pb-3 border-b border-slate-100 flex items-center gap-2">👥 ${Utils.t('kyc.structure')}</h4>
                    <div class="mb-5">
                        <span class="block text-[10px] font-black text-blue-700 uppercase tracking-widest mb-2 bg-blue-50 inline-block px-2 py-0.5 rounded border border-blue-100">${Utils.t('kyc.directors')}</span>
                        ${renderPersons(data.directors || [{name: data.dirName, passport: data.dirPassport, nationality: data.dirNat}])}
                    </div>
                    <div>
                        <span class="block text-[10px] font-black text-emerald-700 uppercase tracking-widest mb-2 bg-emerald-50 inline-block px-2 py-0.5 rounded border border-emerald-100">${Utils.t('kyc.ubos')}</span>
                        ${renderPersons(data.ubos || [{name: data.uboName, passport: data.uboPassport, nationality: data.uboNat}])}
                    </div>
                </div>

                <div class="bg-white border border-slate-200 rounded-xl p-6 shadow-sm mb-6">
                    <h4 class="font-black text-slate-800 uppercase text-[10px] tracking-widest mb-5 pb-3 border-b border-slate-100 flex items-center gap-2">🚨 AML / CFT Skrining</h4>
                    <div class="flex flex-col gap-3">
                        <div class="flex justify-between items-center p-2.5 hover:bg-slate-50 rounded-lg transition-colors border border-transparent hover:border-slate-200"><span class="text-xs font-bold text-slate-600">${Utils.t('kyc.pep')}</span> ${renderAmlBadge(aml.isPEP || data.isPEP, Utils.t('kyc.yes'), Utils.t('kyc.no'))}</div>
                        <div class="flex justify-between items-center p-2.5 hover:bg-slate-50 rounded-lg transition-colors border border-transparent hover:border-slate-200"><span class="text-xs font-bold text-slate-600">${Utils.t('kyc.sanctions')}</span> ${renderAmlBadge(aml.isSanctioned || data.isSanctioned, Utils.t('kyc.yes'), Utils.t('kyc.no'))}</div>
                        <div class="flex justify-between items-center p-2.5 hover:bg-slate-50 rounded-lg transition-colors border border-transparent hover:border-slate-200"><span class="text-xs font-bold text-slate-600">${Utils.t('kyc.litigation')}</span> ${renderAmlBadge(aml.litigation, Utils.t('kyc.yes'), Utils.t('kyc.no'))}</div>
                        <div class="flex justify-between items-center p-2.5 hover:bg-slate-50 rounded-lg transition-colors border border-transparent hover:border-slate-200"><span class="text-xs font-bold text-slate-600">${Utils.t('kyc.dualUse')}</span> ${renderAmlBadge(aml.dualUse, Utils.t('kyc.yes'), Utils.t('kyc.no'))}</div>
                    </div>
                </div>

                <div class="bg-white border border-slate-200 rounded-xl p-6 shadow-sm mb-6">
                    <h4 class="font-black text-slate-800 uppercase text-[10px] tracking-widest mb-5 pb-3 border-b border-slate-100 flex items-center gap-2">📎 ${Utils.t('kyc.attachedDocs')}</h4>
                    <div class="mb-4">
                        <span class="block text-[10px] font-black text-slate-400 uppercase tracking-widest mb-2">${Utils.t('kyc.tradeLicenses')}</span>
                        <div class="flex flex-wrap">${renderFiles(files.licenses || files.license, 'License')}</div>
                    </div>
                    <div class="mb-4">
                        <span class="block text-[10px] font-black text-slate-400 uppercase tracking-widest mb-2">${Utils.t('kyc.passportsDoc')}</span>
                        <div class="flex flex-wrap">${renderFiles(files.passports || files.passport, 'Passport')}</div>
                    </div>
                    <div>
                        <span class="block text-[10px] font-black text-slate-400 uppercase tracking-widest mb-2">${Utils.t('kyc.incorpDocs')}</span>
                        <div class="flex flex-wrap">${renderFiles(files.incorporation, 'Incorp_Doc')}</div>
                    </div>
                </div>
                
                <div class="bg-blue-50 border border-blue-200 rounded-xl p-6 shadow-inner">
                    <h4 class="font-black text-blue-800 uppercase text-[10px] tracking-widest mb-3 flex items-center gap-2">✍️ ${Utils.t('kyc.declaration')}</h4>
                    <p class="text-sm font-bold text-slate-800">${Utils.escapeHtml(data.submitterName)} <span class="text-xs text-slate-500 ml-2 border-l border-slate-300 pl-2">${Utils.escapeHtml(data.submitterTitle)}</span></p>
                    <p class="text-[10px] text-blue-700 font-bold mt-2 uppercase tracking-widest">✅ ${Utils.t('kyc.consent')}</p>
                </div>
            </div>

            <form id="kyc-action-form" class="w-full md:w-80 bg-white border-t md:border-t-0 md:border-l border-slate-200 flex flex-col flex-shrink-0 z-10 shadow-[-4px_0_15px_rgba(0,0,0,0.03)]">
                <div class="p-6 border-b border-slate-200 bg-slate-50">
                    <h3 class="text-sm font-black text-slate-800 uppercase tracking-widest">${Utils.t('kyc.complianceActions')}</h3>
                    <p class="text-[10px] text-slate-500 font-bold mt-1">${Utils.t('kyc.dashboard')}</p>
                </div>
                
                <div class="flex-1 p-6 space-y-6 overflow-y-auto">
                    <div>
                        <label class="block text-[10px] font-black text-slate-400 uppercase tracking-widest mb-2">${Utils.t('kyc.riskLevel')}</label>
                        <select name="riskLevel" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-3 text-sm font-bold text-slate-800 outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer shadow-sm transition-all">
                            <option value="low" ${currentKyc.riskLevel === 'low' ? 'selected' : ''}>🟢 ${Utils.t('kyc.lowRisk')}</option>
                            <option value="medium" ${currentKyc.riskLevel === 'medium' ? 'selected' : ''}>🟡 ${Utils.t('kyc.mediumRisk')}</option>
                            <option value="high" ${currentKyc.riskLevel === 'high' ? 'selected' : ''}>🔴 ${Utils.t('kyc.highRisk')}</option>
                        </select>
                    </div>

                    <div>
                        <label class="block text-[10px] font-black text-slate-400 uppercase tracking-widest mb-2">${Utils.t('kyc.notesPlaceholder')}</label>
                        <textarea name="notes" rows="8" class="w-full bg-white border border-slate-300 rounded-lg px-4 py-3 text-sm font-medium text-slate-800 outline-none focus:ring-2 focus:ring-blue-500 shadow-sm leading-relaxed transition-all" placeholder="${Utils.t('kyc.notesPlaceholder')}">${Utils.escapeHtml(currentKyc.notes || '')}</textarea>
                    </div>
                </div>

                <div class="p-5 border-t border-slate-200 bg-slate-50 flex flex-col gap-3">
                    <button type="button" class="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-black py-3.5 rounded-xl shadow-md uppercase tracking-widest text-[11px] transition-transform transform hover:-translate-y-0.5" onclick="submitKycDecision('approved')">
                        ✅ ${Utils.t('kyc.approve')}
                    </button>
                    <button type="button" class="w-full bg-amber-500 hover:bg-amber-600 text-white font-black py-3.5 rounded-xl shadow-md uppercase tracking-widest text-[11px] transition-transform transform hover:-translate-y-0.5" onclick="submitKycDecision('update_requested')">
                        ⚠️ ${Utils.t('kyc.requestUpdate')}
                    </button>
                    <button type="button" class="w-full bg-red-600 hover:bg-red-700 text-white font-black py-3.5 rounded-xl shadow-md uppercase tracking-widest text-[11px] transition-transform transform hover:-translate-y-0.5" onclick="submitKycDecision('rejected')">
                        ❌ ${Utils.t('kyc.reject')}
                    </button>
                </div>
            </form>
        </div>`;

        const mBody = document.getElementById('modal-body');
        if(mBody) { mBody.classList.remove('p-6'); mBody.classList.add('p-0'); }

        Utils.openModal(Utils.t('kyc.reviewTitle'), html, null);

        const oldClose = window.closeModal;
        window.closeModal = function() {
            if(mBody) { mBody.classList.add('p-6'); mBody.classList.remove('p-0'); }
            oldClose();
            window.closeModal = oldClose; 
        };

        window.submitKycDecision = async function(decisionStatus) {
            const form = document.getElementById('kyc-action-form');
            const fd = new FormData(form);
            
            partner.kyc = {
                status: decisionStatus,
                riskLevel: fd.get('riskLevel'),
                notes: fd.get('notes'),
                lastChecked: new Date().toISOString()
            };

            const statusLabel = {
                'approved': 'APPROVED',
                'rejected': 'REJECTED',
                'update_requested': 'UPDATE_REQUESTED'
            }[decisionStatus];

            partner.activities = partner.activities || [];
            partner.activities.unshift({
                id: Utils.generateId(),
                date: new Date().toISOString(),
                type: 'Compliance Review',
                note: `KYC Status updated to: ${statusLabel}, Risk: ${fd.get('riskLevel').toUpperCase()}`
            });

            try {
                const btn = document.activeElement;
                if(btn) { btn.disabled = true; btn.innerText = `⏳ ...`; }

                if (typeof saveSingleItem === 'function') {
                    await saveSingleItem('partners', partner);
                }
                Utils.closeModal();
                if (typeof renderPartnersView === 'function') renderPartnersView();
                if (typeof renderPartnerDetailView === 'function' && state.currentView === 'partnerDetail') renderPartnerDetailView(partner.id);
                if (typeof UI !== 'undefined' && UI.showNotification) UI.showNotification(`${Utils.t('kyc.statusUpdated')} ${statusLabel}`);
            } catch (e) {
                console.error("Error:", e);
                alert(Utils.t('kyc.saveError'));
            }
        };

    } catch (globalErr) {
        console.error("Error:", globalErr);
        alert(Utils.t('api.serverError'));
        Utils.closeModal();
    }
};