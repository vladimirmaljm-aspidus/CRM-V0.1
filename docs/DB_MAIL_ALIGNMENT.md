# Aspidus — Database & Email Alignment (v22.2)

> **Namena ovog dokumenta.** Jedno mesto istine za sve što se tiče
> baza podataka (SQLite ↔ Supabase Postgres) i email sistema (SMTP,
> Resend, Supabase Auth). Sve komande, feature flag-ovi, dugmad u
> admin panelu i troubleshooting koraci na jednom mestu.
>
> Kad god menjaš nešto oko baze ili mejlova, **ažuriraj ovaj fajl**.

---

## Sadržaj

1. [Trenutno stanje sistema](#1-trenutno-stanje-sistema)
2. [Baze — arhitektura i feature flag-ovi](#2-baze---arhitektura-i-feature-flag-ovi)
3. [Dual-write i migracija](#3-dual-write-i-migracija)
4. [Reconcile: kako proveriti da su dve baze u sinhronizaciji](#4-reconcile-kako-proveriti-da-su-dve-baze-u-sinhronizaciji)
5. [Email sistem — potpuni pregled](#5-email-sistem---potpuni-pregled)
6. [Email trigger tabela (kada koji mejl ide)](#6-email-trigger-tabela)
7. [Supabase Auth email templates — status](#7-supabase-auth-email-templates)
8. [Storage (fajlovi) — dual-write i signed URL-ovi](#8-storage-fajlovi)
9. [Troubleshooting — najčešći problemi](#9-troubleshooting)
10. [Roadmap i sledeći koraci](#10-roadmap)

---

## 1. Trenutno stanje sistema

| Sloj | Primarno | Sekundarno / Fallback |
|---|---|---|
| **Admin CRM auth** | Flask session + TOTP | — (ne diramo) |
| **Portal klijent auth** | Supabase Auth (JWT, ES256/HS256) | Legacy OTP (kad je `USE_SUPABASE_AUTH=false`) |
| **CRM podaci (partners, deals, offers, products…)** | SQLite (`aspidus_crm.db`) | Supabase Postgres (kad se ukljuci `USE_SUPABASE_DB=true`) |
| **Portal podaci (KYC, portal_products, activity_log)** | SQLite (`aspidus_portal.db`) | Supabase Postgres |
| **Audit log** | SQLite (`aspidus_audit.db`) | — (ne migrira se u prvoj fazi) |
| **Fajlovi (KYC, PDF ponude, portal upload)** | Lokalni disk (`data/uploads/`) | Supabase Storage (kad se ukljuci `USE_SUPABASE_STORAGE=true`), sa **best-effort mirror** iz portal upload-a |
| **Transakcijski mejlovi** (welcome, KYC approved, offer notif.) | `utils_email` queue → SMTP | Automatski fallback na **Resend/SendGrid/Postmark** preko `mail_providers` |
| **Auth mejlovi** (reset password, magic link, invite) | Supabase Auth (šablon iz Dashboard-a) | — |

**Ključna filozofija**: NIKAD ne prelazimo naglo. Sve prelaze idu kroz
**feature flag-ove** (u `.env` ili live iz `/admin/supabase` panela) i
**dual-write mod** gde novi zapisi idu i u SQLite i u Supabase paralelno.

---

## 2. Baze — arhitektura i feature flag-ovi

### Fajlovi baza

```
data/
├── aspidus_crm.db         # partners, products, deals, offers, transactions, users
├── aspidus_portal.db      # kyc_submissions, portal_products, activity_log, profile_change_requests
└── aspidus_audit.db       # audit_log (SECURITY, CREATE, UPDATE, DELETE, ERROR)
```

Sve tri se otvaraju sa `PRAGMA journal_mode=WAL` i `busy_timeout=30000`
(već postavljeno u `database.py` i `routes/portal/__init__.py`).

### Feature flags (.env)

```bash
# Portal auth — koje 'ulazne kapije' su aktivne za klijente
USE_SUPABASE_AUTH=true|false        # true = koristi Supabase Auth; false = legacy OTP
SUPABASE_URL=https://<proj>.supabase.co
SUPABASE_ANON_KEY=eyJhbGc...        # javni klijent-side kljuc
SUPABASE_SERVICE_ROLE_KEY=eyJhbGc.. # ADMIN, NIKAD u browser
SUPABASE_JWT_SECRET=...             # samo za HS256 legacy tokene

# Data layer — odakle citaju/pisu partners/products/deals…
USE_SUPABASE_DB=true|false          # true = data_layer koristi PostgREST
DUAL_WRITE_MODE=true|false          # true = pise i u SQLite I u Supabase paralelno

# Storage — gde idu upload-ovani fajlovi
USE_SUPABASE_STORAGE=true|false     # true = mirror u Supabase Storage
```

### Runtime flag switching (bez server restart-a)

Idi na `/admin/supabase` (admin only). U kartici **Feature Flags** klikni
odgovarajuci **Toggle** — postavlja se **samo u trenutnom procesu**. Za
**trajnu** izmenu, edituj `.env` i pokreni "Reload Web App" na
PythonAnywhere.

Ovo je namerno — dual-toggle strategija:
1. **Test u runtime-u** (bez rizika) da vidiš da li flag radi.
2. **Ako radi**, prebaci u `.env` da preživi restart.
3. **Ako ne radi**, `Toggle` nazad — instant rollback.

---

## 3. Dual-write i migracija

### Zašto dual-write

Prelazak sa SQLite na Postgres **NE SME** biti hard-cut. Ako Supabase
ima problem, klijent koji odmah pravi novu narudžbinu bi izgubio podatke.
Zato ide u ovom redosledu:

```
FAZA A: USE_SUPABASE_DB=false, DUAL_WRITE_MODE=false
        → sve u SQLite. Baseline. Trenutno stanje.

FAZA B: USE_SUPABASE_DB=false, DUAL_WRITE_MODE=true
        → čita iz SQLite, ali svaki save ide u OBA.
        → Posle 1-2 nedelje bez incidenata, pusti reconcile
          (dole) da vidiš da su brojevi identični.

FAZA C: USE_SUPABASE_DB=true, DUAL_WRITE_MODE=true
        → primarno iz Postgres-a, ali još piše i u SQLite (safety).
        → Idealno: minimum 2 nedelje pre nego što skineš DUAL_WRITE.

FAZA D: USE_SUPABASE_DB=true, DUAL_WRITE_MODE=false
        → SQLite je sada samo backup, ne dobija nove zapise.
        → Posle sledećeg backup-a možeš da ga arhiviraš.
```

### Skripta za jednokratnu migraciju svih postojećih redova

```bash
cd /home/aspidus/mysite/CRM
source .env

# 1) DRY-RUN — samo prebrojava, ne piše nista
python3.13 scripts/migrate_data_to_supabase.py --dry-run

# 2) Pravi run — piše u Supabase (UPSERT po id, bezbedno je ponoviti)
python3.13 scripts/migrate_data_to_supabase.py --confirm

# 3) Samo odredjene tabele
python3.13 scripts/migrate_data_to_supabase.py --confirm --tables partners,products

# 4) Sa limitom (za test)
python3.13 scripts/migrate_data_to_supabase.py --confirm --limit 50 --batch 10
```

Isto se moze pokrenuti iz UI-ja: **`/admin/supabase` → Dry-Run / Start Full Migration**.

### Partner Auth migracija (samo email-i, ne data)

Za postojece klijente kojima treba Supabase Auth nalog:

```bash
python3.13 scripts/migrate_partners_to_supabase.py --only-active
# sa emailom (posalje reset link):
python3.13 scripts/migrate_partners_to_supabase.py --only-active --send-emails
```

---

## 4. Reconcile: kako proveriti da su dve baze u sinhronizaciji

Posle bilo koje migracije ili nedelju dana dual-write-a, pokreni:

```bash
python3.13 scripts/reconcile_sqlite_supabase.py
```

Šta radi:
- Za svaku tabelu iz `MIGRATION_PLAN`, uzima **broj redova** iz SQLite i Supabase-a.
- Uzima **sample od 10 najsvezijih ID-eva** iz obe baze.
- Detektuje: **razliku u brojevima**, **ID-eve koji su u jednoj a ne u drugoj bazi**.

Izlaz:

```
✓ partners        (SQLite: 42, Supabase: 42)
✓ products        (SQLite: 88, Supabase: 88)
⚠ deals           (SQLite: 15, Supabase: 12)
     ⚠ U SQLite-u ima 3 ID-eva koje NEMA u Supabase-u:
        · a1b2-c3d4
        · e5f6-g7h8
        · i9j0-k1l2
```

Ako vidiš drift:
1. Otvori `/admin/errors` — proveri da li je bilo Supabase pisanja neuspešnog.
2. Pokreni `python3.13 scripts/migrate_data_to_supabase.py --confirm --only-missing` (planirano).
3. Za sad, `--confirm` bez `--only-missing` radi UPSERT po id, tako da će
   postojeći redovi biti overwrite-ovani novim, i missing će biti insert-ovani.

### JSON mod (za skripte / monitoring)

```bash
python3.13 scripts/reconcile_sqlite_supabase.py --json > reports/reconcile-$(date +%F).json
```

---

## 5. Email sistem — potpuni pregled

Aplikacija ima **3 nezavisna kanala** koja šalju mejl:

### A) `utils_email` — glavni transactional pipeline

`utils_email.py` je **jedan endpoint** za sve transakcijske mejlove
(welcome, KYC approved, offer notification, KYC update requested).

- **Redosled slanja:**
  1. Probaj **Resend/SendGrid/Postmark** preko `mail_providers` (ako je
     `RESEND_API_KEY`/`SENDGRID_API_KEY`/`POSTMARK_API_KEY` postavljen).
  2. Ako to ne uspe → probaj **SMTP** iz `.env` (`SMTP_HOST`, `SMTP_USER`,
     `SMTP_PASSWORD`).
  3. Ako SMTP odbije (auth, connect fail, disconnect) → **park u
     `email_queue` tabelu** sa `next_retry_at`.
- **Circuit breaker**: posle N SMTP grešaka za redom (`utils_reliability`),
  otvori circuit i 60s ne pokušava — direktno u queue.
- **Queue drain**: `process_email_queue()` se pokreće iz `utils.py`
  (background thread) svakih par minuta; obrađuje do 20 email-a odjednom.

### B) `auth_supabase` — Supabase Auth mejlovi

Kada klijent klikne "Reset password" ili admin klikne "Send Portal Invite",
poziv ide **direktno** na Supabase-ov `/auth/v1/*` endpoint. Šablon
mejla je onaj koji je admin ručno stilizovao u **Supabase Dashboard →
Auth → Email Templates** (vidi `docs/SUPABASE_EMAIL_TEMPLATES.md`).

**Ne prolaze kroz `utils_email` queue.** Ako Supabase ima outage, mejl
neće biti poslat — ali imamo `@retry(3x, exp backoff)` oko svih ovih
funkcija (`utils_reliability.retry`) za transient network greške.

### C) `routes/comms` — legacy admin custom mejl

`/api/comms/send_email` — dugme "Send email" iz CRM-a, gde admin ručno
kuca poruku klijentu. Ide **direktno preko SMTP**, ne kroz queue. Ovo se
zadržava zato što je namenjeno interaktivnom slanju (admin čeka feedback
"success/error" u toast-u).

---

## 6. Email trigger tabela

Automatski generisan sa:

```bash
python3.13 scripts/audit_email_triggers.py
```

Manuelno održavan pregled najbitnijih trigger-a:

| Kada | Funkcija | Kanal | Šablon |
|---|---|---|---|
| Admin ručno "Generate portal link" | `send_portal_welcome` | utils_email | `send_portal_welcome` u `utils_email.py` |
| Admin ručno "Send Portal Invite / Reset" (Supabase Auth on) | `send_password_reset` | Supabase Auth | "Reset Password" u Supabase Dashboard |
| Admin ručno "Set Portal Password" | (nema mejl) | — | Klijent obavesti WhatsApp-om |
| Klijent klikne "Forgot password?" na portal login | `send_password_reset` | Supabase Auth | isto kao gore |
| Klijent klikne "Send me a login link" | `send_magic_link` | Supabase Auth | "Magic Link" u Supabase Dashboard |
| Admin approve KYC | `send_kyc_approved` | utils_email | `send_kyc_approved` |
| Admin traži izmene na KYC | `send_kyc_update_requested` | utils_email | `send_kyc_update_requested` |
| Admin izda ponudu (offer.pdf generisan) | `send_new_offer` | utils_email | `send_new_offer` |
| Klijent submituje KYC | (samo notifikacija u CRM) | — | — |
| Admin klikne "Send" u komunikaciji sa klijentom | `POST /api/comms/send_email` | comms.py → direct SMTP | ručno |

Za **potpunu listu** call site-ova u kodu:

```bash
python3.13 scripts/audit_email_triggers.py
```

---

## 7. Supabase Auth email templates

Svih 4 šablona (Confirm signup, Invite user, Magic Link, Reset Password)
su ručno stilizovani u Supabase Dashboard-u. Postavlja ih admin jednom;
ne diramo ih iz koda.

Vidi `docs/SUPABASE_EMAIL_TEMPLATES.md` za konkretan HTML koji koristimo.

**Redirect URL-ovi** koji moraju biti dozvoljeni u Supabase → Auth →
URL Configuration → Redirect URLs:

- `https://aspidus.pythonanywhere.com/portal/login`
- `https://aspidus.pythonanywhere.com/portal/login#type=recovery`

Za lokalni razvoj dodaj:
- `http://localhost:5000/portal/login`

---

## 8. Storage (fajlovi)

### Bucket bootstrap (jednom, iz admin panela)

`/admin/supabase` → **Supabase Storage** kartica → **Init Buckets**

Kreira 3 bucket-a kao **PRIVATE**:
- `partner-docs` — KYC dokumenti, ugovori, sertifikati
- `offer-pdfs` — server-generisani PDF-ovi ponuda/faktura
- `portal-uploads` — klijentski upload iz portala (KYC files, per-osoba pasoši)

### Dual-write u KYC upload flow-u

Kada klijent uploaduje fajl kroz portal:
1. Fajl se snima **lokalno** (`data/portal_uploads/doc_<uuid>.ext`).
2. Ako je `USE_SUPABASE_STORAGE=true`, **best-effort mirror** u
   `partner-docs` bucket sa path-om `partners/<partner_id>/portal-uploads/<file>`.
3. Vraćeni URL koji admin vidi u KYC pregledu i dalje pokazuje na
   `/portal_uploads/<file>` (serving iz lokalnog diska).
4. Ako Storage padne, mirror se preskače (log u `/admin/errors`), lokalna
   kopija ostaje kao autoritativna.

### Signed URL-ovi (planirano)

Kad presečemo na Supabase Storage kao primarno, dodaćemo:
- Frontend zove `/api/portal/file/<id>/signed-url?ttl=300`
- Server preko `utils_storage.signed_url()` vraća 5-min link
- Klijent ide direktno na `<signed_url>` (bypass Flask, direktno Storage CDN)

---

## 9. Troubleshooting

### "Mejl je poslat ali klijent ga nije dobio"

1. Otvori `/admin/errors` — prvo pogledaj tu.
2. `sqlite3 data/aspidus_crm.db "SELECT id, recipient, status, last_error FROM email_queue ORDER BY queued_at DESC LIMIT 20"`
   - Ako je `status='pending'` — queue je zapao, restartuj web app.
   - Ako je `status='failed'` sa `last_error` — pogledaj poruku.
3. Ako klijent ide na Gmail/Outlook, proveri spam folder + DKIM/SPF
   podešavanja za `SMTP_FROM_EMAIL` domain.
4. Za Supabase Auth mejlove: **Supabase Dashboard → Logs → Auth** —
   tu vidiš da li je poslat.

### "Klijent ne može da se uloguje"

1. Otvori `/admin/health` — proveri da li je "Supabase Auth" zelen.
2. Ako je "circuit_open" — Supabase Auth API vrati nekoliko puta grešku.
   Sačekaj 30s da se resetuje ili klikni "Refresh Now".
3. Otvori `/admin/errors` — pogledaj request_id koji je klijent kopirao iz
   error toast-a (Ref: badge).
4. Ako je `USE_SUPABASE_AUTH=true` a Supabase nedostupan → toggle na
   `false` (fallback na legacy OTP koji koristi lokalnu bazu).

### "Portal fajlovi se ne otvaraju"

1. Ako je `USE_SUPABASE_STORAGE=true`, proveri `/admin/supabase` →
   Storage kartica → svaki bucket bi trebalo da bude "exists".
2. Ako je storage isključen, proveri `data/portal_uploads/` folder na
   diskuu — možda je disk pun (`df -h`).

### "Reconcile pokazuje drift"

1. `python3.13 scripts/reconcile_sqlite_supabase.py --sample 30`
2. Kopiraj ID-eve koji fale u Supabase-u.
3. `python3.13 scripts/migrate_data_to_supabase.py --confirm` — UPSERT
   po id, tako da će missing biti dodati bez duplikata.
4. Ponovo pokreni reconcile → očekuje se ✓.

---

## 10. Roadmap

Sledece faze koje jos treba uraditi:

- [ ] **Sync-back script** — kad je USE_SUPABASE_DB=true a neko nešto
      napiše u SQLite direktno (npr. iz test-a), reconcile treba da moze
      da push-uje nazad. Trenutno je jednosmerno SQLite→Supabase.
- [ ] **Signed URL migration** — svi `/portal_uploads/<file>` linkovi
      da se zamene sa Supabase signed URL-om posle Faza D.
- [ ] **Email queue admin UI** — dugme u `/admin/health` za "Retry failed
      emails" i "Purge stale queue entries".
- [ ] **Supabase Auth webhook** — kad Supabase pošalje "user.created" ili
      "password.changed", da automatski osvezi `partners` u SQLite. Za sad
      admin mora ručno da klikne Refresh.
- [ ] **Encrypted backup slanje na off-site** — trenutno ide u
      `data/backups/*.fernet`. Trebalo bi opcionalno slati u S3
      kompatibilan storage (Supabase Storage može).

---

**Verzija dokumenta:** v22.2
**Poslednja izmena:** vezano za commit koji uvodi Faza 4/5/6 + display customization.
**Kome se javiti za pitanja:** vladimir.maljm@gmail.com
