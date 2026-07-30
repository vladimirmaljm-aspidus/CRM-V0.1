# SUPABASE-ONLY MIGRACIJA — STATUS I PLAN

**Verzija:** V24.4 (2026-07-30)
**Cilj:** Aplikacija radi 100% preko Supabase. Bez SQLite reads/writes u produkciji.

---

## DEO 1 — STA JE ZAVRSENO (radi 100% preko Supabase)

Ovi fajlovi i endpoint-i su prepisani da **direktno** citaju/pisu u Supabase preko
`data_layer` facade-a i `supabase_store.py` helper-a. Bez `sqlite3.connect` u
kriticnom putu.

### 1.1 Auth (login, sesije, lozinke)
- `routes/auth.py`
  - `POST /api/auth/login` — cita user-a iz Supabase, proverava lockout, TOTP, azurira last_login_country
  - `POST /api/auth/change_password` — pise novi password hash direktno u Supabase
  - `POST /api/auth/logout`, `POST /api/auth/logout_all`
  - `GET /api/auth/me` — cita profil iz Supabase
  - `POST /api/auth/signature` — pise potpis u Supabase
  - `POST /api/auth/totp/*` — TOTP setup flow
- `magic_link.py` — JTI single-use registry u Supabase (portal single-click sign-in)
- `database.py::seed_admin_supabase()` — pri boot-u kreira admin nalog u Supabase ako ne postoji
- `utils.py::log_audit` — audit log ide direktno u Supabase `audit_logs`
- `utils.py::bump_user_token_version` / `get_user_token_version` — Supabase

### 1.2 Users CRUD
- `routes/users.py` — list/create/update/delete admin usera
- `routes/supabase_admin.py` `GET/PATCH /api/users/me` — self-profile + notif_prefs

### 1.3 Entity CRUD (glavni CRM podaci)
- `routes/data.py`
  - `GET /api/data/<key>` — partners/products/deals/offers/demands/shared_documents + settings
  - `POST /api/item/<key>` — save single entity + settings
  - `DELETE /api/item/<key>/<id>` — delete + cascade
  - `POST /api/data/<key>` — bulk save
  - `GET /api/partners/<id>/risk-score`
  - `GET /api/dashboard/insights`

### 1.4 Security Center
- `routes/security_center.py`
  - `GET /api/security/sessions`, revoke session
  - `GET /api/security/login-history`
  - `GET /api/security/known-ips`, forget IP
  - `GET /api/security/trusted-devices`, revoke device
  - Password policy get/set (`security_policy` u settings)
  - `POST /api/security/magic-link`, `GET /login/magic`
  - `POST /api/security/lockout/status`
  - Admin: force-reset, unlock, break-glass
  - Helpers: `_create_session_row`, `touch_session`, `is_session_revoked`,
    `record_login_ip`, `send_new_ip_alert`, `check_password_reuse`, `add_password_history`

### 1.5 Activity Feed
- `routes/activity_feed.py`
  - `GET /api/activity/recent`, `GET /api/activity/mine`

### 1.6 Inventory (per-partner stock)
- `routes/inventory.py`
  - `GET /api/partners/<id>/inventory`
  - `POST /api/partners/<id>/inventory/movements`
  - `GET /api/partners/<id>/inventory/movements`

### 1.7 Notes, deal docs, low-stock, audit CSV, XLSX
- `routes/entities_extras.py`
  - `GET/POST /api/notes/<type>/<id>`, delete/pin
  - `GET/POST /api/deals/<id>/docs`, detach
  - `GET /api/inventory/low-stock`
  - `GET /api/audit/export.csv`
  - `GET /api/offers/<id>/export.xlsx`

### 1.8 Saved filters + user tasks
- `routes/saved_filters.py` — sve
- `routes/user_tasks.py` — sve

### 1.9 Firewall
- `routes/firewall.py` — status, blacklist/whitelist mgmt, settings, unblock

### 1.10 V23 admin extras
- `routes/v23_extras.py`
  - Bulk actions, custom fields (sa dupe check), API keys, outbound webhooks
  - `emit_event` + `_deliver_webhook` u Supabase

### 1.11 Reports (metadata)
- `routes/reports.py`
  - list/get/create/delete direktno iz Supabase
  - `/api/reports/<id>/run` — vraca 501 sa uputstvom za Supabase RPC funkciju

### 1.12 Portal admin (dva endpoint-a)
- `routes/portal/auth_supabase.py`
  - `POST /api/portal/admin/send-portal-invite/<partner_id>` — cita partnera iz Supabase
  - `POST /api/portal/admin/set-partner-password/<partner_id>` — cita partnera iz Supabase

