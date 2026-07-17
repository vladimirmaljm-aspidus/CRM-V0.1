const PRODUCT_CATEGORIES = [ 'agriculture', 'food', 'beverages', 'inputs', 'industry', 'construction', 'energy', 'metals', 'chemicals', 'textiles', 'electronics', 'pharma', 'packaging', 'other' ];

const UNITS = ['kg - Kilogram', 'MT - Metric Ton', 'g - Gram', 'lb - Pound', 'oz - Ounce', 'pcs - Pieces', 'L - Liter', 'mL - Milliliter', 'CBM - Cubic Meter', 'sqm - Square Meter', 'm - Meter', 'plt - Pallet', 'ctr - Container', 'FCL - Full Container Load', 'box - Box', 'ctn - Carton', 'bag - Bag', 'bbl - Barrel', 'gal - Gallon'];

// ==========================================================
//  UNIT NORMALIZATION SYSTEM
// ==========================================================
// Bilo koji tekst iz UNITS liste ili "MT", "mt", "tona", "Metric Ton" itd.
// mapira se u kanonski oblik (kg / t / L / mL / pcs / box / ...).
// Sve konverzije mase se rade kroz KILOGRAM kao pivot.
const UNIT_CANONICAL = {
    // Masa
    'kg': { canonical: 'kg', kind: 'mass', toKg: 1 },
    'g':  { canonical: 'g',  kind: 'mass', toKg: 0.001 },
    'mg': { canonical: 'mg', kind: 'mass', toKg: 0.000001 },
    't':  { canonical: 't',  kind: 'mass', toKg: 1000 },
    'mt': { canonical: 't',  kind: 'mass', toKg: 1000 },
    'ton':{ canonical: 't',  kind: 'mass', toKg: 1000 },
    'tona':{canonical: 't',  kind: 'mass', toKg: 1000 },
    'tonna':{canonical: 't', kind: 'mass', toKg: 1000 },
    'metric ton':{ canonical: 't', kind: 'mass', toKg: 1000 },
    'lb': { canonical: 'lb', kind: 'mass', toKg: 0.45359237 },
    'lbs':{ canonical: 'lb', kind: 'mass', toKg: 0.45359237 },
    'oz': { canonical: 'oz', kind: 'mass', toKg: 0.0283495 },
    // Zapremina
    'l':  { canonical: 'L',  kind: 'volume', toL: 1 },
    'ml': { canonical: 'mL', kind: 'volume', toL: 0.001 },
    'cbm':{ canonical: 'CBM',kind: 'volume', toL: 1000 },
    'm3': { canonical: 'CBM',kind: 'volume', toL: 1000 },
    'gal':{ canonical: 'gal',kind: 'volume', toL: 3.78541 },
    'bbl':{ canonical: 'bbl',kind: 'volume', toL: 158.987 },
    // Diskretni
    'pcs':{ canonical: 'pcs',kind: 'count' },
    'pc': { canonical: 'pcs',kind: 'count' },
    'kom':{ canonical: 'pcs',kind: 'count' },
    'box':{ canonical: 'box',kind: 'count' },
    'ctn':{ canonical: 'ctn',kind: 'count' },
    'bag':{ canonical: 'bag',kind: 'count' },
    'plt':{ canonical: 'plt',kind: 'count' },
    'plt.':{canonical: 'plt',kind: 'count' },
    'pallet':{canonical:'plt',kind:'count' },
    'paleta':{canonical:'plt',kind:'count' },
    'ctr':{ canonical: 'ctr',kind: 'count' },
    'fcl':{ canonical: 'fcl',kind: 'count' },
    // Površina / dužina
    'sqm':{ canonical: 'sqm',kind: 'area', toSqm: 1 },
    'm':  { canonical: 'm',  kind: 'length', toM: 1 },
};

