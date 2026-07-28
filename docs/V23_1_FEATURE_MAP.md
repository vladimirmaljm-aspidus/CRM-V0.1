# V23.1 — Where each feature lives

Kompletno mapiranje: šta od 8 obećanih tačaka gde se otvara, koji API pokreće
šta, i koja tabela u SQLite/Supabase drži podatke. Koristi kao "cheat sheet"
kada tražiš gde je nešto podešeno.

---

## 1. Deep DB check, migracija i Supabase pokrivenost

### Admin UI
| Gde | Šta |
|---|---|
| `/admin/supabase` | **Operations Center** — glavni admin panel za Supabase migraciju, dry-run, sync-back, reconcile |
| `/admin/supabase/merge` | **Merge Wizard** — pregled lokalni vs Supabase, preview reda pre push-a, per-row error report |
| `/admin/health` | Health check kroz sve provider-e |

### Skripte
| Fajl | Namena |
|---|---|
| `scripts/audit_supabase_coverage.py` | Skenira izvorni kod i prijavljuje potencijalne lokalne pisače (out/supabase_coverage_report.md) |
| `scripts/import_local_db_to_supabase.py` | Bezbedan `.db → Supabase` uvoz (`--dry-run` / `--confirm`, per-row error tolerance) |
| `scripts/migrate_data_to_supabase.py` | Legacy migracija sa transformima po tabeli (JSON → normalizovane kolone) |
| `scripts/reconcile_sqlite_supabase.py` | Poredi row-count SQLite vs Supabase; opciono sync-back |

### Korisničke preference u bazi
- `/api/users/me` PATCH → piše u `users` tabelu (full_name, email, phone, notif_prefs) + **dual-write** u Supabase (`data_layer.upsert('users', ...)`).
- `user_sessions`, `known_ips`, `trusted_devices`, `magic_login_tokens`, `password_history` — sve u DB, sa Supabase mirror na kreaciju sesije.
- Layout customization (accent color, font size, sidebar width) čuvaju se u `notif_prefs.display` polju istog PATCH-a.

---

## 2. UI/UX reorganizacija

### Top bar (horizontalni)
- **Fajl**: `static/js/core/topbar.js`
- Mount: automatski, sedi na vrhu index.html-a
- Sadrži: hamburger (mobile), breadcrumbs slot, global search, "+ New offer", notifikacije, user menu sa svim linkovima (Security, Preferences, Register, admin skup)

### Left sidebar (vertikalni)
- **Fajl**: `static/js/core/ui.js`, funkcija `_renderNav`
- Katalog: `fullNavigationItems` niz — dodaj tu novu stavku
- Grupe: `sales`, `network`, `admin`, `system`

### Stranice umesto modala
| Ranije modal | Sada stranica |
|---|---|
| Custom report editor | `/admin/reports` (admin_reports.html) |
| Portal permissions | `/admin/portal-permissions` |
| Permissions matrix | `/admin/permissions` |
| Custom fields | `/admin/custom-fields` |
| Webhooks & API | `/admin/webhooks` |
| **Ofer/Invoice/Proforma editor** | `/documents/edit/<type>/<id>` (document_editor.html) — page-based, autocomplete partnera, "Save Draft" / "Save Final" |
| **Document Register** | `/documents/register` |
| Supabase Merge Wizard | `/admin/supabase/merge` |
| Security Center | `/profile/security` |

### Responsive
- **Fajl**: `static/css/modern.css`
- Ispod 1024px sidebar postaje off-canvas sa overlay-om
- Toggle: `body.sidebar-mobile-open` (postavlja hamburger u top bar)
- Tabele: horizontal-scroll wrapper na mobile-u

---

## 3. Granularne interne permisije

### Admin UI
| `/admin/permissions` | Matrica: korisnik u koloni × permission ključ u redu. Admin dobija sve automatski. |

### Backend
- **Katalog**: `routes/v23_admin.py` → `PERMISSION_CATALOG` dict (44 ključa, grupisan po modulu, sa objašnjenjem za svaki)
- **Enforcement dekorator**: `utils.require_perm('deals.delete')` — dodaje se PORED `@login_required`. Admin uvek prolazi.
- **Storage**: `users.permissions` (JSON dict). Save invalidira sve sesije tog user-a (bumps token_version).

### Kako gate-ovati novu rutu
```python
from utils import require_perm

@app.route('/api/deals/<did>', methods=['DELETE'])
@login_required
@require_perm('deals.delete')
def delete_deal(did):
    ...
```

---

## 4. Portal permisije po klijentu

### Admin UI
| `/admin/portal-permissions` | Za svakog partnera: master switch (Portal Active), Premium flag, **View only own docs** (default ON, strogo filtrira), lista modula |

### Enforcement
- **Fajl**: `routes/portal/data.py` — svi `if x.get('partnerId') == partner_id` filteri; invoices/proformas dodati kao poseban filter
- Ako `view_only_own_docs = False`, i dalje ne otkrivamo tuđe dokumente (bezbedan default)

---

## 5. Modul za izradu i uređivanje dokumenata

### Stranica
- `/documents/new/offer` — nova prazna ponuda
- `/documents/new/invoice` — nova faktura
- `/documents/new/proforma` — nova proforma
- `/documents/edit/<type>/<id>` — edit postojećeg

