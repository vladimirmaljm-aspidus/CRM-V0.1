// static/js/core/pdf_generator.js
async function generateNativePDF(data, filename, action = 'download') {
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF('p', 'mm', 'a4');

    // 1. STRIKTNA DVOJEZIČNOST - Direktno ugrađen rečnik za PDF
    const lang = (typeof state !== 'undefined' && state.lang) ? state.lang : 'en';
    const dict = {
        'en': {
            'offer': 'FIRM OFFER', 'proforma': 'PROFORMA INVOICE', 'commercial': 'COMMERCIAL INVOICE',
            'inv_no': 'Document No.', 'date': 'Date of Issue', 'due': 'Due Date', 'po': 'PO Number',
            'bill_to': 'BILL TO (BUYER):', 'ship_to': 'SHIP TO (CONSIGNEE):', 'same_as_buyer': 'SAME AS BUYER',
            'tax_id': 'Tax ID / VAT', 'reg_no': 'Reg. No.', 'product': 'Product', 'hs': 'HS Code', 'origin': 'Origin',
            'incoterm': 'Incoterm', 'pol': 'Port of Loading', 'pod': 'Port of Discharge', 'vessel': 'Vessel / Voyage',
            'bl': 'B/L Number', 'ship_date': 'Shipment Date', 'pack': 'Packaging', 'pay_terms': 'Payment Terms',
            'net': 'Net Weight', 'gross': 'Gross Weight', 'vol': 'Volume', 'desc': 'Description / Specification', 'qty': 'Quantity',
            'price': 'Unit Price', 'total': 'Total', 'subtotal': 'Subtotal', 'discount': 'Discount', 'vat': 'VAT',
            'grand': 'GRAND TOTAL', 'advance': 'Less Advance Payment', 'balance': 'BALANCE DUE',
            'bank': 'BANK INSTRUCTIONS', 'remarks': 'REMARKS / NOTES', 'from': 'Issued By:',
            'accepted': 'Accepted By (Sign & Stamp):',
            'legal': 'This document is electronically generated and verified by Aspidus CRM System.'
        },
        'sr': {
            'offer': 'ZVANIČNA PONUDA', 'proforma': 'PROFAKTURA', 'commercial': 'KOMERCIJALNA FAKTURA',
            'inv_no': 'Broj dokumenta', 'date': 'Datum izdavanja', 'due': 'Rok plaćanja', 'po': 'PO / Ugovor',
            'bill_to': 'KUPAC (BILL TO):', 'ship_to': 'PRIMALAC ROBE (SHIP TO):', 'same_as_buyer': 'ISTO KAO KUPAC',
            'tax_id': 'PIB / Poreski broj', 'reg_no': 'Matični broj', 'product': 'Roba / Proizvod', 'hs': 'Tarifni broj (HS)', 'origin': 'Poreklo',
            'incoterm': 'Paritet', 'pol': 'Luka ukrcaja (POL)', 'pod': 'Luka iskrcaja (POD)', 'vessel': 'Brod / Putovanje',
            'bl': 'B/L Broj (Tovarni list)', 'ship_date': 'Datum utovara', 'pack': 'Pakovanje', 'pay_terms': 'Uslovi plaćanja',
            'net': 'Neto težina', 'gross': 'Bruto težina', 'vol': 'Zapremina (CBM)', 'desc': 'Opis stavke / Specifikacija', 'qty': 'Količina',
            'price': 'Jed. Cena', 'total': 'Ukupno', 'subtotal': 'Međuzbir', 'discount': 'Odobren Popust', 'vat': 'PDV',
            'grand': 'ZA NAPLATU', 'advance': 'Uplaćen Avans', 'balance': 'PREOSTALI DUG',
            'bank': 'BANKARSKE INSTRUKCIJE ZA UPLATU', 'remarks': 'NAPOMENE', 'from': 'Izdavalac:',
            'accepted': 'Kupac (Pečat i Potpis):',
            'legal': 'Ovaj dokument je elektronski generisan i verifikovan u Aspidus CRM sistemu.'
        }
    };
    const t = (k) => dict[lang][k] || dict['en'][k] || k;

    const fmtCurr = (val, curr) => {
        if (typeof Utils !== 'undefined' && Utils.formatCurrency) return Utils.formatCurrency(val, curr);
        return Number(val).toLocaleString('en-US', { style: 'currency', currency: curr });
    };

    const docTypeStr = data.type === 'offer' ? t('offer') : (data.type === 'proforma' ? t('proforma') : t('commercial'));
    const custName = data.customer ? (data.customer.companyName || '') : 'Unknown';

    // 2. KREIRANJE VERIFIKACIONOG HASH-A I PROFESIONALNIH METAPODATAKA
    const verHash = 'VER-' + Array.from({length: 12}, () => Math.floor(Math.random()*36).toString(36)).join('').toUpperCase() + '-' + Date.now().toString(36).toUpperCase();

    const compName = (typeof state !== 'undefined' && state.company && state.company.name) ? state.company.name : 'Aspidus Enterprise';

    doc.setProperties({
        title: `${docTypeStr} - ${data.documentNo}`,
        subject: `Commercial Document issued to ${custName}`,
        author: compName,
        creator: 'Aspidus CRM Enterprise PDF Engine v0.369',
        keywords: `trade, export, invoice, ${custName}, ${verHash}`,
        producer: 'Aspidus Global Trading'
    });

    const margin = 15;
    const pageWidth = doc.internal.pageSize.getWidth();
    const pageHeight = doc.internal.pageSize.getHeight();
    const blue = [30, 64, 175];
    const darkGray = [40, 40, 40];

    let currentY = 15;

    // --- ZAGLAVLJE DOKUMENTA ---
    // Ime kompanije levo
    doc.setFont('helvetica', 'bold'); doc.setFontSize(15); doc.setTextColor(blue[0], blue[1], blue[2]);
    doc.text(compName.toUpperCase(), margin, currentY + 8);
    
    // Logo desno
    if(typeof state !== 'undefined' && state.company && state.company.logoDataUrl) {
        try { doc.addImage(state.company.logoDataUrl, undefined, pageWidth - margin - 45, currentY, 45, 15, '', 'FAST'); } catch(e) {}
    }
    
    currentY += 18;
    doc.setLineWidth(0.5); doc.setDrawColor(blue[0], blue[1], blue[2]);
    doc.line(margin, currentY, pageWidth - margin, currentY);
    currentY += 8;

    // Document Type Title & Info
    doc.setFont('helvetica', 'bold'); doc.setFontSize(16); doc.setTextColor(0, 0, 0);
    doc.text(docTypeStr, margin, currentY + 5);

    // Info block on the right
    let infoBody = [];
    infoBody.push([{ content: t('inv_no') + ':', styles: { fontStyle: 'bold' } }, { content: data.documentNo, styles: { fontStyle: 'bold' } }]);
    infoBody.push([{ content: t('date') + ':', styles: { fontStyle: 'bold' } }, new Date(data.date).toLocaleDateString(lang)]);
    if(data.poNumber) infoBody.push([{ content: t('po') + ':', styles: { fontStyle: 'bold', textColor: blue } }, { content: data.poNumber, styles: { fontStyle: 'bold', textColor: blue } }]);
    if(data.validUntil) infoBody.push([{ content: t('due') + ':', styles: { fontStyle: 'bold' } }, new Date(data.validUntil).toLocaleDateString(lang)]);

    doc.autoTable({
        startY: currentY - 2, body: infoBody, theme: 'plain',
        styles: { font: 'helvetica', fontSize: 9, cellPadding: 1, textColor: darkGray },
        columnStyles: { 0: { halign: 'right', cellWidth: 40 }, 1: { halign: 'left', cellWidth: 40 } },
        margin: { left: pageWidth - margin - 80 }
    });
    
    currentY = Math.max(doc.lastAutoTable.finalY + 5, currentY + 15);

    // --- KUPAC I PRIMALAC ROBE ---
    const formatAddress = (partner) => {
        if(!partner) return 'N/A';
        let str = `${partner.companyName}\n`;
        if(partner.address) {
            if(partner.address.street) str += `${partner.address.street}\n`;
            const csz = [partner.address.city, partner.address.zip, partner.address.country].filter(Boolean).join(', ');
            if(csz) str += `${csz}\n`;
        }
        if(partner.taxId) str += `${t('tax_id')}: ${partner.taxId}`;
        return str;
    };

    const buyerStr = formatAddress(data.customer);
    const consStr = (data.consignee && data.consignee.id) ? formatAddress(data.consignee) : t('same_as_buyer');

    doc.autoTable({
        startY: currentY,
        head: [[ t('bill_to'), t('ship_to') ]],
        body: [[ buyerStr, consStr ]], theme: 'plain',
        headStyles: { fillColor: [245,245,245], textColor: [0,0,0], fontStyle: 'bold', fontSize: 9, cellPadding: 2, lineWidth: 0.1, lineColor: [200,200,200] },
        styles: { font: 'helvetica', fontSize: 9, cellPadding: 2, textColor: darkGray },
        columnStyles: { 0: { cellWidth: '50%' }, 1: { cellWidth: '50%' } }, margin: { left: margin, right: margin }
    });
    currentY = doc.lastAutoTable.finalY + 5;

    // --- TRANSPORT I LOGISTIKA ---
    const logData = data.logistics || {};
    doc.autoTable({
        startY: currentY,
        body: [
            [ { content: t('product')+':', styles: { fontStyle: 'bold' } }, data.productName || 'N/A', { content: t('hs')+':', styles: { fontStyle: 'bold' } }, data.hsCode || 'N/A' ],
            [ { content: t('origin')+':', styles: { fontStyle: 'bold' } }, logData.origin || 'N/A', { content: t('incoterm')+':', styles: { fontStyle: 'bold' } }, logData.incoterm || 'N/A' ],
            [ { content: t('pol')+':', styles: { fontStyle: 'bold' } }, logData.pol || 'N/A', { content: t('pod')+':', styles: { fontStyle: 'bold' } }, logData.pod || 'N/A' ],
            [ { content: t('vessel')+':', styles: { fontStyle: 'bold' } }, logData.vessel || 'TBA', { content: t('bl')+':', styles: { fontStyle: 'bold' } }, logData.blNumber || 'TBA' ],
            [ { content: t('ship_date')+':', styles: { fontStyle: 'bold' } }, logData.shipmentDate ? new Date(logData.shipmentDate).toLocaleDateString(lang) : 'TBA', { content: t('pack')+':', styles: { fontStyle: 'bold' } }, logData.packaging || 'N/A' ],
            [ { content: t('pay_terms')+':', styles: { fontStyle: 'bold' } }, { content: logData.paymentTerms || 'TBA', colSpan: 3, styles: { fontStyle: 'bold', textColor: [200,0,0] } } ]
        ],
        theme: 'grid',
        styles: { font: 'helvetica', fontSize: 8, cellPadding: 2, textColor: darkGray, lineColor: [220, 220, 220] },
        columnStyles: { 0: { fillColor: [250, 250, 250], cellWidth: 35 }, 2: { fillColor: [250, 250, 250], cellWidth: 35 } },
        margin: { left: margin, right: margin }
    });
    currentY = doc.lastAutoTable.finalY + 3;

    // --- TEŽINE I ZAPREMINA ---
    if (data.weights && (data.weights.net || data.weights.gross || data.weights.cbm)) {
        doc.autoTable({
            startY: currentY,
            body: [[
                { content: `${t('net')}: ${data.weights.net} ${data.weights.unit}`, styles: { halign: 'center' } },
                { content: `${t('gross')}: ${data.weights.gross} ${data.weights.unit}`, styles: { halign: 'center' } },
                { content: `${t('vol')}: ${data.weights.cbm} CBM`, styles: { halign: 'center' } }
            ]],
            theme: 'grid',
            styles: { font: 'helvetica', fontSize: 8, fontStyle: 'bold', fillColor: [250, 250, 250], textColor: darkGray, lineColor: [220, 220, 220], cellPadding: 2 },
            margin: { left: margin, right: margin }
        });
        currentY = doc.lastAutoTable.finalY + 5;
    }

    // --- SPECIFIKACIJA / COA ---
    if(data.detailedSpec && data.detailedSpec.trim() !== '') {
        doc.autoTable({
            startY: currentY,
            head: [[ t('desc') ]], body: [[ data.detailedSpec ]], theme: 'grid',
            headStyles: { fillColor: [245,245,245], textColor: [0,0,0], fontStyle: 'bold', fontSize: 8, cellPadding: 2 },
            styles: { font: 'helvetica', fontSize: 8, textColor: [60,60,60], cellPadding: 2, lineColor: [220, 220, 220] },
            margin: { left: margin, right: margin }
        });
        currentY = doc.lastAutoTable.finalY + 5;
    }

    // --- GLAVNA TABELA STAVKI ---
    const tableData = data.items.map(item => [ item.desc, `${item.qty} ${item.unit}`, fmtCurr(item.price, data.currency), fmtCurr(item.total, data.currency) ]);
    doc.autoTable({
        startY: currentY,
        head: [[ t('desc'), t('qty'), t('price'), t('total') ]],
        body: tableData, theme: 'grid',
        headStyles: { fillColor: blue, textColor: 255, fontStyle: 'bold', fontSize: 9, cellPadding: 3 },
        styles: { font: 'helvetica', fontSize: 9, cellPadding: 3, textColor: darkGray, lineColor: [220, 220, 220] },
        columnStyles: { 0: { cellWidth: 'auto' }, 1: { cellWidth: 30, halign: 'right' }, 2: { cellWidth: 35, halign: 'right' }, 3: { cellWidth: 40, halign: 'right' } },
        margin: { left: margin, right: margin, bottom: 45 } 
    });
    currentY = doc.lastAutoTable.finalY + 2;

    // --- FINANSIJSKI OBRAČUN ---
    let totalsBody = [];
    totalsBody.push([ t('subtotal') + ':', fmtCurr(data.subtotal, data.currency) ]);
    if(data.discount > 0) totalsBody.push([ { content: t('discount') + ':', styles: { textColor: [200,0,0] } }, { content: `- ${fmtCurr(data.discount, data.currency)}`, styles: { textColor: [200,0,0] } } ]);
    if(data.vat > 0) totalsBody.push([ `${t('vat')} (${data.customVatRate || 5}%):`, fmtCurr(data.vat, data.currency) ]);
    totalsBody.push([ { content: t('grand') + ':', styles: { fontStyle: 'bold', fontSize: 11, textColor: [0,0,0] } }, { content: fmtCurr(data.grandTotal, data.currency), styles: { fontStyle: 'bold', fontSize: 11, textColor: [0,0,0] } } ]);

    if (data.advance > 0) {
        totalsBody.push([ { content: t('advance') + ':', styles: { textColor: [0,128,0] } }, { content: `- ${fmtCurr(data.advance, data.currency)}`, styles: { textColor: [0,128,0] } } ]);
        totalsBody.push([ { content: t('balance') + ':', styles: { fontStyle: 'bold', fontSize: 12, textColor: [200, 0, 0] } }, { content: fmtCurr(data.balance, data.currency), styles: { fontStyle: 'bold', fontSize: 12, textColor: [200, 0, 0] } } ]);
    }

    doc.autoTable({
        startY: currentY, body: totalsBody, theme: 'plain',
        styles: { font: 'helvetica', fontSize: 9, halign: 'right', cellPadding: 1.5, textColor: darkGray },
        columnStyles: { 0: { cellWidth: 60 }, 1: { cellWidth: 40 } },
        margin: { left: pageWidth - margin - 100, right: margin }
    });
    currentY = doc.lastAutoTable.finalY + 10;

    // --- BANKARSKE INSTRUKCIJE ---
    if (data.bankDetails && data.bankDetails.trim() !== '') {
        doc.autoTable({
            startY: currentY,
            head: [[ t('bank') ]], body: [[ data.bankDetails ]], theme: 'grid',
            headStyles: { fillColor: [245, 245, 245], textColor: [0, 0, 0], fontStyle: 'bold', fontSize: 8, cellPadding: 2 },
            styles: { font: 'helvetica', fontSize: 8, fontStyle: 'bold', cellPadding: 3, textColor: darkGray, lineColor: [220,220,220] },
            margin: { left: margin, right: margin }
        });
        currentY = doc.lastAutoTable.finalY + 5;
    }

    // --- NAPOMENE ---
    if (data.notes && data.notes.trim() !== '') {
        doc.autoTable({
            startY: currentY,
            head: [[ t('remarks') ]], body: [[ data.notes ]], theme: 'plain',
            headStyles: { fillColor: [255, 255, 255], textColor: [0, 0, 0], fontStyle: 'bold', fontSize: 8, cellPadding: 1 },
            styles: { font: 'helvetica', fontSize: 8, cellPadding: 1, textColor: darkGray },
            margin: { left: margin, right: margin }
        });
        currentY = doc.lastAutoTable.finalY + 15;
    }

    // --- POTPISI I PEČAT ---
    let signY = currentY;
    if (signY > pageHeight - 50) { doc.addPage(); signY = 25; }

    doc.setDrawColor(0,0,0); doc.setLineWidth(0.3);
    doc.line(margin, signY, margin + 60, signY); 
    doc.line(pageWidth - margin - 60, signY, pageWidth - margin, signY); 
    
    doc.setFontSize(8); doc.setTextColor(0,0,0); doc.setFont('helvetica', 'bold');
    doc.text(`${t('from')}\n${compName}`, margin, signY + 4);
    doc.text(t('accepted'), pageWidth - margin, signY + 4, { align: 'right' });

    if(typeof state !== 'undefined' && state.company && state.company.stampDataUrl) { 
        try { doc.addImage(state.company.stampDataUrl, 'PNG', margin + 10, signY - 25, 40, 25, '', 'FAST'); } catch(e) {} 
    }

    // --- FOOTER SA VERIFIKACIJOM (Na svakoj stranici) ---
    const pages = doc.internal.getNumberOfPages();
    for (let i = 1; i <= pages; i++) {
        doc.setPage(i);
        
        doc.setDrawColor(blue[0], blue[1], blue[2]); doc.setLineWidth(0.5);
        doc.line(margin, pageHeight - 20, pageWidth - margin, pageHeight - 20);
        
        doc.setFont('helvetica', 'bold'); doc.setFontSize(6); doc.setTextColor(blue[0], blue[1], blue[2]);
        doc.text(`VERIFICATION HASH: ${verHash}`, margin, pageHeight - 16);
        
        doc.setFont('helvetica', 'italic'); doc.setTextColor(100, 100, 100);
        doc.text(t('legal'), margin, pageHeight - 12);

        doc.setFont('helvetica', 'bold'); doc.setTextColor(0,0,0);
        const compAddress = (typeof state !== 'undefined' && state.company && state.company.address) ? state.company.address.replace(/\n/g, ', ') : '';
        doc.text(compAddress, margin, pageHeight - 8);
        
        doc.setFont('helvetica', 'normal'); doc.setTextColor(120,120,120);
        doc.text(`Page ${i} of ${pages}`, pageWidth - margin, pageHeight - 8, { align: 'right' });
    }

    // --- LOGIKA ZA EMAIL ILI DOWNLOAD ---
    if (action === 'send') {
        const base64Str = doc.output('datauristring');
        if (typeof Comms !== 'undefined') {
            Comms.showSendModal(base64Str, filename, data);
        } else {
            alert('Comms modul nije učitan!');
        }
    } else {
        doc.save(filename);
        if(typeof logClientEvent === 'function') logClientEvent('DOWNLOAD', data.type === 'offer' ? 'offers' : 'deals', `Downloaded PDF: ${filename} (Hash: ${verHash})`);
    }
}