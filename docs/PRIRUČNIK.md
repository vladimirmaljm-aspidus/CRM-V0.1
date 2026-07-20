# Aspidus CRM + B2B Portal — Kompletan Priručnik (v22)

**Poslednji update:** 20. jul 2026
**Verzija aplikacije:** 22

Ovaj dokument je _jedinstveni izvor istine_ za svaki modul u sistemu.
Namenjen je vlasniku sistema (netehnički korisnik) — objašnjava ŠTA modul
radi, ODAKLE mu dolaze podaci, KAKO se konfiguriše i U KOJIM SLUČAJEVIMA
ga koristite.

---

## Sadržaj

1. [Kritični operativni saveti pre puštanja u rad](#0-kritični-saveti)
2. [Podešavanja preko Settings modula](#1-settings-modul)
3. [OTP Delivery (Resend/SendGrid/Postmark)](#2-otp-delivery)
4. [Chat notifikacije (Slack/Teams/Telegram/ntfy/WhatsApp)](#3-chat-notifikacije)
5. [Sigurnost — hCaptcha, HIBP, TOTP 2FA](#4-sigurnost)
6. [Živi tržišni podaci — kursevi + robni indeksi](#5-tržišni-podaci)
7. [Praćenje pošiljki — 17TRACK, MarineTraffic, FlightAware](#6-praćenje-pošiljki)
8. [Registri firmi — Companies House UK i drugi](#7-registri-firmi)
9. [KYC + sankcije — OpenSanctions + Open Ownership PEP](#8-kyc-i-sankcije)
10. [VIES VAT + IBAN + BIC validacija](#9-vies-iban-bic)
11. [HS kodovi + CAS brojevi + REST Countries](#10-hs-cas-countries)
12. [Logistika — pomorske rute, kanali, luke, vremenska prognoza](#11-logistika)
13. [PDF sistem + SEPA QR + Verify QR](#12-pdf-sistem)
14. [Dashboard sa KPI grafikonima](#13-dashboard)
15. [Cmd+K globalna pretraga](#14-cmdk)
16. [Baza podataka — anti-lock hardening](#15-baza-podataka)
17. [Migracija baze van PythonAnywhere-a](#16-migracija-baze)
18. [Deploy checklist za sutra](#17-deploy-checklist)

---

## 0. Kritični operativni saveti

### Šta MORA biti podešeno pre nego što pustite 20 ljudi u aplikaciju

1. **OTP Delivery preko Resend-a** (ne SMTP na PythonAnywhere)
   - PythonAnywhere free/starter tier blokira odlazni SMTP → OTP mejlovi
     nikad ne stižu ili idu u spam.
   - Rešenje: podesite Resend (besplatno do 3000 mejlova/mesec).
   - Uputstvo: sekcija 2 ispod.

2. **Backup baze** — ovo je već automatizovano, ali proverite
   - Backup thread radi svakih 6h.
   - Enkriptovan Fernet, snima se u `data/backups/*.fernet`.
   - **Preuzmite jedan backup lokalno pre deploy-a** kao osiguranje.

3. **hCaptcha na portalu** (samo ako imate javno pristupačan URL)
   - Bez ovoga, boti mogu masovno pokretati OTP zahteve.
   - Uputstvo: sekcija 4.

4. **Sat serveru** — proverite da su NTP synchronizacija radi na PythonAnywhere.
   TOTP 2FA i magic-link zavise od tačnog vremena. Ako je sat pomeren
   >30 sekundi, TOTP kodovi neće raditi.

### Šta apsolutno ne smete raditi

- **Ne mešajte se u** `.venv/` **direktorijum** i ne brišite ga bez znanja.
- **Ne** menjajte `.env` u produkciji bez restart-a servera.
- **Ne** brišite `data/` direktorijum — tu žive baze, uploads, backups.

---

## 1. Settings modul

**Gde se nalazi:** klik na ⚙️ ikonicu u gornjem desnom uglu → **SYSTEM CONTROL CENTER**.

**Šta ima:**

| Tab | Sadržaj |
|---|---|
| 🏢 Company Data | Naziv firme, adresa, PIB, MB, logo, pečat, bankovni računi, brand boja |
| ⚙️ System Config | Jezik, valuta, VAT %, upload limit, brojači ponuda/faktura |
| 📡 SMTP & Comms | SMTP kredencijali, email šabloni (subject/body), WhatsApp šablon |
| 🛡️ Security & Firewall | IP whitelist, IP blacklist, rate limits |
| 💾 Data Management | Export/import baze (JSON), CSV import partnera |
| **🔌 Integracije (API-ji)** | **NOVO — sve v19/20/21/22 spoljne servise** |
| 🛠️ Diagnostics & Tools | Integrity scan, financial check, storage status, audit export |

Svi ključevi u tabu **🔌 Integracije** se **enkriptuju Fernet AES-128** pre
snimanja u bazu — API ključevi nikad ne izlaze u plaintext-u.

Kad su jednom snimljeni, prikazuju se u UI-u **maskirani** (npr. `re_a1…xy23`).
Kad želite da rotirate ključ, samo unesite novi u isto polje — prazno polje
znači "sačuvaj postojeći".

---

## 2. OTP Delivery

### Problem koji rešava

OTP kodovi u B2B portalu se šalju klijentima e-mailom. Ako se šalju preko
SMTP-a sa PythonAnywhere hostinga, javljaju se dva problema:
- Free/starter tier PythonAnywhere blokira odlazni SMTP na najveći broj
  servera → OTP nikad ne stižu.
- Kad prođu, često završe u spam-u jer PythonAnywhere IP nema DMARC/SPF
  reputaciju.

### Rešenje: transakcioni email provideri

Podržavamo 4 providera:
- **Resend** (`api.resend.com`) — preporuka. Besplatno **3000 mejlova/mesec**.
  Ključ počinje sa `re_`. Otvorite [resend.com](https://resend.com), 
  registrujte se, dodajte svoj domen, kopirajte API ključ.
- **SendGrid** — besplatno 100/dan. Ključ počinje sa `SG.`.
- **Postmark** — nema free tier ali odličan deliverability.
- **SMTP** (fallback) — koristite samo ako niste na deljenom hostingu.

### Kako se koristi

1. Settings → 🔌 Integracije → **📧 Dostava OTP kodova**
2. Provider = `Resend`
3. API Key = paste ključa (bez `Bearer ` prefiksa)
4. From name = `Aspidus` (ili šta god vam odgovara)
5. From email = `no-reply@vasdomain.com` (mora biti verifikovan u Resend-u)
6. Uključite **Magic Link** — dodaje jedan-klik URL u OTP mejl
   pored šifre. Klijent može da klikne umesto da tipka 6 cifara.
7. TTL = 15 (minuta — magic link istekne posle toga)
8. Kliknite **Snimi OTP config**
9. U polju za test unesite svoj mejl → kliknite **Test mejl** →
   proverite inbox (i spam).

### Odakle podaci dolaze

- OTP kodovi se generišu lokalno (nema poziva prema Resend-u za generisanje).
- Resend samo prosleđuje SMTP-like poruku klijentu.
- Magic link URL se potpisuje HMAC-SHA256 sa vašim tajnim ključem — spolja
  nemoguće da se lažira.

### Bezbednosne garancije

- Isti OTP se **nikad ne šalje dva puta** — čim je izgenerisan, snima se
  status `sent` u bazu; sledeći request pravi novi OTP.
- Circuit breaker: ako provider padne 3 puta u 5 min, sistem privremeno
  blokira mail slanje da ne bi ušao u loop (kao onaj koji se desio
  juče sa 50 mejlova u 10 min).

---

## 3. Chat notifikacije

### Problem koji rešava

Kad klijent prihvati ponudu ili pošalje KYC, admin obično sazna preko
mejla. Chat notifikacije šalju odmah poruku u Slack/Teams/Telegram/ntfy
kanal da tim reaguje u realnom vremenu.

### Podržani kanali

| Kanal | Kada da koristite | Šta vam treba |
|---|---|---|
| **Slack** | Ako firma već koristi Slack | Incoming Webhook URL — `Apps → Incoming Webhooks → Add to Slack` |
| **MS Teams** | Ako je firma Microsoft-shop | Connector URL — `Channel → Connectors → Incoming Webhook` |
| **Telegram** | Za lične notifikacije na telefon | Bot Token (`@BotFather`) + Chat ID |
| **ntfy.sh** | Najjednostavniji push notifikacije | URL topic-a, npr. `https://ntfy.sh/aspidus-alerts` |
| **WhatsApp** | Za VIP klijent-menadžere | WhatsApp Cloud API Token + Phone ID + To (msisdn) |

### Kako se koristi

1. Settings → 🔌 Integracije → **💬 Chat Notifikacije**
2. Popunite bilo koje polje (svako je opciono — možete koristiti samo Slack)
3. Kliknite **Snimi webhooks**
4. Kliknite **Test obaveštenje** → svi konfigurisani kanali dobijaju
   test poruku istog trenutka

### Kada se okidaju automatski

- Klijent prihvatio ponudu → poruka u sve konfigurisane kanale
- Klijent odbio ponudu → poruka
- Klijent podneo KYC formu → poruka
- Sanctions flag na KYC → **crveni alert** (visok prioritet)
- Kreiran novi dil → poruka
- Dokument potpisan → poruka

### Bezbednost

- Bot tokeni se **enkriptuju Fernet-om** pre snimanja.
- Slack/Teams URL-ovi nisu enkriptovani jer ne otkrivaju workspace secret.
- Svaki dispatch prolazi kroz 3s timeout — ne blokira glavnu app rutu.

---

## 4. Sigurnost — hCaptcha, HIBP, TOTP

### 4.1 hCaptcha (portal anti-bot)

**Problem:** neko iz spolja može masovno da bombarduje portal OTP zahtevima.

**Rešenje:** hCaptcha na svakoj portal formi za slanje OTP-a.

**Setup:**
1. Otvorite [hcaptcha.com](https://hcaptcha.com), registrujte se
2. Add Site → unesite domen portala (npr. `portal.aspidus.io`)
3. Kopirajte **Site key** (public) i **Secret key** (private)
4. Settings → 🔌 Integracije → **🛡️ hCaptcha** → paste + Save

Ako oba polja ostanu prazna, provera je isključena. Ako je uključena a
hCaptcha padne, portal fail-open (dozvoli zahtev) da ne blokira klijente.

### 4.2 HIBP — Have I Been Pwned (password check)

**Problem:** admin postavi password `Aspidus123` — koji je već procureo u
zbirci od 10 milijardi.

**Rešenje:** kada admin menja password, sistem šalje **SHA-1 prefiks**
(prvih 5 karaktera) na HIBP API. HIBP vraća listu punih hash-eva sa tim
prefiksom, sistem lokalno proverava da li je full hash unutra. Password
sam se **nikad ne šalje** — ovo je k-anonymity model.

Ako je password poznat (>= 1 pojavljivanje u bazi provala), sistem baca
grešku i tera admin-a da izabere drugi.

Ovo je **automatski uključeno** — nema šta da se podešava.

### 4.3 TOTP 2FA za CRM admin

**Problem:** ako admin password procuri, cela firma je otvorena.

**Rešenje:** RFC 6238 TOTP kodovi (Google Authenticator kompatibilan).

**Setup:**
1. Login u CRM
2. Users → moj profil → **Enable 2FA**
3. Skenirajte QR kod telefonom (Google Authenticator, Authy, 1Password)
4. Unesite 6-cifarni kod da potvrdite
5. **SAČUVAJTE recovery kodove** — one služe kao backup ako izgubite telefon

Od tada, svaki login traži password + 6-cifarni kod.

### 4.4 Rate limits (firewall)

Portal endpoint-i su rate-limited:
- Login: max 10 pokušaja / 5 min po IP-u
- Portal OTP zahtev: max 50 zahteva / minutu po IP-u

Podešavanja: Settings → 🛡️ Sigurnost i Firewall.

---

## 5. Živi tržišni podaci

### 5.1 Kursevi (FX)

**Izvor:** [exchangerate.host](https://exchangerate.host) — proxy za ECB
reference rates. **Ne treba API ključ.** Keš 4h.

**Gde vidite:** Dashboard → 💱 Live kursevi.

**API endpoint (za razvoj):**
- `GET /api/geo/fx/rates?base=USD` → sve valute prema USD
- `GET /api/geo/fx/convert?from=USD&to=EUR&amount=1000` → konverzija

### 5.2 Spot cene robe (commodities)

**Izvor:** [Alpha Vantage](https://alphavantage.co). **Treba besplatan API
ključ** — 25 poziva/dan, dovoljno za jedan dashboard refresh svakih sat vremena.

**Setup:**
1. [alphavantage.co/support/#api-key](https://www.alphavantage.co/support/#api-key)
2. Copy Free API Key
3. Settings → 🔌 Integracije → **💹 Tržišni podaci** → paste + Save

**Podržani indeksi:**
- WTI Crude, Brent Crude, Natural Gas
- Copper, Aluminum
- Wheat, Corn, Cotton, Sugar, Coffee

**Gde vidite:** Dashboard → 📊 Robni indeksi.

---

## 6. Praćenje pošiljki

### 6.1 17TRACK — kontejneri + parcels

**Izvor:** [17track.net](https://17track.net) API. **Treba API ključ** —
besplatno **1000 tracking-a/mesec**.

**Kako se koristi:**
- Iz Deals detail view → sekcija "Shipment tracking" → unesite broj kontejnera
  (npr. `MSCU1234567`) ili airway bill (npr. FedEx `123456789012`).
- Sistem će prikazati events (Loaded → Departed → In Transit → Delivered).
- Osvežava se svakih 4h automatski.

### 6.2 MarineTraffic — pozicije brodova

**Izvor:** [MarineTraffic PS7 API](https://www.marinetraffic.com/en/ais-api-services).
**Treba API ključ.**

**Šta radi:** ako znate IMO ili MMSI broda na kome je vaš teret, možete
videti njegovu real-time GPS poziciju + brzinu + kurs + ETA do sledeće luke.

### 6.3 FlightAware — cargo letovi

**Izvor:** [FlightAware AeroAPI](https://flightaware.com/commercial/aeroapi/).
**Treba API ključ** (postoji free tier).

**Šta radi:** za cargo airway bill sa flight number-om (npr. `EK5023`),
prikazuje status leta i lokaciju.

**Setup za sve tri:** Settings → 🔌 Integracije → **📦 Praćenje pošiljki** → paste ključeve.

---

## 7. Registri firmi

### Companies House UK

**Izvor:** [Companies House Public API](https://developer.company-information.service.gov.uk/).
**Besplatno,** treba samo API ključ (jednostavan self-service registration).

**Šta radi:** za UK firmu unesete Company Number (npr. `12345678`) i
sistem izvuče:
- Registrovano ime i skraćenice
- Adresu registrovane kancelarije
- Datum osnivanja
- SIC kodove (delatnost)
- Direktore i kontrolne osobe (PSC — Persons with Significant Control)

**Gde:** Partner form → **Lookup Registry** dugme.

**Setup:** Settings → 🔌 Integracije → **📦 Praćenje pošiljki** →
Companies House UK ključ.

### KBO Belgija, Handelsregister Nemačka, Sirene Francuska

Ovi registri su podržani u backend-u (`routes/geo.py`) ali ne zahtevaju
API ključ za nivo koji koristimo (Sirene = free, ostali = OpenCorporates fallback).

---

## 8. KYC i sankcije

### 8.1 OpenSanctions — sanction lists

**Izvor:** [opensanctions.org](https://opensanctions.org). **Besplatan API,**
poziv na `/search/default` endpoint.

**Kada se okida:** kad klijent submit-uje KYC formu, sistem automatski
pretražuje ime + državu protiv OpenSanctions konsolidovane liste
(OFAC, EU, UK Treasury, UN Security Council, INTERPOL i još 400 lista).

**Šta se dešava kad ima match:**
- KYC status = `pending_sanctions_review`
- Admin je notifikovan (chat webhook + in-app)
- **Admin mora eksplicitno da klikne "Acknowledge sanctions match"** pre
  nego što se KYC odobri — HARD-GATE, ne može da se preskoči

### 8.2 Open Ownership — PEP registry

**Izvor:** [openownership.org](https://openownership.org) — Politically
Exposed Persons.

**Kada:** takođe na KYC submit, dopunska pretraga.

**Šta radi:** flag-uje političke izložene osobe (ministri, ambasadori,
direktori državnih firmi) — trebate im poklanjati dodatnu pažnju
(enhanced due diligence).

**Nema šta da se podešava** — API je besplatan i nema ključa.

---

## 9. VIES VAT + IBAN + BIC

### 9.1 VIES — EU VAT validacija

**Izvor:** [ec.europa.eu/taxation_customs/vies](https://ec.europa.eu/taxation_customs/vies).
**Besplatan SOAP servis,** bez ključa.

**Kada:** kad admin popuni EU partner formu sa VAT ID-jem (npr. `DE123456789`),
sistem automatski poziva VIES i **blokira save** ako VAT ID nije validan.

**Zašto HARD-BLOCK:** invalidan EU VAT znači da je klijent ili neispravno
uneo podatke ili je firma ugašena — u oba slučaja ne želite je u sistemu.

### 9.2 IBAN validacija

**Algoritam:** ISO 13616 mod-97 checksum + country-specific length.

**Podržano 76 zemalja** (svaka EU + većina relevantnih van EU).

**Gde radi:**
- Company form → bank accounts
- Partner form → bank details
- Portal KYC form → HARD-BLOCK ako je IBAN pogrešan

### 9.3 BIC/SWIFT validacija

**Algoritam:** ISO 9362 struktura (8 ili 11 karaktera, alfanumerički
bank code + country code + location + optional branch).

**Cross-check sa IBAN-om:** country code u BIC-u mora se poklopiti sa
country code u IBAN-u — ako se ne poklapa, blok.

---

## 10. HS kodovi, CAS brojevi, REST Countries

### 10.1 HS Codes bundle

**Šta je:** 250+ najčešćih HS heading-a (Harmonized System) + puno ime
za svaku šifru.

**Gde:** Products form → HS Code polje → autocomplete kada tipkate.

**Ponašanje:** format check (2-10 cifara) je HARD-BLOCK, ali ako je
kod nepoznat u lokalnom bundle-u, sistem **snimi sa warning-om** umesto
da odbije — WCO ima 5400+ heading-a i lokalni bundle nikad ne može biti
kompletan.

### 10.2 CAS brojevi (PubChem)

**Izvor:** [PubChem PUG-REST](https://pubchem.ncbi.nlm.nih.gov/pug-rest.html).
**Besplatan,** bez ključa.

**Kada:** Products form → CAS number polje → onBlur poziva PubChem.

**Šta vraća:** hemijsko ime, IUPAC ime, formula, molekulska težina.

**Ponašanje:** format check (`XXXXX-XX-X`) je HARD-BLOCK, PubChem miss
je warning (ne blokira save).

### 10.3 REST Countries

**Izvor:** [restcountries.com](https://restcountries.com). Besplatno.

**Šta radi:** kad izaberete zemlju u partner formi, sistem
auto-popunjava:
- Dial code (npr. +49 za Nemačku)
- Valutu (npr. EUR)
- Region (npr. Europe)
- Zastavu (emoji)

---

## 11. Logistika

### 11.1 Pomorske rute

**Izvor:** interno — waypoints kalkulisani po veruji da rute idu preko
okeanskih koridora, ne preko kopna.

**Uključeno:**
- Kanali: Suez ($400k+ tolls za VLCC), Panama ($270k+ prosek),
  Kiel, Danish straits
- Tranzitna vremena u satima
- Detaljno raščlanjenje port cost-a: THC (Terminal Handling),
  documentation fees, port dues, pilot, tugs, storage

### 11.2 Cestovni + vazdušni transport

- Cestovni: breakdown po tipu prikolice (tautliner, reefer, tanker,
  container carrier) + tipu kamiona.
- Vazdušni: breakdown po tipu aviona (B747F, B777F, MD-11F, B737-800BCF)
  + cost per kg dead weight.

### 11.3 Vremenska prognoza

**Izvor:** [open-meteo.com](https://open-meteo.com). Besplatno, bez ključa.

**Gde:** logistics planner overlay → za svaki waypoint prikaže se
temperatura + vetar + padavine za dan ETA-e.

---

## 12. PDF sistem

### Server-side PDF (ReportLab)

- Sve ponude i fakture generišu se **na serveru** (Python ReportLab).
- Client-side PDF (jsPDF) je uklonjen — nema više razlike između preview-a
  i finalnog PDF-a.

### SEPA EPC069-12 QR

Na svakoj ponudi i fakturi štampa se **SEPA QR kod** koji klijent može
skenirati bankarskom aplikacijom da inicira SEPA plaćanje bez tipkanja.

Standard: **EPC069-12 v2.1**. Sadrži IBAN, BIC, iznos, valutu, remit info,
partner name — sve u jednom QR kodu.

### Verify QR

Pored SEPA QR-a, na svakom dokumentu je i **Verify QR** koji vodi na
javnu URL formatu `https://vas-host.com/verify/<hash>`. Bilo ko sa
telefonom može da skenira i vidi da li je dokument autentičan
(ili je falsifikat).

Hash je deterministički izračunat od `doc_type + doc_no + issue_date +
partner_id + total_amount`. Ako klijent ili neko treće lice pokuša da
promeni bilo koji od tih polja, hash se ne poklapa i verify page kaže "INVALID".

**Kritično:** URL se formira dinamički iz `flask.request.host_url`, tako da
radi bez obzira gde je aplikacija hostovana (localhost dev, PythonAnywhere,
Digital Ocean, Render, itd.).

---

## 13. Dashboard

**Gde:** klik na "Dashboard" u glavnom navu (prva stavka).

**Šta prikazuje:**

- **KPI kartice** (gore): ukupna vrednost pipeline-a, broj aktivnih
  dilova, broj partnera, broj ponuda čekaju odgovor
- **Mesečni prihod** (12M) — line chart iz podataka o dilovima
- **Top 5 partnera** — horizontal bar chart
- **Pipeline po statusu** — donut chart
- **Broj dilova po mesecu** — bar chart
- **💱 Live kursevi** — ECB reference rates
- **📊 Robni indeksi** — Alpha Vantage spot cene (samo ako ste konfigurisali)

**Odakle podaci:** lokalna baza (deals, partners, offers) + spoljni API-ji
(exchangerate.host + Alpha Vantage).

**Chart.js** se učitava sa CDN-a — ne bundlujemo ga da ne bi opterećivao
svaki page load.

---

## 14. Cmd+K globalna pretraga

**Prečica:** `Cmd+K` (Mac) ili `Ctrl+K` (Windows/Linux).

**Šta radi:** otvara palette gde tipkate → pretražuje partnere, proizvode,
dilove, ponude, fajlove u dokumentima. Enter = otvori.

**Zašto:** kad imate 500 partnera i 2000 proizvoda, klikanje kroz menije
je gubljenje vremena.

---

## 15. Baza podataka — anti-lock hardening

### Problem koji smo imali (v22 batch 2)

Jedan admin je otvarao product form, drugi je pravio dil, a u pozadini je
backup thread pokušavao snapshot — sve tri operacije su pokušavale write
istovremeno → `sqlite3.OperationalError: database is locked` → save ne
prošao → klijent vidi "greška, pokušaj ponovo".

### Rešenje (v22 P0 hotfix)

Novi centralni modul `db.py` obezbeđuje:

1. **WAL journal mode** (`PRAGMA journal_mode=WAL`) — writer i readers
   ne blokiraju jedan drugog. Persist na disku.
2. **60-sekundni busy_timeout** (`PRAGMA busy_timeout=60000`) — kernel
   čeka do 60s pre nego što baci error.
3. **synchronous=NORMAL** — WAL default, brz i siguran (fsync samo na
   checkpoint).
4. **mmap 128 MB** — čitanje kroz memory-mapped I/O, nema I/O sistemskih
   poziva za često pristupljene stranice.
5. **cache 8 MB** — 8 MB page cache u memoriji app-a.
6. **Retry-on-lock decorator** — 6 pokušaja sa exponential backoff
   (100ms → 3.2s ukupno ~6.3s). Ako i posle 6 pokušaja ne uspe, error
   se propušta admin-u (praktično se nikad ne dešava sa WAL + busy_timeout).
7. **Process-level write lock** — dodatna zaštita kad više thread-ova u
   ISTOM procesu simultano piše u isti DB fajl.

### Kako da proverite da radi

`GET /api/system/health` — sekcija `db_pragmas` pokazuje trenutno stanje:

```json
{
  "crm": {
    "journal_mode": "wal",
    "synchronous": 1,
    "busy_timeout_ms": 60000,
    "integrity": "ok",
    "size_bytes": 4210688
  }
}
```

Ako je `journal_mode: "wal"` → hardening je aktivan.

### Šta ako i dalje pukne

Sa ovim setup-om, `database is locked` bi trebalo da se **ne javlja NIKAD**
za normalan promet (do ~50 pisanja/sekundu na PythonAnywhere-u). Ako se
ipak javi:

1. Proverite disk prostor — WAL fajl može da naraste (retry_on_lock i
   auto-checkpoint na 1000 stranica ovo drže na uzdi).
2. Proverite da li neki drugi proces (osim aplikacije) drži fajl
   otvoren — nikad ne kopirajte `.db` fajl dok server radi.

### Backup strategija

Backup thread radi `sqlite3.backup()` (online backup) — ne blokira app.
Backup se enkriptuje Fernet-om, snima u `data/backups/*.fernet`.
Interval: 6h. Retention: 30 dana (starije backup fajlove pojede housekeeping).

---

## 16. Migracija baze van PythonAnywhere-a

Ovo je vaše ključno pitanje — daću vam 4 opcije, rangirane od najlakše do
najbolje po ceni + performansama.

### Opcija A — Ostavite SQLite gde jeste, ali samo baza na drugom volume-u

**Cena:** $0/mesec dopunska (PythonAnywhere plan koji već imate).

**Kako:** montirajte poseban PythonAnywhere disk za `data/` folder.
Backup i dalje ide u S3 (opcija ispod).

**Kada da koristite:** ako je Aspidus glavni klijent i imate <10k dilova/god.
Sa WAL hardening-om iz sekcije 15, SQLite bez problema podržava 20 istovremenih
korisnika za ovaj profil use case-a.

**Ograničenje:** ako PythonAnywhere padne, i vaša baza je nedostupna. Backup
u S3 rešava data loss ali ne downtime.

### Opcija B — Preselite bazu na Turso ($0-$29/mesec)

**Šta je Turso:** managed SQLite kompatibilan cloud servis od kompanije
ChiselStrike. Koristi libSQL protokol koji je 99% kompatibilan sa običnim
SQLite.

**Cena:**
- Free tier: 9 GB storage, 1 milijardu reads/mesec, 25 milijardi writes/mesec
- Starter: $29/mesec (ako prerastete free tier)

**Prednosti:**
- Ne menjate app kod praktično uopšte — samo connection string
- Automatski backup + point-in-time restore
- Multi-region replica (Frankfurt + Virginia + Sydney)
- Fetch iz Beograda ide na Frankfurt regiju → <20ms latencija

**Kako se preseli (radiću ovo za vas ako izaberete):**
1. `pip install libsql-experimental`
2. Zamenim `sqlite3.connect(DB_FILE)` sa `libsql.connect(TURSO_URL, TOKEN)`
3. Import postojećeg .db fajla u Turso preko `turso db shell < dump.sql`

**Vreme migracije:** 2h uz zajedničku sesiju.

**Preporuka:** ovo je **najbolji odnos cena/kvaliteta** za vaš profil.

### Opcija C — Postgres na Supabase ili Neon ($0/mesec free tier)

**Šta je Supabase/Neon:** managed Postgres cloud servis sa besplatnim tier-om
za male projekte.

**Cena:**
- Supabase free: 500 MB storage, 5 GB egress/mesec
- Neon free: 512 MB storage, unlimited compute

**Prednosti:**
- Postgres je "prava" enterprise baza — bolji za >100 istovremenih usera
- Full SQL feature set (window functions, CTE, JSONB, full-text search)
- Multi-user friendly out of the box

**Nedostaci za vaš use case:**
- **Ozbiljna migracija koda** — SQLite → Postgres sintaksa različita na
  ~50 mesta u kodu (npr. `?` vs `%s` za parametre, `AUTOINCREMENT` vs `SERIAL`,
  `datetime('now')` vs `NOW()`, itd.).
- Postgres ima connection pooling da bi bio brz — treba PgBouncer u pipeline.
- **Vreme migracije: 2-3 dana** rada.

**Preporuka:** samo ako predviđate rast > 50 istovremenih korisnika.

### Opcija D — Managed VPS + SQLite ($5-$10/mesec)

**Provideri:** Hetzner Cloud (CX11 = €4.51/mesec, Frankfurt), Digital Ocean
Droplet ($6/mesec, Frankfurt), Linode ($5/mesec).

**Prednosti:**
- Pun root pristup, možete instalirati bilo šta
- Bez PythonAnywhere ograničenja (nema SMTP block, nema CPU limit)
- Backup u S3-compatible storage (Hetzner Storage Box €3/mesec za 1 TB)

**Nedostaci:**
- **Vi ste sysadmin** — SSH ključevi, firewall, updates, monitoring
- Treba web reverse proxy (nginx) + WSGI server (gunicorn) + systemd unit
- **Vreme setup-a: 4-6h** ako niste sysadmin

**Ako želite ovaj put:** preporučujem **Coolify** (open-source Heroku-clone)
na Hetzner-u. Deploy iz git push-a, automatski SSL, automatski restart.

### Moja preporuka za vas — **Opcija B (Turso)**

- Najbrža migracija (2h vs 2-3 dana)
- Automatski backup i restore
- Latencija bolja od trenutnog PythonAnywhere setup-a
- Cena $0 dok ne dostignete free tier limite

Kada budete spremni, javite mi i uradiću migraciju u istoj sesiji.

---

## 17. Deploy checklist za sutra

Pre nego što pustite 20 ljudi u aplikaciju:

### Odmah (danas)

- [ ] **Podesite Resend za OTP** (sekcija 2) — bez ovoga, klijenti neće
      dobijati OTP kodove
- [ ] **Podesite hCaptcha** (sekcija 4.1) — ako je portal javno pristupačan
- [ ] **Preuzmite jedan backup baze lokalno** (Settings → Data Management →
      Download Backup)
- [ ] **Enable TOTP 2FA** za sve admin naloge (sekcija 4.3)
- [ ] **Proverite `GET /api/system/health`** — `journal_mode` mora biti `wal`
- [ ] **Test-OTP mejl na svoju adresu** — proverite da stiže u inbox ne u spam

### Sutra pre puštanja

- [ ] Kreirajte sve user naloge (Users → Add User)
- [ ] Dodelite svakom user-u odgovarajuće permisije (posebno "audit view",
      "portal manage")
- [ ] Uploadujte logo firme (Settings → Company Data)
- [ ] Postavite brand color
- [ ] Uploadujte pečat (samo za admin naloge)
- [ ] Napravite jedan test-dil da vidite da flow radi end-to-end

### Prvih 48h posle puštanja

- [ ] Proverite `/api/system/health` svakih par sati — pratite disk usage
- [ ] Otvorite Audit log — tražite `is_suspicious=true` zapise
- [ ] Otvorite Email queue → nema `dead` mejlova
- [ ] Otvorite Portal activity → svi klijenti su prijavljeni i aktivni

---

## Kontakt za tehničku podršku

Bug ili unapređenje → otvorite issue na GitHub repo-u,
[https://github.com/vladimirmaljm-aspidus/CRM-V0.1/issues](https://github.com/vladimirmaljm-aspidus/CRM-V0.1/issues).

Kritični bugs u produkciji → poruka odmah + attach:
- Screenshot problema
- Copy iz Diagnostics tab-a (Download Report)
- Poslednji audit log export (Settings → Diagnostics → Download JSON Logs)