### 1.13 Infrastruktura
- `supabase_store.py` — jedinstveni CRUD layer nad Supabase
  - User: `get_user_by_username`, `get_user_by_id`, `upsert_user`, `update_user_password`,
    `bump_token_version`, `set_locked_until`, `seed_admin_if_empty`
  - Entity: `list_entities`, `get_entity`, `upsert_entity`, `delete_entity`
    (sa camelCase→snake_case alias `_TABLE_ALIAS` za `recurringExpenses`→`recurring_expenses`)
  - Settings: `get_setting`, `set_setting`, `delete_setting`
  - Magic-link: `is_jti_used`, `mark_jti_used`
  - Audit: `append_audit`
- `data_layer/__init__.py` — facade sa REST/Postgres/Mock backend-ima
- `data_layer/_mock.py` — in-memory MockBackend za CI/testove (DB_BACKEND=mock)
- `data_layer/_rest.py` — Supabase HTTPS backend (production)
- `schemas/supabase_v23_1.sql` — SVE tabele definisane (idempotentno preko IF NOT EXISTS)

### 1.14 Testovi
- `tests/test_v23_1.py` — 53 testa, svi green sa DB_BACKEND=mock
- `tests/test_backend.py` — 85 testova, svi green sa DB_BACKEND=mock

---

## DEO 2 — STA JOS TREBA DA SE URADI (sledeci krug)

Ovo su fajlovi koji jos uvek imaju `sqlite3.connect` pozive. Aplikacija radi
**bez pucanja** (SQLite se in-memory kreira pri boot-u, fajlovi su isprazni ali
kod nastavlja da radi), ali funkcionalnosti u ovim modulima **ne persistuju
podatke posle Render redeploy-a** dok se ne migriraju.

### 2.1 KRITICNO — Portal frontend (klijenti)
**Fajl:** `routes/portal/actions.py` (55 sqlite3.connect poziva)

Sadrzi ceo portal client flow — sto klijent vidi/radi na portalu:
- KYC form submit + review od strane admin-a
- Portal partner profile update (kontakt, adresa, bank data)
- RFQ (request for quote) — klijent salje potraznju
- Order confirmation — klijent prihvata ponudu
- Portal document library — klijent preuzima svoje dokumente
- Shared documents — dvosmerno deljenje dokumenata izmedju CRM i portala
- Portal notifikacije (novi RFQ za admin, offer response za portal, itd.)

**Sve tabele koje koristi vec postoje u `supabase_v23_1.sql`:**
`kyc_submissions`, `portal_products`, `shared_documents`, `partners`,
`offers`, `deals`, `demands`, `entity_notes`

**Sta treba:** migrirati sve SQLite pozive u `data_layer.*` + `supabase_store.*`,
analogno kako je radjeno u `routes/data.py` i `routes/entities_extras.py`.

**Rizik ako se ne uradi:** klijent nakon Render redeploy-a nema KYC podataka,
ne moze da submit RFQ, nema pristup dokumentima.

**Estimate:** 3-4 sata rada.

---

### 2.2 KRITICNO — Portal auth
**Fajl:** `routes/portal/auth.py` (6 sqlite3.connect poziva)

- Portal OTP send + verify (email-based portal login)
- Portal session establish + kill switch
- Portal token invalidation (kada admin revoke-uje pristup partneru)

**Tabele:** `partners` (portal_token, otp_secret), `magic_link_used_jti`
— sve u Supabase schema.

**Sta treba:** migrirati OTP flow (SELECT partner by portal_token,
UPDATE partner sa otp_hash, verify OTP protiv Supabase reda).

**Rizik ako se ne uradi:** portal login ne radi posle redeploy-a.

**Estimate:** 1 sat.

---

### 2.3 KRITICNO — Portal data (klijent view)
**Fajl:** `routes/portal/data.py` (7 sqlite3.connect poziva)

- Klijent lista svoje ponude, dilove, potraznje
- Klijent preuzima svoj offer PDF
- Klijent gleda svoj partner profile

**Tabele:** `offers`, `deals`, `demands`, `partners`, `shared_documents`,
`portal_products`

**Sta treba:** identican pattern kao CRM `get_data` — data_layer.select
filtrirano po `partner_id`/`buyer_id`.

**Rizik:** klijent posle redeploy-a vidi prazan portal.

**Estimate:** 1 sat.

---

