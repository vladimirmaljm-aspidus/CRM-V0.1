# RENDER DEPLOYMENT — kompletno uputstvo za produkciju

Ovaj vodic pretpostavlja da imate Render Web Service + Supabase projekat.
Sve komande su idempotentne — bezbedno ih je pokrenuti vise puta.

---

## 1) Supabase — pokretanje sheme

U Supabase Studio → SQL Editor, izvrsite (redom):

```sql
-- Ceo osnovni set tabela (partners, offers, deals, users sa password
-- kolonom, settings, sessions, itd.)
-- SADRZAJ: schemas/supabase_v23_1.sql
```

Napomena: **users.password** kolona je **obavezna** u V23.4 — bez nje
prijava nece raditi posle Render redeploy-a (jer se admin ne moze povuci
iz Supabase-a nazad u SQLite). Fajl `schemas/supabase_v23_1.sql` u V23.4
sadrzi `ALTER TABLE users ADD COLUMN IF NOT EXISTS password TEXT;` pa
mozete ga izvrsiti bez straha da cete duplirati postojece podatke.

Kada zavrsite, u Supabase Studio → Table Editor → `users` proverite da
postoji red za `vladimir` (username). Ako ga nema, prvo pokrenite app na
Renderu jednom (seed-uje admin u SQLite pa ga mirror-uje u Supabase).

---

## 2) Render — env varijable

U Render dashboard → Your Service → Environment, postavite (identicno
kao na screenshot-u sto ste poslali):

| Kljuc                          | Vrednost                                                    |
|--------------------------------|-------------------------------------------------------------|
| `ADMIN_USERNAME`               | `vladimir`                                                  |
| `ADMIN_PASSWORD`               | `Vladimir2026`                                              |
| `APP_BASE_URL`                 | `https://aspiduscrm.onrender.com`                           |
| `PORTAL_BASE_URL`              | `https://aspiduscrm.onrender.com/portal/login`              |
| `DATA_DIR`                     | `/opt/render/project/src/data`  (**NE /tmp**, vidi napomenu)|
| `DB_BACKEND`                   | `rest`                                                      |
| `SUPABASE_URL`                 | `https://<projekat>.supabase.co`                            |
| `SUPABASE_ANON_KEY`            | (iz Supabase → Settings → API)                              |
| `SUPABASE_SERVICE_ROLE_KEY`    | (iz Supabase → Settings → API — **secret**)                 |
| `SUPABASE_JWT_SECRET`          | (iz Supabase → Settings → API → JWT Secret)                 |
| `SUPABASE_DB_URL`              | (opciono; koristi se samo za direct-Postgres backup skripte) |
| `USE_SUPABASE_AUTH`            | `true` (opciono — trenutno se ne koristi za CRM login)      |
| `USE_SUPABASE_STORAGE`         | `true`                                                      |
| `SESSION_COOKIE_SECURE`        | `true`                                                      |
| `SECRET_KEY`                   | (bilo koji dugacak nasumican string — mora ostati stabilan!) |
| `ENCRYPTION_KEY`               | (Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) |
| `WEBHOOK_SECRET`               | (bilo koji nasumican string za outbound webhook potpise)    |
| `PYTHON_VERSION`               | `3.11.6`                                                    |

### **KRITICNO — `DATA_DIR`**

**NE stavljajte `/tmp`** — to se brise pri svakom kontejnerskom
restart-u/deploy-u pa gubite SQLite. Umesto toga:

**Opcija A (preporucena) — Render Persistent Disk:**
1. Render → Your Service → Disks → Add Disk
2. Name: `aspidus-data`, Mount path: `/opt/render/project/src/data`, Size: 1 GB
3. Env `DATA_DIR=/opt/render/project/src/data`
4. Vec je pripremljeno u `render.yaml` u repo-u.

**Opcija B — bez diska (samo Supabase-first):**
1. `DATA_DIR=/tmp` je OK jer sada V23.3+ ima **Supabase read-fallback**
   i V23.4 ima **login recovery iz Supabase**. Podaci nece "nestati" jer
   svaki save ide i u Supabase, a read se automatski povlaci iz Supabase
   kad je SQLite prazan.
2. Ali `SECRET_KEY` i `ENCRYPTION_KEY` **moraju** biti postavljeni u env-u
   — bez njih se pri svakom deploy-u generisu novi kljucevi, sto pravi
   dve nezeljene posledice:
   - Sesije se invalidiraju (SECRET_KEY)
   - Enkriptovani podaci u Supabase-u vise ne mogu da se procitaju
     (ENCRYPTION_KEY)

---

## 3) Render — Build & Start komande

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn app:app --workers 2 --timeout 120 --bind 0.0.0.0:$PORT`

Vec je definisano u `Procfile` i `render.yaml`.

---

## 4) Provera prijave admin-a

Posle prvog deploy-a:

1. Otvorite `https://aspiduscrm.onrender.com`
2. Prijavite se sa `vladimir` / `Vladimir2026`
3. U Render → Logs proverite da postoji poruka:
   `SEED: kreiran početni administrator 'vladimir' (lozinka iz ADMIN_PASSWORD env-a).`

