# Aspidus CRM — Produkcijski vodič za admina

Ovo je vodič **za tebe, korisnika** (ne za programera). Objašnjava kako da
pokreneš, kontrolišeš i održavaš aplikaciju u svakodnevnom radu.

---

## 🚀 Kako da migriraš SVE podatke na Supabase (jedan klik)

Kada želiš da preselim SQLite bazu (koja je na tvom PA serveru) u
Supabase Postgres (cloud, brz, skalabilan) — sve iz browser-a:

### 1) Uloguj se u CRM kao admin

### 2) Otvori URL: `https://aspidus.pythonanywhere.com/admin/supabase`

Dobićeš stranicu **Supabase Migration Control** sa:
- **Feature Flags** — gde vidiš da li je Supabase Auth, DB, Storage uključen
- **SQLite (source)** — broj redova u tvojoj lokalnoj bazi (šta imaš SADA)
- **Supabase Postgres (target)** — broj redova u cloud-u (šta je već prebačeno)
- **Migration Actions** — dugmad

### 3) Prvi korak: **🧪 Dry-Run**

Klikni dugme. Pojaviće se listing: koliko partnera, proizvoda, deala,
KYC podnesaka ima u SQLite bazi. **Ne piše ništa** u Supabase — samo broji.

Ako brojevi izgledaju kako treba (npr. 14 partnera, 31 proizvod), prelaz na sledeći.

### 4) Drugi korak: **▶ Start Full Migration**

Klikni. Backend će u pozadinskom thread-u pokrenuti pravu migraciju:
- Piše sve u Supabase Postgres redom (`partners → products → deals → ...`)
- **UPSERT** — ako je red već tamo, samo ažurira; ako nije, kreira. Sigurno se
  može pokrenuti više puta bez duplikata.
- Stranica automatski osvežava log svake 3 sekunde dok radi.
- Na kraju vidiš: `✅ MIGRACIJA USPESNA` ili listu grešaka.

**Trajanje**: 20 sekundi za manje od 100 redova. Do 5 minuta za par hiljada.

### 5) Treći korak: **Toggle DUAL_WRITE_MODE**

Klikni dugme. To znači: aplikacija sada piše **u OBE baze paralelno** (SQLite
+ Supabase). Ovo je "safety net" — ako Supabase ima problem, tvoji podaci
su i dalje u SQLite-u.

Ostavi ovako par dana. Radi sa aplikacijom normalno. Proveravaj periodično
u Table Editor Supabase Dashboard-a da li vidiš iste podatke.

### 6) Kada si potpuno siguran: **Toggle USE_SUPABASE_DB**

Klikni. Sada aplikacija **ČITA** iz Supabase-a (SQLite postaje samo backup
zbog DUAL_WRITE_MODE). Sve je brže, cloud-hosted, skalabilno.

### 7) Poslednji korak (nekoliko nedelja kasnije): **Isključi DUAL_WRITE_MODE**

Kad si potpuno siguran da Supabase radi savršeno, isključi dual-write.
SQLite baze ostaju kao istorijski arhiv, ali aplikacija ih više ne dira.

---

## 👥 Kako da onboarduješ novog klijenta (3 klika)

1. **CRM → Partners → New Partner** → popuni podatke (email je obavezan)
2. Otvori tog partnera → sekcija **B2B Portal & Compliance** →
   klikni **📧 Send Portal Invite / Reset**
3. Gotovo. Klijent dobija email sa linkom, klikne, postavi lozinku, ulazi u portal.

**Alternativa**: klikni **🔑 Set Portal Password** → uneses lozinku → javiš je
klijentu van sistema (WhatsApp, telefon). Klijent odmah može da se uloguje bez
čekanja mejla.

---

## 🚨 Šta ako nešto krene loše (Rollback)

### Portal login ne radi kod klijenata
- Otvori `/admin/supabase` → dugme **Toggle USE_SUPABASE_AUTH** → isključi
- Portal se vraća na stari OTP sistem
- Klijenti dobijaju 6-cifreni kod mejlom (kao ranije)

### Supabase baza je nedostupna
- Otvori `/admin/supabase` → **Toggle USE_SUPABASE_DB** → isključi
- Aplikacija se vraća na SQLite bazu
- Ako je DUAL_WRITE_MODE bio ON, sve novo od poslednje migracije JE u SQLite-u

### Podaci su izgubljeni ili oštećeni
- Bash konzola na PA:
```bash
cd /home/aspidus/mysite/CRM
python3.13 scripts/restore_from_fernet_backup.py --dry-run
```
- Vidiš listu dostupnih backup-a
- Pokreni sa `--confirm --only crm` da vratiš samo CRM bazu iz backup-a

---

## 🔒 Bezbednost — šta je pod haubom

Aplikacija ima sledeće nivoe zaštite (već aktivno):

- **Fernet AES-128 enkripcija** — svi osetljivi podaci u SQLite-u su šifrovani
  ključem koji NIKAD ne napušta server. Čak i ako neko ukrade `.db` fajl, ne
  može da ga pročita bez `vault.key`.
- **Session TTL + inactivity timeout** — automatski logout posle 15 min neaktivnosti
- **IP binding** — portal auth_key vezan je za IP sa kog je login prošao;
  ako se pojavi sa drugog IP-a, sesija se poništava
- **Rate limiting** — max 50 request-a/minutu po IP-u na portal, brute-force zaštita
- **CSRF token** — svaki write endpoint traži validan token
- **Kill Switch** — admin može trenutno da opozove portal pristup partnera
- **hCaptcha** — anti-bot na OTP endpoint-u (kad je konfigurisan)
- **2FA/TOTP za admina** — Google Authenticator prijava
- **Audit log** — svaka bezbednosno-relevantna akcija se beleži sa timestamp-om
  i IP-om
- **Automatski Fernet backup** — dnevni šifrovani snapshot svih baza u
  `backups/` folder, čuva se 14 dana

---

## 👥 Konkurentnost — više korisnika istovremeno

### SQLite (trenutno)
- **WAL mode** aktivan — čitanja NE blokiraju pisanja
- **busy_timeout=30s** — piše čekaju do 30s da dobiju lock (retry-with-retry)
- **Praktičan limit**: 5-10 istovremenih aktivnih korisnika. Preko toga
  počinjaš da vidiš "database is locked" errore povremeno.

### Supabase Postgres (posle migracije)
- **Nema praktičnog limita** — hiljade istovremenih korisnika bez problema
- Free tier: do 500MB storage, 2GB bandwidth/mesec, 50 concurrent connections
- Pro tier ($25/mesec): 8GB storage, 250GB bandwidth, 400 concurrent
- Za tvoj B2B portal sa desetak-dvadeset klijenata, Free tier je **više
  nego dovoljan** godinama.

### Portal session storage (napomena)
Aktivne portal sesije se čuvaju u memoriji Flask procesa. Ovo znači:
- Na **PA Free** (1 worker) — radi savršeno
- Na **PA Hacker+ ili više worker-a** — sesije se ne dele između worker-a,
  klijent može biti "nasumično" izbačen. Kada nadogradiš, javi mi da
  prebacim session store u Supabase (redis-slično, u posebnu tabelu).

---

## 📞 Kada da me pozoveš

Prati ovaj vodič. Ako:
- Dry-run pokaže neočekivane brojeve
- Migracija bacа više od 5 grešaka
- Klijent prijavi da ne može da uđe
- Rate limit počne da bije klijente

**Uvek ima rollback dugme.** Klikni, vrati na staro stanje, javi mi šta je bilo.
Nikad ne ostaneš u haosu.