### 2.4 SREDNJE — Portal blueprint init + status
**Fajlovi:**
- `routes/portal/__init__.py` (4 poziva)
- `routes/portal/auth_supabase.py` (1 preostali)

Uglavnom helper-i (session heartbeat, pending counts, admin badge counters).

**Estimate:** 30 min.

---

### 2.5 SREDNJE — V23 Admin (permission matrix, document register)
**Fajl:** `routes/v23_admin.py` (11 sqlite3.connect poziva)

- Granular permissions matrix (`GET/POST /api/v23/permissions/matrix`)
- Portal permissions (`/api/v23/portal-permissions/*`)
- KYC admin approve/reject (`/api/v23/kyc/*`)
- Offer → Invoice / Proforma conversion (`/api/v23/convert/*`)
- Document register + revisions (V1/V2/V3 numbering)

**Tabele:** `users` (permissions), `partners` (kyc_approved, portal_level),
`offers`, `invoices`, `proformas`, `document_register`, `document_revisions`
— sve postoji u schemi.

**Rizik:** conversion wizard i document register ne rade nakon redeploy-a.

**Estimate:** 2-3 sata (najsloženija logika je registar sa V1/V2 numeracijom).

---

### 2.6 SREDNJE — System admin (health, backup, errors)
**Fajl:** `routes/system.py` (13 sqlite3.connect poziva)

- `/api/admin/health` — vraca DB size, WAL size, itd. — treba da vraca Supabase
  connection status umesto SQLite fajl-info
- `/api/admin/backup/*` — backup lista, restore. **Supabase ima svoj backup
  sistem (PITR + daily) — ovaj endpoint moze da postane read-only prikaz
  linkova ka Supabase Studio-u.**
- `/api/admin/errors` — error buffer. Vec je in-memory (Python list),
  SQLite je bio za persist. Moze da ostane in-memory (izbrise se posle
  restart-a, i to je OK) ili u Supabase tabelu.
- `/api/admin/mail-queue` — email queue. Ovo bi trebalo u Supabase da bi
  posle Render restart-a mail-ovi u queue-u opstali.

**Estimate:** 2 sata.

---

### 2.7 SREDNJE — Supabase admin panel
**Fajl:** `routes/supabase_admin.py` (9 sqlite3.connect poziva)

- `/api/supabase/status` — vec radi
- `/api/supabase/storage/status` — vec radi
- `/api/supabase/init-storage` — inicijalizacija bucket-a — vec radi
- `/api/supabase/import-local-db` — import iz stare SQLite baze u Supabase.
  **Ovo je legacy migracioni tool — moze da se obrise nakon sto se svi
  klijenti prebace na Supabase-only mode.**
- `/api/users/me` GET/PATCH — vec migrirano

**Estimate:** 1 sat (mahom brisanje mrtvog koda za import-local-db).

---

### 2.8 NIŽE — Utility fajlovi (background tasks + helpers)

**`utils.py`** (6 preostalih):
- 4 pozivа: `must_change_password` check u `login_required` decorator-u —
  jos uvek cita SQLite, treba u Supabase
- 1 poziv: firewall settings loader — pozvano samo pri boot-u
- 2 poziva: audit retention prune loop (background)
- 2 poziva: backup snapshot loop (background) — moze da ostane SQLite-only ili
  se izbaci jer Supabase ima sopstveni backup

**`utils_email.py`** (5 poziva):
- Email queue table (`email_queue`) — pri sledecem restart-u mail-ovi u queue-u
  se gube. Treba u Supabase tabelu.

**`utils_reliability.py`** (2 poziva):
- Circuit breaker state, retry counters — moze da ostane in-memory ili u Supabase.

**`security_ext.py`** (2 poziva):
- HIBP + hCaptcha helper-i — cita/pise cache tabelu

**`webhooks.py`** (1), `tracking.py` (1), `market_data.py` (1),
`mail_providers.py` (1), `search_index.py` (1) — sve su cache/state tabele.

**Estimate za sve:** 2-3 sata.

---

### 2.9 NIŽE — Legacy migration/reconcile tools

**Fajlovi:**
- `routes/supabase_merge.py` (2 poziva) — legacy migracioni wizard (SQLite → Supabase)
- `routes/supabase_webhook.py` (2) — Supabase Auth webhook handler
- `routes/vault.py` (2) — vault/secrets management
- `routes/comms.py` (2) — SMTP config test
- `routes/audit.py` (2) — audit read endpoints (delimicno vec migrirano)
- `routes/documents.py` (5), `routes/documents_register.py` (1) — document register read
- `routes/verify_public.py` (1) — QR verify public endpoint