function _normalizeUnitKey(raw) {
    if (!raw && raw !== 0) return '';
    // "MT - Metric Ton" → "mt"; "40 kg" → "kg"; " Kilogram" → "kilogram"
    const s = String(raw).trim().toLowerCase();
    // Uzmi prvi token pre "-" ili razmaka koji nije broj
    const beforeDash = s.split(/[-–—]/)[0].trim();
    if (UNIT_CANONICAL[beforeDash]) return beforeDash;
    // Numeric prefix ("40 kg") → skini brojeve
    const stripped = beforeDash.replace(/^[\d.\s]+/, '').trim();
    if (UNIT_CANONICAL[stripped]) return stripped;
    // Whole string as-is
    if (UNIT_CANONICAL[s]) return s;
    return beforeDash || s;
}

if (typeof window !== 'undefined') {
    window.UNIT_CANONICAL = UNIT_CANONICAL;
    window._normalizeUnitKey = _normalizeUnitKey;
}

const CURRENCIES = ['USD', 'EUR', 'AED', 'RSD', 'GBP', 'CHF', 'BAM', 'TRY', 'CNY'];

const INCOTERMS = [
  'EXW - Ex Works', 'FCA - Free Carrier', 'CPT - Carriage Paid To', 'CIP - Carriage and Insurance Paid To',
  'DAP - Delivered at Place', 'DPU - Delivered at Place Unloaded', 'DDP - Delivered Duty Paid',
  'FAS - Free Alongside Ship', 'FOB - Free On Board', 'CFR - Cost and Freight', 'CIF - Cost, Insurance and Freight',
  'DAT - Delivered at Terminal', 'DDU - Delivered Duty Unpaid'
];

const CERTIFICATES = ['HALAL', 'HACCP', 'ISO 9001', 'ISO 14001', 'ISO 22000', 'ISO 45001', 'FSSC 22000', 'BRC', 'IFS', 'UTZ', 'Rainforest Alliance', 'RSPO', 'FDA', 'CE', 'Kosher', 'Organic', 'Fairtrade', 'GlobalGAP', 'FSC', 'GMP', 'Vegan', 'SGS Inspected', 'Bez Sertifikata'];

const GLOBAL_RATES = { 'USD': 1.0, 'EUR': 0.92, 'AED': 3.67, 'RSD': 108.5, 'GBP': 0.79, 'CHF': 0.90, 'BAM': 1.80, 'TRY': 32.0, 'CNY': 7.23 };

const COUNTRIES = ["Afghanistan","Albania","Algeria","Argentina","Armenia","Australia","Austria","Bahrain","Bangladesh","Belarus","Belgium","Bosnia and Herzegovina","Brazil","Bulgaria","Canada","Chile","China","Colombia","Croatia","Cyprus","Czechia","Denmark","Egypt","Ethiopia","Finland","France","Georgia","Germany","Ghana","Greece","Hungary","India","Indonesia","Iran","Iraq","Ireland","Israel","Italy","Japan","Jordan","Kazakhstan","Kenya","Kuwait","Lebanon","Libya","Malaysia","Mexico","Montenegro","Morocco","Netherlands","New Zealand","Nigeria","North Macedonia","Norway","Oman","Pakistan","Peru","Philippines","Poland","Portugal","Qatar","Romania","Russia","Saudi Arabia","Senegal","Serbia","Singapore","Slovakia","Slovenia","South Africa","South Korea","Spain","Sri Lanka","Sweden","Switzerland","Syria","Taiwan","Tanzania","Thailand","Tunisia","Turkey","Uganda","Ukraine","United Arab Emirates","United Kingdom","United States of America","Vietnam"];