### Features
- Partner autocomplete (traži po nazivu iz `/api/partners`)
- Currency, incoterm, issue/due date, notes
- Line items sa auto-total kalkulacijom
- **Save Draft** — brz save, nema doc numbera
- **Save Final** — rezerviše broj iz `document_register`, dodeljuje V1 label
- Ako je već finalizovan i menja se → traži **change_reason**, pravi novu reviziju sa V2, V3, …
- Za finalizovane ponude: **→ Invoice** i **→ Proforma** dugmad (1:1 konverzija)

---

## 6. Numeracija + "Knjiga izdatih dokumenata"

### Stranica
- `/documents/register` — Knjiga sa filterima po tipu, godini, klijentu, tekstu

### Logika broja
- `document_register` tabela (postojeca): `docType | year | seq | docNumber | entityId | revision | status | issuedAt | issuedBy`
- Prvi save: seq je `MAX(seq)+1` za tu godinu i tip; docNumber je `OFF-2026-00001` / `INV-2026-...` / `PRO-2026-...`
- Naknadna izmena finalnog dokumenta: nova revizija (`V2`, `V3`, ...) sa snapshot-om u `document_revisions`, `changeReason` obavezan
- Version label uvek vidljiv (`V1`, `V2`, ...)

### Šta se vidi u Register-u
Doc # · Type · **Version badge** (V1/V2) · Client · Issued (datum + vreme) · By (autor) · Status · Open link · History (za revizije)

---

## 7. Workflow konverzije dokumenata (1/1 prenos)

### API
- `POST /api/documents/convert` sa `{source_type: 'offer', source_id, target_type: 'invoice'|'proforma'}`
- Kopira `ceo offer JSON` u novi red target tabele bez ikakve transformacije
- Dodeljuje novi docNumber (nezavisan sekvencijalni), status='draft'
- Link nazad na izvornu ponudu preko `sourceOfferId` + `sourceOfferNumber`

### UI trigger
- U Document Editor-u — kad je ponuda finalizovana, gumbovi **"→ Invoice"** i **"→ Proforma"** su vidljivi
- Klik → confirm → convert → redirect na editor novog dokumenta

### Portal accept trace
- Kada klijent prihvati ponudu preko portala (POST `/api/portal/offers/accept/...`), event se piše u `document_revisions` sa reason `Portal ACCEPT by <client>`
- Vidiš u `/documents/register` → History dugme

---

## 8. Breadcrumbs

- **Fajl**: `static/js/core/breadcrumbs.js`
- API:
  - `window.setBreadcrumbs([{label:'Partners', href:'#partners'}, {label:'Vectra'}, {label:'New offer'}])`
  - `window.pushBreadcrumb({label, href})`
  - `window.popBreadcrumb()`
- Automatski mount u top bar (`#breadcrumbs-mount` slot)
- Sidebar view klik automatski postavlja breadcrumb na naziv view-a
- Poslednji element je nekliktabilan (trenutna stranica)

---

## Extras (dodato van osnovnog spiska)

### Bulk actions
- `POST /api/bulk/<entity>/<action>` — archive, tag, delete
- Emisija outbound event-a (`partners.bulk_archive`, itd.)

### Custom fields
- `/admin/custom-fields` — admin definiše polja po entitetu
- Tipovi: text, number, date, bool, select, url, email
- Storage: `custom_field_defs`

### API keys
- `/admin/webhooks` — donji panel
- Bearer `ask_<token>` (SHA-256 hash u bazi, raw jednom prikazan)
- Scope: read | write | admin, rate_limit_per_min

### Outbound webhooks
- `/admin/webhooks` — gornji panel
- HMAC-SHA256 potpisivanje (`X-Aspidus-Signature`)
- Auto-disable posle 20 consecutive fail-ova
- Delivery log per webhook

### Security Center
- `/profile/security` — self-service: sessions list + revoke, trusted devices, login history, known IPs, password change
- **Round F**: magic-link login, account lockout sa self-unlock, must-change-password gate, break-glass recovery (admin)

### Reports
- `/admin/reports` — SQL SELECT reports (WITH allowed), chart types: table/kpi/bar/line/pie
- Sve SELECT-only, blokira DDL/DML keyword-e, timeout 10s, LIMIT 5000

---

## Kako proveriti da sve radi

1. **Otvori CRM** → u sidebar-u pod **Admin** i **System** grupama vidiš sve nove stavke
2. **Top bar** — u desno gore je meni sa Security Center, Preferences, Document Register + admin skup
3. **Mobile** — hamburger u top bar-u otvara sidebar
4. **Breadcrumbs** — u top bar-u levo, klikni "Dashboard" za povratak
5. **Kreiraj ponudu** → Top bar → "+ New offer" ili sidebar → New Offer
6. **Save Draft** → dopuni → **Save final** dobija docNumber (V1) → uredi → **Save final** ponovo traži change reason (V2)
7. **→ Invoice** dugme na finalnoj ponudi konvertuje 1:1
8. **Document Register** pokazuje sve sa quick-link Open →

## Kako pokrenuti .db import

```bash
# 1. Prvo dry-run (bezbedno — samo broji)
python scripts/import_local_db_to_supabase.py --db aspidus_crm.db --dry-run

# 2. Ako izgleda OK, stvarno importuj
python scripts/import_local_db_to_supabase.py --db aspidus_crm.db --confirm

# Ili iz UI: /admin/supabase/merge → klikni "Push →" po tabeli
```

## Kako pokrenuti Supabase coverage audit

```bash
python scripts/audit_supabase_coverage.py
# Ispisuje out/supabase_coverage_report.md — proveri i popravi lokalne write-ove
```
