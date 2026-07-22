# PythonAnywhere setup — kako .env fajl ući u sistem

Cilj: postaviti Supabase kredencijale na tvom PythonAnywhere serveru tako
da ih Flask app čita pri startu, bez commit-a u git.

Traje 5-10 minuta.

---

## Korak 1 — Nađi 2 vrednosti u Supabase Dashboard-u

### 1A) JWT Secret

1. Otvori Supabase Dashboard → tvoj projekat `aspidus-crm-prod` (ili kako si nazvao)
2. U levom meniju: **Project Settings** (ikonica ⚙️ na dnu)
3. Klikni **API** (u listi settings-a)
4. Skroluj dole do sekcije **"JWT Settings"** (obično 2/3 stranice na dole)
5. Videćeš polje **"JWT Secret"** — pored njega dugme **"Reveal"**. Klik.
6. Pojaviće se dugačak string (40+ karaktera, izgleda nasumično). To je tvoj JWT Secret.
7. **Kopiraj ga** — trebaće ti u sledećem koraku.

### 1B) Postgres Connection String + Database Password

1. Isti projekat → **Project Settings** → **Database**
2. Skroluj do sekcije **"Connection string"**
3. Ispod polja gde možeš da biraš tip konekcije, **klikni tab "Session mode"** (port 5432, ne "Transaction mode" koji je port 6543)
4. Kopiraj ceo string ispod (počinje sa `postgresql://postgres:...`)
5. U tom stringu vidiš `[YOUR-PASSWORD]` na sredini — **treba tu da zameniš pravu DB lozinku**.

**Gde ti je DB lozinka?**
- Ako si sačuvao pri kreiranju projekta — koristi tu.
- Ako si zaboravio — u istoj sekciji **"Database"** postoji dugme **"Reset database password"**. Klikni, generisaće novu (možeš i sam da uneseš), **kopiraj je i sačuvaj u password manager**. Ne postoji "vidi mi lozinku" — samo reset.

**Kompletirano** izgleda otprilike:
```
postgresql://postgres:MojaSuperJakaLozinka123@db.gceaznutofvqbuyypjlh.supabase.co:5432/postgres
```

---

## Korak 2 — Kreiraj `.env` fajl na PythonAnywhere

1. Uloguj se na PythonAnywhere.
2. Otvori tab **"Files"** (gore u navigaciji).
3. Navigiraj do foldera gde ti stoji `app.py` (obično `~/mysite/` ili `~/CRM/`).
4. U tekst polju **"New file"** unesi ime `.env` (sa tačkom na početku) i klikni **"New file"**.
5. Otvoriće se editor za novi fajl. **Nalepi ovaj sadržaj**:

```bash
# --- ADMIN CRM (postojeće — ne diraj vrednosti koje već rade) ---
ADMIN_USERNAME=<tvoj CRM admin username>
ADMIN_PASSWORD=<tvoja CRM admin lozinka>
DATA_DIR=/home/aspidus/mysite
SESSION_COOKIE_SECURE=true

# --- SUPABASE (NOVO) ---
SUPABASE_URL=https://gceaznutofvqbuyypjlh.supabase.co
SUPABASE_ANON_KEY=<NALEPI OVDE Publishable key iz Supabase Dashboard-a — vidi napomenu 1>
SUPABASE_SERVICE_ROLE_KEY=<NALEPI OVDE Secret key iz Supabase Dashboard-a — vidi napomenu 2>
SUPABASE_JWT_SECRET=<NALEPI OVDE JWT Secret iz koraka 1A>
SUPABASE_DB_URL=<NALEPI OVDE ceo connection string iz koraka 1B, sa lozinkom>

# --- FEATURE FLAGS (svi ostaju false za sada) ---
USE_SUPABASE_AUTH=false
USE_SUPABASE_STORAGE=false
USE_SUPABASE_DB=false
DUAL_WRITE_MODE=false
```

6. Zameni sva mesta gde piše `<...>` sa pravim vrednostima:
   - **Napomena 1 — Publishable key**: Supabase Dashboard → Project Settings → API → "Project API keys" → "anon public" (ili "Publishable" u novom UI-ju). Kopiraj vrednost, nalepi umesto `<NALEPI OVDE Publishable key ...>`.
   - **Napomena 2 — Secret key**: Isti ekran (Project Settings → API) → "service_role" (klik "Reveal") ili "Secret" u novom UI-ju. Kopiraj i nalepi umesto `<NALEPI OVDE Secret key ...>`. Nikad ovaj ne u git.
   - **JWT Secret**: iz koraka 1A gore.
   - **DB URL sa lozinkom**: iz koraka 1B gore.
