// static/js/core/api.js

async function handleApiError(res) {
    if (res.status === 401) {
        alert(Utils.t('misc.sessionExpired') || 'Session expired. Please login again.');
        localStorage.clear();
        window.location.reload();
        throw new Error('Unauthorized');
    }
    if (res.status === 403) {
        alert(Utils.t('users.accessDeniedEdit') || 'Access denied.');
        throw new Error('Forbidden');
    }
    if (res.status === 429) {
        alert(Utils.t('api.rateLimited') || 'Too many requests. Please wait.');
        throw new Error('Rate Limited');
    }
    if (res.status >= 500) {
        let errMsg = Utils.t('api.serverError') || 'Internal server error.';
        try { 
            const errObj = await res.json(); 
            if(errObj.error) errMsg = Utils.t(errObj.error) || errObj.error; 
        } catch(e){}
        alert(errMsg);
        throw new Error(errMsg);
    }
    if (!res.ok) {
        let errMsg = `HTTP error! status: ${res.status}`;
        try { 
            const errObj = await res.json(); 
            if(errObj.error) errMsg = Utils.t(errObj.error) || errObj.error; 
        } catch(e){}
        throw new Error(errMsg);
    }
}

async function fetchWithRetry(url, options = {}, retries = 2, delay = 1000) {
    for (let i = 0; i <= retries; i++) {
        try {
            const res = await fetch(url, options);
            if (res.status === 503 && i < retries) {
                await new Promise(r => setTimeout(r, delay * (i + 1)));
                continue;
            }
            return res;
        } catch (error) {
            if (i === retries) throw error;
            console.warn(`Network hiccup. Retrying API call to ${url} (Attempt ${i + 1} of ${retries})...`);
            await new Promise(r => setTimeout(r, delay * (i + 1)));
        }
    }
}

async function loadFromStorage() {
  try {
      for (const key of DATA_KEYS) {
          const res = await fetchWithRetry(`/api/data/${key}?t=${Date.now()}`, {}, 2, 500);
          if(!res.ok && res.status === 403) continue; 
          await handleApiError(res);
          
          let json;
          try {
              json = await res.json();
          } catch(parseError) {
              console.error(`Oštećen JSON sa servera za modul: ${key}`, parseError);
              continue; 
          }
          
          const loadedData = json.value;
          if (loadedData !== null && loadedData !== undefined) {
              if (['partners', 'products', 'deals', 'demands', 'accounts', 'transactions', 'recurringExpenses', 'connections', 'offers'].includes(key)) {
                  state.data[key] = loadedData;
              } else if (key === 'settings') {
                  state.settings = { ...state.settings, ...loadedData };
              } else {
                  state[key] = loadedData;
              }
          }
      }
      if(state.settings.lang) state.lang = state.settings.lang;
      if(state.settings.fileLimitMB) FILE_LIMIT_MB = state.settings.fileLimitMB;
  } catch(e) { 
      console.error(Utils.t('api.dbLoadError'), e); 
      if (!navigator.onLine) {
          if(typeof UI !== 'undefined' && UI.showNotification) {
              UI.showNotification(Utils.t('api.offline') || 'Offline mode. Cached data shown.', 'error');
          }
      }
  }
}