**Estimate:** 3-4 sata.

---

### 2.10 NIŽE — PDF generator
**Fajl:** `pdf_generator.py` (6 poziva)

Cita company/settings/partner podatke za renderovanje PDF-ova.

**Sta treba:** zameniti sa `supabase_store.get_setting('company')` i
`supabase_store.get_entity('partners', pid)`.

**Estimate:** 30 min.

---

### 2.11 NIŽE — Init/boot
**Fajl:** `database.py` (3 preostalih poziva)

- `init_db()` — kreira SQLite tabele. **Ovo moze da se skroz obrise u
  Supabase-only mode** jer ne treba nam SQLite uopste. Ali za sada je bezopasno
  — kreira prazan SQLite u /tmp koji nista ne cita.

**`app.py`** (1 poziv) — DB bootstrap block (PRAGMA settings za sve SQLite baze).
Moze da se obrise.

**Estimate:** 30 min.

---

### 2.12 CISCENJE (opciono ali preporuceno)

Kada je sve migrirano:

1. **Obrisati** `db.py` (SQLite wrapper), `config.py::DB_FILE` konstantu,
   `config.py::PORTAL_DB_FILE`, `config.py::AUDIT_DB_FILE`
2. **Obrisati** stari `data_layer/_pg.py` (direktan Postgres — nije potreban
   jer REST radi svuda)
3. **Obrisati** `scripts/import_local_db_to_supabase.py`,
   `scripts/migrate_data_to_supabase.py` — legacy migracioni tool-ovi
4. **Obrisati** `routes/supabase_merge.py` — legacy merge wizard, zamenjen
   je time sto sve ide direktno
5. **Obrisati** `PORTAL_DB_FILE` i `AUDIT_DB_FILE` reference svuda
6. **Obrisati** SQLite-specific PRAGMA calls u `app.py` DB bootstrap block-u
7. **Obrisati** `INSTANCE_DIR`/`SECRET_KEY_FILE` file writes iz `config.py`
   ako je SECRET_KEY u env-u (koji jeste u vasoj produkciji)

---

## DEO 3 — PRIORITETI ZA SLEDECI KRUG

**Krug 1 (mora — bez ovih klijent portal ne radi na Renderu):**
1. ✅ `routes/portal/actions.py` — KYC, RFQ, orders (2.1) — 3-4h
2. ✅ `routes/portal/auth.py` — OTP login flow (2.2) — 1h
3. ✅ `routes/portal/data.py` — klijent view (2.3) — 1h

**Krug 2 (treba — admin funkcije bez ovih ne persistuju):**
4. ✅ `routes/v23_admin.py` — permissions matrix + document register (2.5) — 2-3h
5. ✅ `routes/system.py::mail_queue` — email queue u Supabase (2.6, deo) — 1h
6. ✅ `utils_email.py::email_queue` — dopuna prethodnog — 1h
7. ✅ `utils.py::must_change_password` check (2.8, deo) — 30 min

**Krug 3 (moze — sve ostalo):**
8. ✅ `pdf_generator.py` (2.10) — 30 min
9. ✅ `routes/documents.py`, `routes/audit.py`, `routes/vault.py` — 1h
10. ✅ `routes/comms.py`, `routes/supabase_webhook.py` — 30 min
11. ✅ Ostatak utility fajlova (2.8) — 2h

**Krug 4 (finalno ciscenje — kada je sve gore zavrseno):**
12. ✅ Obrisati `db.py`, `PORTAL_DB_FILE`, `AUDIT_DB_FILE` konstante
13. ✅ Obrisati legacy migracioni skriptove
14. ✅ Obrisati `data_layer/_pg.py`
15. ✅ Full smoke test + performance profil (Supabase ima RTT tacno)

**Total estimate za sve preostalo:** 12-15 sati fokusiranog rada.

---

## DEO 4 — SUPABASE SETUP CHECKLIST (za produkciju)

Da bi sve ovo sto smo migrirali radilo, potrebno je:

