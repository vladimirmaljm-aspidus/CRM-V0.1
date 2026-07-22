# Aspidus CRM — Migracija portala na Supabase

## Šta migriramo

- **Portal auth**: SQLite session + OTP → Supabase Auth (email/password + magic link fallback)
- **Portal baza**: `aspidus_portal.db` (SQLite) → Supabase PostgreSQL
- **Fajlovi (KYC, PDF ponude)**: lokalni disk → Supabase Storage
- **Admin CRM ostaje NEPROMENJEN.** Session auth, TOTP, sve postojeće funkcionalnosti rade kao do sada.

Ništa se ne dešava odjednom. Migriramo u 5 faza; posle svake možeš da testiraš.

---

## Odluke koje smo dogovorili

| Odluka | Vrednost |
|---|---|
| Admin CRM auth | Ostaje kao sad (session + TOTP) |
| Postojeći portal klijenti | Automatski dobiju "Set your password" email |
| Fernet vault (SMTP/API ključevi) | Ostaje na serveru |
| Supabase plan | Free tier |
| Free tier housekeeping | Admin bulk-download → obriši iz baze |
| Region | EU (Frankfurt ili Ireland) |
| Magic link | UKLJUČEN kao rezerva pored password-a |

---

## RBAC — 4 nivoa portal klijenta

Sve se skladišti u tabeli `partners`, kolone `can_login`, `portal_level`, `kyc_approved`, `is_premium`.

| Nivo | Vidi | Može | Kako se dodeljuje |
|---|---|---|---|
| **1. Explorer** | Opšte specifikacije (COA), naziv proizvoda, HS kod | Ne vidi cene. RFQ mu vraća maskirane iznose. | Default za svakog novog klijenta. |
| **2. Trader** | Sve što Explorer + cene i RFQ | Može uploadovati KYC dokumente. Ne može preuzimati B2B ugovore. | Admin ručno postavi `portal_level=2`. |
| **3. Verified** | Sve. Signed URL-ovi za PDF preuzimanje. | Sve. | Auto kad admin odobri KYC (`kyc_approved=true`). Može ručno postaviti `portal_level=3`. |
| **4. Premium** | Sve što i Verified | Sve što Verified + **bez GPS zahteva i bez KYC gate-a** (kako je i sad kod `isPremium`) | Admin postavi `is_premium=true`. |

**Kill switch**: `can_login=false` odbija login bez obzira na nivo. Sve postojeće sesije se odmah gase.

---

## Faza 0 — Setup (ovo je sada gotovo)

### Šta si već uradio
- [x] Napravio Supabase projekat `gceaznutofvqbuyypjlh`, region EU
- [x] Site URL: `https://aspidus.pythonanywhere.com/portal/login`
- [x] Providers → Email uključen

### Šta treba da uradiš SADA (10-15 min):

1. **Kreiraj PostgreSQL šemu** (kopiraj `schemas/supabase_schema.sql` u SQL Editor):
   - Supabase Dashboard → **SQL Editor** → **New query**
   - Otvori `schemas/supabase_schema.sql` iz projekta, prekopiraj SVE
   - Nalepi u SQL Editor i klikni **Run**
   - Očekivano: "Success. No rows returned."
   - Verifikuj: pokreni `SELECT tablename FROM pg_tables WHERE schemaname='public';` → treba 15 tabela.

2. **Kreiraj Storage bucket-e**:
   - Storage → **New bucket** → naziv `partner-docs`, **Public: OFF**, File size limit `10 MB`, Allowed MIME: `application/pdf, image/jpeg, image/png, image/webp`
   - Storage → **New bucket** → naziv `offer-pdfs`, **Public: OFF**, File size limit `10 MB`, Allowed MIME: `application/pdf`

3. **Podesi Email Templates** (Authentication → Email Templates):
   - Nalepi 3 šablona iz `docs/SUPABASE_EMAIL_TEMPLATES.md` (odvojen fajl).

4. **URL Configuration** (Authentication → URL Configuration):
   - Site URL: `https://aspidus.pythonanywhere.com/portal/login` ✓
   - **Redirect URLs** → dodaj: `https://aspidus.pythonanywhere.com/portal/**`

5. **Postavi environment varijable na PythonAnywhere** (Web tab → Files → `.env`):
   ```
   SUPABASE_URL=https://gceaznutofvqbuyypjlh.supabase.co
   SUPABASE_ANON_KEY=<publishable ključ koji imaš>
   SUPABASE_SERVICE_ROLE_KEY=<secret ključ, nikad ne u git>
   SUPABASE_JWT_SECRET=<iz Project Settings → API → JWT Settings>
   ```
   **Rotiraj secret ključ** kad završimo, pošto je bio u chat-u.

6. **Javi mi** kad je ovo sve gotovo. Onda krećem sa Fazom 1.

---

## Faza 1 — Auth switch (2-3 dana rada)

**Rezultat**: portal login stranica koristi Supabase Auth. Klijenti se prijavljuju email + password ili magic link.