async function saveToStorage(key) {
  try {
      let dataToSave = (key in state.data) ? state.data[key] : state[key];
      const res = await fetchWithRetry(`/api/data/${key}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: dataToSave }) });
      await handleApiError(res);
  } catch(e) { 
      console.error(Utils.t('api.bulkSaveError'), e); 
      throw e; 
  }
}

async function saveSingleItem(key, item) {
    try {
        const res = await fetchWithRetry(`/api/item/${key}`, { 
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' }, 
            body: JSON.stringify(item) 
        });
        await handleApiError(res);
        return true; 
    } catch(e) { 
        console.error(Utils.t('api.singleSaveError'), e); 
        throw e; 
    }
}

async function deleteItemFromServer(key, id) {
    try {
        const res = await fetchWithRetry(`/api/item/${key}/${id}`, { method: 'DELETE' });
        await handleApiError(res);
    } catch(e) { console.error(Utils.t('api.deleteError'), e); throw e; }
}

async function deleteFileFromServer(fileUrl) {
    if (!fileUrl) return;
    try {
        const filename = fileUrl.split('/').pop();
        const res = await fetchWithRetry(`/api/upload/${filename}`, { method: 'DELETE' });
        await handleApiError(res);
    } catch(e) { console.error(Utils.t('api.fileDeleteError'), e); }
}

async function exportDatabase() {
  const exportData = {};
  for(const key of DATA_KEYS) {
      try {
          const res = await fetchWithRetry(`/api/data/${key}?t=${Date.now()}`);
          if(res.ok) {
              const json = await res.json();
              if(json.value !== null) exportData[key] = json.value;
          }
      } catch(e) {}
  }
  const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = `ASPIDUS_Database_${new Date().toISOString().slice(0,10)}.json`; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  if(typeof logClientEvent === 'function') logClientEvent('DOWNLOAD', 'database', 'Exported complete database archive (JSON)');
}

async function importDatabase(file) {
  if (!file) return; const reader = new FileReader();
  reader.onload = async (e) => {
      try {
          const importedData = JSON.parse(e.target.result);
          document.getElementById('import-btn-txt').innerText = `⏳ ${Utils.t('misc.loadingStatus')}`;
          
          for (const key of Object.keys(importedData)) {
              if (!DATA_KEYS.includes(key)) continue;
              const importedItems = importedData[key];
              
              if (typeof importedItems !== 'object' || !Array.isArray(importedItems)) {
                  if(key in state.data) state.data[key] = importedItems; else state[key] = importedItems; 
                  await saveToStorage(key); 
                  continue;
              }
              
              const currentItems = state.data[key] || []; 
              const currentItemsMap = new Map(currentItems.map(item => [item.id, item]));
              
              for (const importedItem of importedItems) {
                  if (importedItem.id && currentItemsMap.has(importedItem.id)) {
                      Object.assign(currentItemsMap.get(importedItem.id), importedItem);
                  } else if (importedItem.id && !currentItemsMap.has(importedItem.id)) {
                      currentItems.push(importedItem);
                  }
              }
              state.data[key] = currentItems;
              await saveToStorage(key);
          }
          
          document.getElementById('import-btn-txt').innerHTML = `📥 <span class="text-xs ml-1">${Utils.t('misc.importLabel')}</span>`;
          alert(Utils.t('misc.importSuccess')); 
          await loadFromStorage(); 
          render();
      } catch (err) { 
          alert(Utils.t('misc.importError') || 'Import error or corrupted file.'); 
          console.error(err); 
      }
  };
  reader.readAsText(file);
}

function parseCSVLine(line) {
    const result = []; let current = ''; let inQuotes = false;
    for(let i=0; i<line.length; i++){
        if(line[i] === '"') { inQuotes = !inQuotes; }
        else if(line[i] === ',' && !inQuotes) { result.push(current.trim().replace(/^"|"$/g, '')); current = ''; }
        else { current += line[i]; }
    }
    result.push(current.trim().replace(/^"|"$/g, ''));
    return result;
}

function importPartnersFromCSV(file) {
  if (!file) return; const reader = new FileReader();
  reader.onload = async (e) => {
      try {
          const text = e.target.result; const rows = text.split('\n').slice(1); const headers = parseCSVLine(text.split('\n')[0]);
          let addedCount = 0;
          for(const rowStr of rows) {
              if (rowStr.trim() === '') continue;
              const values = parseCSVLine(rowStr);
              const rowData = headers.reduce((obj, header, index) => { obj[header] = values[index]; return obj; }, {});
              const newPartner = {
                  id: Utils.generateId(), companyName: rowData.companyName || '', taxId: rowData.taxId || '', regNumber: rowData.regNumber || '',
                  entityType: rowData.entityType || 'company', linkedCompanyId: null,
                  address: { street: rowData.street || '', city: rowData.city || '', zip: rowData.zip || '', country: rowData.country || '' },
                  contact: { person: rowData.contactPerson || '', email: rowData.contactEmail || '', phone: rowData.phone || '' },
                  bank: { name: rowData.bankName || '', accountNumber: rowData.accountNumber || '', swift: rowData.swift || '' },
                  types: rowData.types ? rowData.types.split(';').map(t => t.trim()) : [],
                  notes: rowData.notes || '', documents: [], activities: [], lastModified: new Date().toISOString()
              };
              state.data.partners.push(newPartner);
              addedCount++;
          }
          if (addedCount > 0) {
              await saveToStorage('partners'); 
              alert(Utils.t('misc.partnersImported') || 'Partners successfully imported.'); 
              render();
          }
      } catch(err) {
          alert(Utils.t('misc.invalidCsv') || 'Invalid CSV format.');
      }
  };
  reader.readAsText(file);
}

function importProductsFromCSV(file) {
  if (!file) return; const reader = new FileReader();
  reader.onload = async (e) => {
      try {
          const text = e.target.result; const rows = text.split('\n').slice(1); const headers = parseCSVLine(text.split('\n')[0]);
          let addedCount = 0;
          for(const rowStr of rows) {
              if (rowStr.trim() === '') continue;
              const values = parseCSVLine(rowStr);
              const rowData = headers.reduce((obj, header, index) => { obj[header] = values[index]; return obj; }, {});
              if(rowData.name) {
                  const prod = {
                      id: Utils.generateId(), name: rowData.name.trim(), category: rowData.category || 'other', hsCode: rowData.hsCode || '', description: rowData.description || '', detailedSpec: '', supplyOffers: [], lastModified: new Date().toISOString()
                  };
                  state.data.products.push(prod);
                  addedCount++;
              }
          }
          if(addedCount > 0) {
              await saveToStorage('products');
              alert(Utils.t('misc.productsImported') || 'Products successfully imported.'); 
              render();
          }
      } catch(err) { alert(Utils.t('misc.invalidCsv') || 'Invalid CSV format.'); }
  };
  reader.readAsText(file);
}

function importOffersFromCSV(file) {
  if (!file) return; const reader = new FileReader();
  reader.onload = async (e) => {
      try {
          const text = e.target.result; const rows = text.split('\n').slice(1); const headers = parseCSVLine(text.split('\n')[0]);
          let changedProducts = false;
          for(const rowStr of rows) {
              if (rowStr.trim() === '') continue;
              const values = parseCSVLine(rowStr);
              const rowData = headers.reduce((obj, header, index) => { obj[header] = values[index]; return obj; }, {});
              const targetProduct = state.data.products.find(p => p.name.toLowerCase() === (rowData.productName || '').toLowerCase().trim());
              if (targetProduct) {
                  targetProduct.supplyOffers = targetProduct.supplyOffers || [];
                  targetProduct.supplyOffers.push({
                      supplierId: rowData.supplierId || '', quantity: parseFloat(rowData.quantity) || 0, price: parseFloat(rowData.price) || 0, currency: rowData.currency || 'USD', unit: rowData.unit || 'MT - Metric Ton', country: rowData.country || '', incoterm: rowData.incoterm || 'FOB', certificates: rowData.certificates || '', history: []
                  });
                  changedProducts = true;
              }
          }
          if (changedProducts) {
              await saveToStorage('products');
              alert(Utils.t('misc.offersImported') || 'Offers successfully imported.'); 
              render();
          }
      } catch(err) { alert(Utils.t('misc.invalidCsv') || 'Invalid CSV format.'); }
  };
  reader.readAsText(file);
}

async function fetchUsers() {
    try {
        const res = await fetchWithRetry('/api/users');
        await handleApiError(res);
        return await res.json();
    } catch(e) { console.error(Utils.t('api.usersLoadError'), e); return []; }
}

async function saveUser(userObj) {
    try {
        const res = await fetchWithRetry('/api/users', { 
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' }, 
            body: JSON.stringify(userObj) 
        });
        await handleApiError(res);
        return await res.json();
    } catch(e) { console.error(Utils.t('api.userSaveError'), e); throw e; }
}

async function deleteUser(id) {
    try {
        const res = await fetchWithRetry(`/api/users/${id}`, { method: 'DELETE' });
        await handleApiError(res);
        return await res.json();
    } catch(e) { console.error(Utils.t('api.userDeleteError'), e); throw e; }
}