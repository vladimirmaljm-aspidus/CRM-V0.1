# Portal Auth — priručnik za admina

Kako da onboarduješ, upravljaš i rešavaš probleme oko portal pristupa klijenata,
sa **Supabase Auth-om** kao produkcionim sistemom.

---

## 1) Novi klijent — od nule do login-a (3 klika)

1. U CRM-u dodaj novog partnera (Partners → New Partner). **Bitno**: unesi
   ispravan email u `contact.email` polju.
2. Otvori Partner detail → sekcija **B2B Portal & Compliance** → klik dugme
   **📧 Send Portal Invite / Reset**.
3. Klijent dobija mail sa linkom "Set your password". Klik → forma za novu
   lozinku → auto-login.

**Alternativno — postavi lozinku ručno bez čekanja mail-a:**
- Isto mesto → **🔑 Set Portal Password** → unesi lozinku (min. 8 karaktera)
- Klijentu javi lozinku van sistema (telefon, sigurni chat)
- Klijent može odmah da se uloguje sa email + tom lozinkom

Preporučeno za VIP klijente ili kad SMTP ne radi.

---

## 2) Postojeći klijent — svakodnevni login

1. Otvori `https://<tvoj-domen>/portal/login`
2. **Password tab** → email + lozinka → **Sign In**
3. U portalu.

Ako klijent zaboravi lozinku:
- Klik **"Forgot password?"** → unese email → dobija reset mail → nova lozinka → login

Ili preferira jednokratni ulaz:
- **Magic Link tab** → email → mail sa direktnim linkom → jedan klik = login (bez lozinke)

---

## 3) Klijent hoće da promeni lozinku (ulogovan)

Klijent u portalu:
1. Klik **Profile** tab
2. Na dnu ekrana sekcija **🔑 Change Password**
3. Trenutna lozinka + Nova lozinka → **Change Password**
4. Odmah aktivno; stara više ne radi.

---

## 4) Kompletno oduzimanje pristupa (Kill Switch)

Isto mesto u Partner detail-u → **🔒 Revoke Portal Access**.

Efekti:
- Sve aktivne portal sesije se odmah gase
- Klijent više ne može da se uloguje (blokirano na Kill Switch-u,
  bez obzira da li zna lozinku)
- Reaktivacija: isto dugme, zeleno **✅ Reactivate**

---

## 5) Bulk onboarding — svi postojeći partneri odjednom

Iz Bash konzole na serveru:
```bash
cd /home/aspidus/mysite/CRM

# Prvo dry-run da vidiš listu koga bi obradio
python3.13 scripts/migrate_partners_to_supabase.py --dry-run

# Pravi run — kreira Auth naloge + šalje reset mail-ove
python3.13 scripts/migrate_partners_to_supabase.py --send-emails --only-active
```

**Napomena**: Supabase built-in email service je limitovan (~4 mail/h). Za
produkciju konfiguriši custom SMTP u Supabase Dashboard → Project Settings →
Auth → SMTP Settings (možeš koristiti isti SMTP koji je već povezan u CRM-u).

---

## 6) Rollback na legacy OTP flow (ako nešto krene loše)

U `.env` postavi:
```
USE_SUPABASE_AUTH=false
```
Reload web app. Legacy 6-cifreni OTP kod flow se automatski vraća — sve stare
portal sesije rade kao pre.

---

## 7) Endpoint mapa (za tehničku referencu)

| Endpoint | Metod | Ko poziva | Šta radi |
|----------|-------|-----------|----------|
| `/api/portal/auth/supabase/signin-password` | POST | portal login | email+password → auth_key |
| `/api/portal/auth/supabase/set-password` | POST | posle klika na reset mail | verify JWT + set new pwd + login |
| `/api/portal/auth/supabase/send-magic-link` | POST | portal login → Magic Link tab | šalje magic link |
| `/api/portal/auth/supabase/send-reset` | POST | portal login → Forgot? | šalje reset mail |
| `/api/portal/auth/supabase/exchange` | POST | posle klika na magic link | JWT → auth_key |
| `/api/portal/user/change-password` | POST | ulogovan klijent u portalu | menja svoju lozinku |
| `/api/portal/admin/send-portal-invite/<id>` | POST | admin dugme | šalje invite/reset mail |
| `/api/portal/admin/set-partner-password/<id>` | POST | admin dugme | direktno postavi lozinku |
| `/api/portal/access/<id>` | POST | admin dugme | Kill Switch (aktivacija/opoziv) |

Svaki endpoint poštuje **Kill Switch** (revoke) i **GPS gate** (osim za Premium
klijente), plus rate limit po IP-u i audit log.

---

## 8) Šta se dešava iza scene

- Kada admin klikne "Send Portal Invite":
  1. Backend proverava da li Supabase Auth user postoji za taj email → ako
     ne, kreira ga (email_confirm=true, bez lozinke)
  2. Poziva Supabase `/auth/v1/recover` sa `redirect_to=<portal_login_url>`
  3. Supabase šalje mail preko konfigurisanog SMTP-a
  4. Loguje event u audit log
- Kada klijent klikne link u mail-u:
  1. Supabase verifikuje token, redirects na portal_login sa
     `#access_token=...&type=recovery` u hash-u
  2. Naš JavaScript detektuje `type=recovery` → prikaže "Set new password" formu
  3. Klijent submit-uje → POST na `/api/portal/auth/supabase/set-password`
  4. Backend offline verifikuje JWT (HS256 ili ES256 preko JWKS)
  5. Zove Supabase admin API da postavi lozinku
  6. Kreira portal_auth_session, vraća auth_key
  7. Frontend redirects na `/portal/<token>` — klijent je unutra

Sve komunikacije sa Supabase strane su preko REST API-ja (bez ijedne
klijentske CDN zavisnosti). Radi na PythonAnywhere Free planu.
