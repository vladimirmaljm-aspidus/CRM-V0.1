function downloadPartnerTemplate() {
    const csvContent = "companyName,taxId,regNumber,entityType,street,city,zip,country,contactPerson,contactEmail,phone,bankName,accountNumber,swift,types,notes\nSample Company LLC,123456789,1234567,company,Main St 1,City,11000,Country,John Doe,john@example.com,060123456,Bank Name,160-123-45,,Buyer;Supplier,Sample Note";
    const blob = new Blob([csvContent], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `ASPIDUS_Partners_Template.csv`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    if(typeof logClientEvent === 'function') logClientEvent('DOWNLOAD', 'partners', 'Downloaded Partners CSV Template');
}

function downloadProductTemplate() {
    const csvContent = "name,category,hsCode,description\nSample Cocoa Beans,agriculture,18010000,High quality cocoa beans\nSample Sugar ICUMSA 45,food,17019910,White refined sugar";
    const blob = new Blob([csvContent], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `ASPIDUS_Products_Template.csv`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    if(typeof logClientEvent === 'function') logClientEvent('DOWNLOAD', 'products', 'Downloaded Products CSV Template');
}

function downloadOfferTemplate() {
    const csvContent = "productName,price,currency,unit,country,incoterm,certificates\nSample Cocoa Beans,2500,USD,t,Ghana,FOB,Fairtrade;Organic\nSample Sugar ICUMSA 45,450,USD,t,Brazil,CIF,SGS Inspected";
    const blob = new Blob([csvContent], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `ASPIDUS_Offers_Template.csv`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    if(typeof logClientEvent === 'function') logClientEvent('DOWNLOAD', 'offers', 'Downloaded Offers CSV Template');
}

function generateCashFlowReport() {
    const rows = [[t('cashflow.date'), t('cashflow.description'), t('cashflow.category'), t('cashflow.type'), t('finances.amount'), t('cashflow.currency')]];
    state.data.transactions.forEach(tr => {
        rows.push([
            tr.date,
            `"${(tr.description||'').replace(/"/g, '""')}"`,
            tr.category || '-',
            tr.type === 'income' ? t('cashflow.income') : (tr.type === 'expense' ? t('cashflow.expense') : t('cashflow.transfer_type')),
            tr.amount || 0,
            tr.currency
        ]);
    });
    const csvContent = rows.map(e => e.join(",")).join("\n");
    const blob = new Blob([csvContent], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; 
    a.download = `ASPIDUS_Cashflow_Report_${new Date().toISOString().slice(0,10)}.csv`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    if(typeof logClientEvent === 'function') logClientEvent('DOWNLOAD', 'finances', 'Downloaded Cashflow Report (CSV)');
}

async function exportDatabase() {
  const exportData = {};
  const failed = [];
  for(const key of DATA_KEYS) {
      try {
          const res = await fetch(`/api/data/${key}?t=${Date.now()}`);
          if (!res.ok) { failed.push(`${key} (HTTP ${res.status})`); continue; }
          const json = await res.json();
          if(json.value !== null && json.value !== undefined) exportData[key] = json.value;
      } catch (e) {
          failed.push(`${key} (${e.message || e})`);
      }
  }
  const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url;
  a.download = `ASPIDUS_Database_Backup_${new Date().toISOString().slice(0,10)}.json`;
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  if(typeof logClientEvent === 'function') logClientEvent('DOWNLOAD', 'database', `Exported ${Object.keys(exportData).length} tables (${failed.length} failed)`);
  if (failed.length && typeof showToast === 'function') {
      showToast(`⚠ Backup preuzet, ali ${failed.length} tabela nije uspelo: ${failed.slice(0,3).join(', ')}${failed.length>3?'…':''}`, 'warning', 8000);
  } else if (typeof showToast === 'function') {
      showToast(`✓ Backup preuzet (${Object.keys(exportData).length} tabela)`, 'success');
  }
}
window._exportDatabaseImpl = exportDatabase;