### Šta ja radim:
- Novi Python modul `auth_supabase.py` sa dekoratorom `@require_portal_supabase_auth` koji verifikuje JWT i pronalazi partner-a preko `email`.
- Zamena portal login stranice — čist redizajn (vidi Faza 3 dole).
- Endpoint `/api/portal/auth/supabase/exchange` koji prima Supabase JWT sa frontenda i vraća partner podatke.
- Sve postojeće portal rute (`/api/portal/*`) prelaze na novi dekorator.
- **Migracija postojećih partnera** (skript `scripts/migrate_partners_to_supabase.py`):
  1. Čita SQLite `partners` tabelu.
  2. Za svakog sa validnim email-om — kreira user-a u Supabase Auth preko admin API-ja (bez lozinke).
  3. Insertuje odgovarajući red u novu `partners` tabelu u Postgres-u.
  4. Šalje mu "Set your password" email (Supabase automatski).
  5. Loguje sve u `logs/migration_partners.log` sa listom uspešnih/neuspešnih.

### Šta ti radiš:
- Pokreneš `python scripts/migrate_partners_to_supabase.py --dry-run` prvo.
- Ako izgleda dobro, pokreneš bez `--dry-run`.
- Klijenti dobijaju email — ništa ne moraju da rade osim da kliknu link i postave lozinku.

---

## Faza 2 — Storage switch (2-3 dana)

**Odgovor na tvoje pitanje**: **automatski, u pozadinskom job-u** — ne moraš ti ništa.

### Novi upload-ovi (odmah počinju da idu u Supabase):
- KYC dokument → `partner-docs/{partner_id}/kyc-{uuid}.pdf`
- Portal upload → `partner-docs/{partner_id}/upload-{uuid}.pdf`
- Generisana ponuda PDF → `offer-pdfs/{offer_id}/offer-v{n}.pdf`
- Svaki upload dobija zapis u `storage_objects` tabelu (za tvoj housekeeping — vidi dole).

### Stari fajlovi (jednokratna migracija):
- `scripts/migrate_files_to_supabase.py` prolazi kroz `uploads/` + `portal_uploads/` foldere na disku
- Uploaduje svaki fajl u odgovarajući bucket
- Ažurira reference u `shared_documents.storage_bucket` + `storage_path`
- Radi u pozadini (možeš da pokreneš pa da ideš na kafu — javlja ti na kraju koliko je migrirano)

### Free Tier housekeeping — NOVI ADMIN EKRAN "Storage manager":
- Ekran ti prikaže tabelu svih storage objekata + veličinu + partner-a
- Filter po partneru, tipu, datumu
- Dugme **"Download & Delete"** — preuzme fajl na tvoj Desktop u strukturu `~/Desktop/aspidus-archive/{partner_name}/{entity_type}/`, pa obriše iz Supabase Storage-a i označi kao `archived_at`
- Bulk selekcija: možeš da čekiraš 10 fajlova i klikneš jedno dugme
- Pokazatelj popunjenosti (npr. "0.7 / 1.0 GB Free tier")
- Ako izbrišeš fajl a zapis o njemu ostane u `shared_documents`, portal će klijentu prikazati "Document archived — request from admin".

---

## Faza 3 — Portal frontend + login redizajn (1-2 dana)

**Šta ćeš dobiti** — novi login flow:

1. Klijent dođe na `/portal/login`.
2. Vidi **elegantnu 2-koraknu formu**:
   - Korak 1: samo Email polje. Klik "Continue".
   - Korak 2 (posle validnog email-a): 2 opcije — "Enter password" ili "Email me a magic link" (na 1 klik).
3. Nakon uspešnog login-a, ako je klijent u Postgres bazi → `/portal/dashboard`. Ako nije (novi email) → forma za osnovne podatke firme + auto-kreira `partners` red sa `portal_level=1`.

**Vizuelno** — koristim postojeći dizajn sistem koji već imaš (accent boje, tipografija, gradient hero), samo modernizovan:
- Full-height 2-column layout: leva strana marketing hero + brand, desna strana forma
- Umesto trenutnog forme-in-a-box, staklaste kartice sa suptilnim gradient border-om
- Loading state je smooth (bez skoka), toast poruke su bottom-right non-blocking
- Dark mode se automatski poštuje ako klijent ima OS preference
- Mobilno: single column, forma na vrhu

Za tebe u CRM-u — na "Partner Details" ekranu (već postojeći):
```
┌─ Portal Access ─────────────────────────────────┐
│                                                 │
│  [🟢 CAN LOGIN   ]        ← toggle kill switch  │
│                                                 │
│  Level:  [1 Explorer] [2 Trader] [3 Verified]  │
│          [4 Premium ★]                          │
│                                                 │
│  ✓ KYC Approved  (auto-set on approve)          │
│                                                 │
│  Last login: 2026-07-22 14:23 from Belgrade     │
│  [Force logout]  [Reset password]  [Copy link]  │
└─────────────────────────────────────────────────┘
```

Na KYC review ekranu — dugme **"Approve → auto Level 3"** koje jednim klikom postavi `kyc_approved=true` i `portal_level=3` ako je manje od 3.

---

## Faza 4 — Data layer switch (3-4 dana)

**Rezultat**: portal rute čitaju/pišu direktno u Supabase Postgres, ne u SQLite.