7. **Save**.

**Provera bezbednosti**: uradi `ls -la ~/mysite/` u Bash konzoli na PythonAnywhere-u. Fajl `.env` treba da postoji. Ako nije, možda je editor sačuvao kao `env` bez tačke — preimenuj ga na `.env`.

---

## Korak 3 — Instaliraj `python-dotenv` (jednokratno)

U PythonAnywhere:
1. Otvori tab **"Consoles"** → **"Start a new console"** → **"Bash"**
2. U konzoli otkucaj:

```bash
pip3.11 install --user python-dotenv supabase psycopg[binary,pool]
```

(Ako koristiš drugu Python verziju — npr. `pip3.10` — prilagodi.)

Čekaj 30-60 sekundi da se instalira. Očekivano: "Successfully installed python-dotenv-... supabase-... psycopg-...".

---

## Korak 4 — Reci Flask app-u da učita `.env` pri startu

1. Otvori tab **"Files"** → nađi WSGI fajl (obično `/var/www/aspidus_pythonanywhere_com_wsgi.py` ili slično).
2. Klikni na njega — otvara se editor.
3. Na **vrh fajla**, IZNAD linija `import sys` i sličnih, dodaj:

```python
# Učitaj .env varijable pre importa Flask app-a
from dotenv import load_dotenv
import os
load_dotenv('/home/aspidus/mysite/.env')  # zameni "aspidus" tvojim PA username-om
```

4. Save.
5. Idi na tab **"Web"** → klikni veliko zeleno dugme **"Reload"** (pored URL-a tvog sajta).

---

## Korak 5 — Verifikacija da je sve povezano

Nakon što si sve podesio, otvori tab **"Consoles"** → nova Bash konzola i pokreni:

```bash
cd ~/mysite
python3.11 scripts/verify_supabase_connection.py
```

Ako sve radi, videćeš:

```
✓ .env loaded from /home/aspidus/mysite/.env
✓ SUPABASE_URL:              https://gceaznutofvqbuyypjlh.supabase.co
✓ SUPABASE_ANON_KEY:         sb_publishable_… (present)
✓ SUPABASE_SERVICE_ROLE_KEY: sb_secret_… (present)
✓ SUPABASE_JWT_SECRET:       … (present, 40 chars)
✓ SUPABASE_DB_URL:           postgresql://postgres:***@db.…supabase.co:5432/postgres

── Postgres connectivity ──
✓ Connected to PostgreSQL 16.x
✓ Found 15 tables in public schema:
   audit_logs, deals, demands, document_register, document_revisions,
   kyc_submissions, offer_versions, offers, partners, portal_hidden_items,
   portal_products, products, profile_change_requests, shared_documents,
   storage_objects

── Storage buckets ──
✓ Bucket 'partner-docs' exists (private)
✓ Bucket 'offer-pdfs' exists (private)

── Auth ──
✓ Auth admin API reachable (0 users currently)

✅ SVE JE POVEZANO. Spreman si za Fazu 1.
```

Ako vidiš neki `✗` red, tu je problem — copy-paste izlaz i pošalji mi.

---

## Najčešće greške i rešenja

**"psycopg.OperationalError: could not connect to server"**
→ DB URL nema tačnu lozinku ILI si nalepio `[YOUR-PASSWORD]` bez zamene. Vrati se na Korak 1B.

**"Invalid API key"**
→ Kopirao si Publishable/Anon umesto Service Role, ili obrnuto. Proveri koji je koji.

**"JWT secret does not match"**
→ Kopirao si iz sekcije "API keys" umesto iz "JWT Settings". Ono je *pod* API stranicom, dole na dnu.

**".env not found"**
→ Fajl je verovatno sačuvan bez tačke na početku. Preimenuj sa PythonAnywhere Bash: `mv env .env`.

---

**Kad ti gornji verify skript prijavi ✅, javi mi** — krećem sa Fazom 1
(Supabase Auth switch, migracija postojećih klijenata, redizajn login stranice).