const CITIES_BY_COUNTRY = {
    "United Arab Emirates": ["Dubai", "Abu Dhabi", "Sharjah", "Al Ain", "Ajman", "Ras Al Khaimah", "Fujairah"],
    "Serbia": ["Beograd", "Novi Sad", "Niš", "Kragujevac", "Subotica", "Čačak", "Zrenjanin"],
    "Bosnia and Herzegovina": ["Sarajevo", "Banja Luka", "Tuzla", "Zenica", "Mostar", "Bihać", "Brčko"],
    "Montenegro": ["Podgorica", "Nikšić", "Herceg Novi", "Pljevlja", "Bar", "Bijelo Polje", "Kotor"],
    "Croatia": ["Zagreb", "Split", "Rijeka", "Osijek", "Zadar", "Pula", "Slavonski Brod"],
    "North Macedonia": ["Skopje", "Bitola", "Kumanovo", "Prilep", "Tetovo", "Ohrid", "Veles"],
    "Slovenia": ["Ljubljana", "Maribor", "Kranj", "Celje", "Koper", "Velenje", "Novo Mesto"],
    "China": ["Shanghai", "Beijing", "Guangzhou", "Shenzhen", "Chengdu", "Chongqing", "Tianjin"],
    "United States of America": ["New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia", "San Antonio"],
    "India": ["Mumbai", "Delhi", "Bangalore", "Hyderabad", "Ahmedabad", "Chennai", "Kolkata"],
    "Brazil": ["São Paulo", "Rio de Janeiro", "Brasília", "Salvador", "Fortaleza", "Belo Horizonte", "Manaus"],
    "Germany": ["Berlin", "Hamburg", "Munich", "Cologne", "Frankfurt", "Stuttgart", "Düsseldorf"],
    "Russia": ["Moscow", "Saint Petersburg", "Novosibirsk", "Yekaterinburg", "Kazan", "Nizhny Novgorod", "Chelyabinsk"],
    "Turkey": ["Istanbul", "Ankara", "Izmir", "Bursa", "Antalya", "Adana", "Konya"],
    "Italy": ["Rome", "Milan", "Naples", "Turin", "Palermo", "Genoa", "Bologna"],
    "France": ["Paris", "Marseille", "Lyon", "Toulouse", "Nice", "Nantes", "Montpellier"],
    "United Kingdom": ["London", "Birmingham", "Manchester", "Glasgow", "Liverpool", "Bristol", "Sheffield"],
    "Ghana": ["Accra", "Kumasi", "Tamale", "Sekondi-Takoradi", "Ashaiman", "Sunyani", "Cape Coast"],
    "Argentina": ["Buenos Aires", "Córdoba", "Rosario", "Mendoza", "Tucumán", "La Plata", "Mar del Plata"],
    "Armenia": ["Yerevan", "Gyumri", "Vanadzor", "Vagharshapat", "Abovyan", "Kapan", "Hrazdan"]
};

const ALL_CITIES_FLAT = Array.from(new Set(Object.values(CITIES_BY_COUNTRY).flat()));

let FILE_LIMIT_MB = 50;

const state = {
  lang: 'sr', currentView: 'deals', detailViewId: null,
  data: { partners: [], products: [], deals: [], demands: [], accounts: [], transactions: [], recurringExpenses: [], connections: [], offers: [] },
  settings: { commissionRate: 0.05, lastInvoiceNumber: 0, lang: 'sr', fileLimitMB: 50, currency: 'USD', lastOfferNumber: 0, vatRate: 5, paymentWarningDays: 7, defaultInvoiceNotes: '', defaultOfferNotes: '' },
  company: { name:'', address:'', taxId:'', regNumber:'', bankName:'', accountNumber:'', swift:'', logoDataUrl:'', stampDataUrl:'' },
  activeFilters: {}, editingItem: null, notifications: []
};

const DATA_KEYS = ['partners', 'products', 'deals', 'demands', 'accounts', 'transactions', 'recurringExpenses', 'connections', 'offers', 'settings', 'company'];
// Eksponuj top-level state/data ključeve kao window.* da bi test tooling (Playwright)
// mogao da im pristupi kroz page.evaluate. U regular browser skriptama, `const` na
// top-level nije globalna promenljiva pa `window.state` nije auto-postavljen.
if (typeof window !== 'undefined') {
    window.state = state;
    window.DATA_KEYS = DATA_KEYS;
    window.FILE_LIMIT_MB = FILE_LIMIT_MB;
    window.ALL_CITIES_FLAT = ALL_CITIES_FLAT;
    window.CITIES_BY_COUNTRY = CITIES_BY_COUNTRY;
}