- Novi modul `db_postgres.py` sa `psycopg[binary,pool]` connection pool-om.
- Postojeći `sqlite3.connect(...)` pozivi u `routes/portal/*` se menjaju na `pg_conn()` context manager.
- Adapterski sloj: postojeće JSON strukture ostaju (samo se čuvaju u `JSONB` koloni), pa nema promene u portal frontend-u.
- Testovi (postojeći `e2e_brutal`, `e2e_massive`, `e2e_logic`) će raditi protiv nove baze. Verifikujemo da je 100% zeleno pre nego što isključim SQLite.

Admin CRM u ovoj fazi i dalje piše u SQLite. Zato imamo **dvosmerni sync** za period tranzicije — Flask piše u OBE baze dok se ne prebacimo potpuno. Kad Faza 5 završi, isključimo SQLite pisanje.

---

## Faza 5 — Retirement SQLite portal-a (1 dan)

**Šta ovo znači i da li ti stvara problem?**

**Šta je "retirement"**: prestanemo da pišemo u `aspidus_portal.db` fajl na PythonAnywhere disku. Fajl ostaje na serveru kao read-only backup 30 dana, pa se arhivira.

**Koristi tebi**:
1. Nema više dual-write koda → čist, brz portal.
2. Baza radi u Supabase-u sa pravim performansama, indeksima, i FTS-om.
3. Automatski backup-i Supabase-a (Point-in-Time Recovery na Pro planu; free ima 7-day backup).
4. Nema više "database is locked" grešaka koje smo imali na SQLite.
5. Cloud baza znači 0 downtime kad se PythonAnywhere restart.

**Da li pravi problem u budućnosti?**
- **Ne pravi**, POD USLOVOM da:
  - Uradiš full backup pre Faze 5 (već imamo `scripts/db_export_full.py` — pokrenućemo ga automatski pre prekidača).
  - Održiš Supabase pretplatu (Free tier je OK dok si u granicama; ako pređeš, Pro je $25/mes).
  - Ne obrišeš Supabase projekat.
- **Rizik ako ostavimo SQLite paralelno predugo**: dva izvora istine → glavobolja kada se ne slažu (npr. klijent update-uje profil, ali baze različito reaguju).
- **Fallback plan** ako baš zatreba: `scripts/db_export_full.py` pravi tar.gz sa svim SQLite bazama JOŠ SE PRAVI I DALJE, sve dok se ne uradi Faza 5. Ako Supabase nešto zabaguje, možeš da vratiš na SQLite iz backup-a za manje od 10 minuta (imaš uputstvo u `RESTORE.md` unutar backup arhive).

---

## Faza 6 (opciono, za kasnije)

Ako se u nekom trenutku odlučiš i za admin CRM na Supabase — to je posebna faza, ne diramo je sad.

---

## Timeline (moja procena)

| Faza | Trajanje mog rada | Trajanje tvog checka |
|---|---|---|
| 0. Setup | 30 min tvog rada (već krećeš) | - |
| 1. Auth | 2-3 dana | 30 min testiranja |
| 2. Storage | 2-3 dana | 1h testiranja |
| 3. Frontend | 1-2 dana | 1h testiranja |
| 4. Data | 3-4 dana | 2h testiranja + dual-run |
| 5. Retirement | 1 dan | 30 min checka |
| **Ukupno** | **9-13 dana rada** | **~5h ukupno testiranja** |

Radim po fazama i posle svake ti šaljem "kraj faze N" javu sa listom šta da testiraš + potvrdu da si zadovoljan pre nego što krenem sa sledećom.

---

## Sigurnost — moje ključne mere

1. **Service role ključ** samo u `.env` na serveru, nikada u git, frontend, ili log.
2. **JWT verifikacija** offline sa `SUPABASE_JWT_SECRET` (ne zove Supabase API na svaki request → brzo).
3. **Signed URL-ovi** za download imaju TTL 60 sekundi. Nema perpetual URL-ova.
4. **Rate limiting** na svim `/api/portal/*` rutama — 60 zahteva / min / partner.
5. **Kill switch** (`can_login=false`) prepiruje sve postojeće sesije preko token version bump-a.
6. **Sve DB operacije** kroz `psycopg` transakcije — ni jedna nedovršena promena.
7. **KYC file upload** validira magic bytes (kao i sada, prošli su brutal testovi).
8. **CSP header** dozvoljava samo Supabase domenu (`*.supabase.co`) — bez third-party skripti.

---

## Ako nešto krene po zlu

- Backup pre Faze 5 → tar.gz + RESTORE.md.
- Rollback Auth-a: postoji flag `USE_SUPABASE_AUTH=false` u `.env`, vrati na `true=false` → portal opet čita SQLite session (dok god SQLite fajl još postoji).
- Rollback Storage-a: fajlovi ostaju na disku dok Faza 5 ne završi.
- Rollback Data-a: dvosmerni sync znači SQLite ima najnovije podatke.

---

**Sledeći korak**: pokreni tačke 1-5 iz Faze 0 gore, pa mi javi. Onda krećem sa Fazom 1.