**Ako login ne uspe:**

- Otvorite Render → Logs i potrazite `LOGIN:` linije.
  - `LOGIN: unknown username 'vladimir' from IP …` → admin nije seed-ovan.
    Proverite da `ADMIN_USERNAME` i `ADMIN_PASSWORD` postoje u env-u i
    redeploy-ujte.
  - `LOGIN: wrong password for 'vladimir' …` → password u env-u se ne
    podudara sa hash-om u bazi. Ovo se moze desiti ako je stari admin
    ostao u Supabase-u sa starim password-om. Fix: u Supabase Studio →
    Table Editor → `users` obrisite red za `vladimir` pa redeploy-ujte
    (novi seed ce zapisati taj isti user sa novim hash-om).
  - `LOGIN: user vladimir restored from Supabase after SQLite miss` →
    V23.4 fallback je odradio posao, prijava treba da radi. Ako i
    dalje ne radi, proverite da `SECRET_KEY` postoji u env-u i da nije
    promenjen izmedju deploy-a.

**Da bi prijava radila i nakon Render redeploy-a bez persistent diska:**

- Osigurajte da su `SUPABASE_URL` i `SUPABASE_SERVICE_ROLE_KEY` u env-u.
- Osigurajte da je `users.password` kolona kreirana u Supabase (`ALTER
  TABLE users ADD COLUMN IF NOT EXISTS password TEXT;`).
- Prilikom prvog uspesnog login-a, V23.4 upisuje admin-a u Supabase sa
  password hash-om. Na sledecem deploy-u, kada je SQLite prazan,
  `seed_admin_if_empty` prvo pokusa recovery iz Supabase i restore-uje
  postojece kredencijale umesto da napravi novog admin-a.

---

## 5) Provera da citanje iz Supabase-a radi

Nakon prijave:

1. U CRM-u → Admin → Supabase Merge (`/admin/supabase/merge`) proverite:
   - `Supabase reachable: yes` (zelena)
   - `mirror_test_ok: true`
2. Ako imate stare podatke u Supabase-u a novi Render container ne
   pokazuje nista:
   - Otvorite bilo koji modul (Partneri, Ponude, Dilovi)
   - Prva request-a: pogledajte Render Logs — trebalo bi da vidite
     `Read-fallback: rehydrating N rows for partners from Supabase`
   - Sledeci request-i idu iz backfill-ovanog SQLite (brzo, bez
     dodatnog Supabase poziva)

---

## 6) Sigurnosne preporuke

- **NE cuvajte** `.env` fajl u git-u (vec je u `.gitignore`).
- **NIKAD** ne stavljajte `SUPABASE_SERVICE_ROLE_KEY` u frontend kod —
  on je server-only (bypass RLS).
- Nakon prve prijave, **odmah** promenite `ADMIN_PASSWORD` u nesto
  jaco (Security > Password u aplikaciji) — trenutna `Vladimir2026` ne
  zadovoljava is_strong_password() proveru na svakom sledecem change-u.
- **Ukljucite 2FA** za admin nalog (Security > Two-Factor).

---

## 7) Sta ako zelim potpuno bez SQLite-a

Trenutna arhitektura je **SQLite kes + Supabase primary**:
- Save → SQLite (transakciono) → mirror u Supabase (best-effort)
- Read → SQLite → ako je prazno, povuci iz Supabase i backfill-uj SQLite
- Login → SQLite → ako korisnik ne postoji, povuci iz Supabase

Ova arhitektura je testirana, radi na Renderu bez persistent diska, i
resava izvorni problem koji ste opisali ("podaci ne postoje posle
redeploy-a"). Puna migracija na "Supabase-only bez ijedne SQLite linije"
je visednevni refaktoring koji ne bih preporucio u produkciji dok se
citav app ne prepise (svako mesto koje otvara `sqlite3.connect(...)`
mora da se zameni sa data_layer pozivom + treba resiti transakcije,
lock-ove, migracije, i test coverage). Ako to zelite, otvorite zaseban
Github issue i planiramo fazu po fazu.

---

## 8) Testiranje lokalno

```bash
export DATA_DIR=/tmp/aspidus-test
export ADMIN_USERNAME=vladimir
export ADMIN_PASSWORD=Vladimir2026
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(64))")
export ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export FLASK_ENV=testing
export SESSION_COOKIE_SECURE=false

# Testovi:
python -m tests.test_v23_1     # 53/53 ocekivano
python -m tests.test_backend   # 85/85 ocekivano

# App:
gunicorn app:app --workers 2 --bind 0.0.0.0:5000
# Otvorite http://localhost:5000 i prijavite se
```