### 4.1 Env vars (na Renderu)
Vec postavljeni u vasem env-u, samo potvrdi:
- `SUPABASE_URL=https://gceaznutofvqbuyypjlh.supabase.co`
- `SUPABASE_SERVICE_ROLE_KEY=<secret>`
- `SUPABASE_ANON_KEY=<secret>`
- `SUPABASE_JWT_SECRET=<secret>`
- `DB_BACKEND=rest`
- `SECRET_KEY=<stabilan random string>`
- `ENCRYPTION_KEY=<stabilan Fernet key>`
- `ADMIN_USERNAME=vladimir`, `ADMIN_PASSWORD=Vladimir2026`
- `SESSION_COOKIE_SECURE=true`
- `USE_SUPABASE_STORAGE=true`
- `APP_BASE_URL=https://crmaspidus.onrender.com`
- `PORTAL_BASE_URL=https://crmaspidus.onrender.com/portal/login`

### 4.2 Supabase Schema
U Supabase Studio → SQL Editor pokreni **jednom**:
```sql
-- Sadrzaj: schemas/supabase_v23_1.sql
-- Idempotentno je (CREATE TABLE IF NOT EXISTS + ALTER TABLE ADD COLUMN IF NOT EXISTS)
```

### 4.3 Supabase Storage bucket-i
Kroz aplikaciju (Admin → Supabase → Init Storage) ili rucno u Supabase Studio:
- `partner-docs` (private)
- `offer-pdfs` (private)
- `portal-uploads` (private)
- `backups` (private)

### 4.4 Supabase RLS policies (SLEDECI KRUG)
Trenutno service_role_key bypass-uje RLS. To je bezbedno jer key nikad ne ide
na frontend. Ali za dodatnu bezbednost, preporuceno je definisati RLS policies
u Supabase-u tako da:
- `partners` — samo taj partner moze da cita/pise (auth_user_id = current_user_id)
- `offers` — samo customer_id ili admin
- `users` — samo id = current_user_id ili admin
- itd.

### 4.5 Postgres RPC funkcije (za reports.py `/run`)
Ako zelis da custom SQL izvestaji rade, u Supabase → SQL Editor:
```sql
CREATE OR REPLACE FUNCTION run_report(report_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
  sql_text text;
  result jsonb;
BEGIN
  SELECT sql_query INTO sql_text FROM custom_reports WHERE id = report_id;
  IF sql_text IS NULL THEN
    RETURN '[]'::jsonb;
  END IF;
  -- OBAVEZNO validirati sql_text pre EXECUTE-a
  EXECUTE 'SELECT jsonb_agg(row_to_json(t)) FROM (' || sql_text || ') t' INTO result;
  RETURN COALESCE(result, '[]'::jsonb);
END $$;
```

---

## DEO 5 — TRENUTNI STANJE APLIKACIJE

**Sto RADI 100% preko Supabase (nista se ne gubi posle Render redeploy-a):**
- Prijava admin i internih korisnika
- Kreiranje/citanje/izmena/brisanje partnera, proizvoda, ponuda, dilova, potraznji, deljenih dokumenata
- Sve settings (company, comms, firewall, security policy)
- Session management (login history, active sessions, revoke)
- Password policy + password history + magic-link + break-glass
- IP tracking (known_ips, trusted_devices)
- Activity feed
- Per-partner inventory + movements
- Entity notes + deal documents
- User tasks + saved filters
- Firewall admin (blacklist/whitelist)
- Bulk actions + custom fields + API keys + outbound webhooks
- Audit log
- User self-profile (`/api/users/me`)
- Dashboard insights + partner risk-score

**Sto **možda ne persistuje** posle Render redeploy-a (radi ali u SQLite):**
- Portal klijent flow (KYC, RFQ, orders) — Krug 1 iznad
- Portal login (OTP) — Krug 1
- Portal client view (offers/deals list) — Krug 1
- Document register + Offer→Invoice/Proforma conversion — Krug 2
- Email queue (poslati mail-ovi u redu cekanja) — Krug 2
- Must-change-password gate — Krug 2 (nije bloker, samo trigger)
- Vault/secrets — Krug 3
- PDF generator (cita company info iz SQLite) — Krug 3

---

## KRAJNJI CILJ

Kada zavrsimo sva 4 kruga, `grep -rE "sqlite3.connect|import sqlite3" routes/ utils.py database.py app.py` treba da vraca **nula rezultata**. Tada se moze:

1. Obrisati `db.py`, `INSTANCE_DIR`, sve SQLite reference iz `config.py`
2. Postaviti `DATA_DIR=/tmp` bez straha (nista se u njemu ne cuva)
3. Ukloniti persistent disk sa Rendera (jeftinije + brze)
4. Aplikacija radi **isto** u development-u i produkciji preko istog Supabase-a
5. Bilo koja instanca aplikacije (Render, PythonAnywhere, VPS, local dev) vidi
   iste podatke jer sve ide kroz jedan Supabase projekat.
