-- ==========================================================================
-- ASPIDUS CRM — Supabase (PostgreSQL) šema — Faza 0
-- ==========================================================================
-- Ovo je čist DDL za NOV Supabase projekat. Pokreće se u:
--   Supabase Dashboard → SQL Editor → New query → paste ovaj sadržaj → Run
--
-- Kreira 3 logička sloja:
--   1) Portal domain (partners, kyc_submissions, portal_products, ...)
--   2) CRM domain (offers, deals, products, ...) — samo one koje portal koristi
--   3) Sistemski (audit_logs, offer_versions, document_register, ...)
--
-- NIJE aktivira RLS. Flask backend ostaje gatekeeper (koristi service_role
-- ključ). RLS se dodaje u kasnijoj fazi kao druga linija odbrane.
--
-- NIJE dira Supabase Auth šemu (auth.users tabela).
-- Naša tabela `partners` se povezuje na auth.users preko partners.auth_user_id.
-- ==========================================================================

-- ---------- EXTENSIONS ----------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- fuzzy pretraga partnera po imenu
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- za buduće gen_random_bytes()

-- ==========================================================================
-- PARTNERS — glavna tabela portal klijenata
-- Struktura: eksplicitne kolone za sve što admin toggluje + `data` JSONB
-- za sve ostale slobodne atribute (adresa, kontakti, tagovi, itd).
-- Ovaj hibrid daje: (a) SQL upitljivost za filter/badge, (b) fleksibilnost
-- za sve buduće detalje bez migracija šeme.
-- ==========================================================================
CREATE TABLE IF NOT EXISTS partners (
  id                    TEXT PRIMARY KEY,
  auth_user_id          UUID UNIQUE REFERENCES auth.users(id) ON DELETE SET NULL,
  email                 TEXT NOT NULL,
  company_name          TEXT NOT NULL,

  -- ---- PORTAL RBAC (4 nivoa) ----
  can_login             BOOLEAN NOT NULL DEFAULT TRUE,                -- master kill switch
  portal_level          SMALLINT NOT NULL DEFAULT 1
                        CHECK (portal_level BETWEEN 1 AND 4),
                        -- 1 = Explorer, 2 = Trader, 3 = Verified, 4 = Premium
  kyc_approved          BOOLEAN NOT NULL DEFAULT FALSE,               -- auto true kad admin approve KYC
  is_premium            BOOLEAN NOT NULL DEFAULT FALSE,               -- Level 4 flag (bez GPS/KYC gate)

  -- ---- Ostatak partner podataka u JSONB (fleksibilno) ----
  data                  JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- ---- Sistemske kolone ----
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by            TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS partners_email_lower_uidx
  ON partners (LOWER(email));
CREATE INDEX IF NOT EXISTS partners_company_name_trgm
  ON partners USING gin (company_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS partners_level_idx
  ON partners (portal_level);
CREATE INDEX IF NOT EXISTS partners_can_login_idx
  ON partners (can_login) WHERE can_login = false;    -- brz filter za "zaključane"

-- Automatski updated_at
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS partners_set_updated_at ON partners;
CREATE TRIGGER partners_set_updated_at
  BEFORE UPDATE ON partners
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ==========================================================================
-- KYC SUBMISSIONS — istorija svake predaje KYC forme sa portala
-- ==========================================================================
CREATE TABLE IF NOT EXISTS kyc_submissions (
  id                    TEXT PRIMARY KEY,
  partner_id            TEXT NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
  data                  JSONB NOT NULL DEFAULT '{}'::jsonb,
  status                TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected', 'update_requested')),
  submitted_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at           TIMESTAMPTZ,
  reviewed_by           TEXT
);
CREATE INDEX IF NOT EXISTS kyc_submissions_partner_idx ON kyc_submissions (partner_id);
CREATE INDEX IF NOT EXISTS kyc_submissions_status_idx  ON kyc_submissions (status);


-- ==========================================================================
-- PORTAL PRODUCTS — klijenti predlažu proizvode preko portala
-- ==========================================================================
CREATE TABLE IF NOT EXISTS portal_products (
  id                    TEXT PRIMARY KEY,
  partner_id            TEXT NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
  data                  JSONB NOT NULL DEFAULT '{}'::jsonb,
  status                TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected')),
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS portal_products_partner_idx ON portal_products (partner_id);
CREATE INDEX IF NOT EXISTS portal_products_status_idx  ON portal_products (status);


-- ==========================================================================
-- PROFILE CHANGE REQUESTS — klijent traži izmenu svojih podataka
-- ==========================================================================
CREATE TABLE IF NOT EXISTS profile_change_requests (
  id                    TEXT PRIMARY KEY,
  partner_id            TEXT NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
  data                  JSONB NOT NULL DEFAULT '{}'::jsonb,
  status                TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected')),
  submitted_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at           TIMESTAMPTZ,
  reviewed_by           TEXT
);
CREATE INDEX IF NOT EXISTS profile_change_requests_partner_idx ON profile_change_requests (partner_id);


-- ==========================================================================
-- PORTAL HIDDEN ITEMS — klijent sakriva ponude/dokumente iz svog view-a
-- ==========================================================================
CREATE TABLE IF NOT EXISTS portal_hidden_items (
  id                    TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  partner_id            TEXT NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
  entity_type           TEXT NOT NULL CHECK (entity_type IN ('offer', 'document')),
  entity_id             TEXT NOT NULL,
  label                 TEXT,
  hidden_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS phi_partner_entity_uidx
  ON portal_hidden_items (partner_id, entity_type, entity_id);


-- ==========================================================================
-- OFFERS — ponude su vezane za partnera, ali portalu se serviraju iz ovog
-- CRM entiteta. Zadržavamo istu JSONB strukturu kao u SQLite-u.
-- ==========================================================================
CREATE TABLE IF NOT EXISTS offers (
  id                    TEXT PRIMARY KEY,
  offer_no              TEXT,
  customer_id           TEXT REFERENCES partners(id) ON DELETE SET NULL,
  data                  JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS offers_customer_idx  ON offers (customer_id);
CREATE INDEX IF NOT EXISTS offers_offer_no_idx  ON offers (offer_no);

DROP TRIGGER IF EXISTS offers_set_updated_at ON offers;
CREATE TRIGGER offers_set_updated_at
  BEFORE UPDATE ON offers
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ==========================================================================
-- OFFER VERSIONS — istorija izmena ponuda
-- ==========================================================================
CREATE TABLE IF NOT EXISTS offer_versions (
  id                    TEXT PRIMARY KEY,
  offer_id              TEXT NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
  version               INTEGER NOT NULL,
  snapshot              JSONB NOT NULL,
  changed_fields        TEXT,
  change_reason         TEXT,
  changed_by            TEXT,
  changed_by_role       TEXT,
  changed_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  origin                TEXT
);
CREATE INDEX IF NOT EXISTS offer_versions_offer_idx  ON offer_versions (offer_id);
CREATE INDEX IF NOT EXISTS offer_versions_at_idx     ON offer_versions (changed_at);


-- ==========================================================================
-- DEALS, PRODUCTS, DEMANDS — ostatak CRM entiteta portal koristi za prikaz
-- ==========================================================================
CREATE TABLE IF NOT EXISTS deals (
  id                    TEXT PRIMARY KEY,
  data                  JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_offer_id       TEXT REFERENCES offers(id) ON DELETE SET NULL,
  buyer_id              TEXT REFERENCES partners(id) ON DELETE SET NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS deals_buyer_idx  ON deals (buyer_id);
CREATE INDEX IF NOT EXISTS deals_source_offer_idx  ON deals (source_offer_id);
DROP TRIGGER IF EXISTS deals_set_updated_at ON deals;
CREATE TRIGGER deals_set_updated_at BEFORE UPDATE ON deals
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS products (
  id                    TEXT PRIMARY KEY,
  data                  JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
DROP TRIGGER IF EXISTS products_set_updated_at ON products;
CREATE TRIGGER products_set_updated_at BEFORE UPDATE ON products
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS demands (
  id                    TEXT PRIMARY KEY,
  data                  JSONB NOT NULL DEFAULT '{}'::jsonb,
  buyer_id              TEXT REFERENCES partners(id) ON DELETE SET NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS demands_buyer_idx  ON demands (buyer_id);


-- ==========================================================================
-- SHARED DOCUMENTS — dokumenti podeljeni sa klijentom (linkovi na Storage)
-- ==========================================================================
CREATE TABLE IF NOT EXISTS shared_documents (
  id                    TEXT PRIMARY KEY,
  partner_id            TEXT REFERENCES partners(id) ON DELETE CASCADE,
  title                 TEXT,
  category              TEXT,
  storage_bucket        TEXT,        -- 'offer-pdfs' | 'partner-docs'
  storage_path          TEXT,        -- putanja unutar bucket-a
  data                  JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS shared_documents_partner_idx ON shared_documents (partner_id);


-- ==========================================================================
-- DOCUMENT REGISTER + REVISIONS — brojači i istorije izdatih dokumenata
-- ==========================================================================
CREATE TABLE IF NOT EXISTS document_register (
  id                    TEXT PRIMARY KEY,
  doc_number            TEXT UNIQUE NOT NULL,
  doc_type              TEXT NOT NULL,
  year                  INTEGER NOT NULL,
  seq                   INTEGER NOT NULL,
  entity_id             TEXT,
  revision              INTEGER NOT NULL DEFAULT 0,
  issued_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  issued_by             TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS docreg_type_year_seq_uidx
  ON document_register (doc_type, year, seq);
CREATE INDEX IF NOT EXISTS docreg_entity_idx ON document_register (entity_id);

CREATE TABLE IF NOT EXISTS document_revisions (
  id                    TEXT PRIMARY KEY,
  doc_number            TEXT NOT NULL,
  revision              INTEGER NOT NULL,
  entity_id             TEXT,
  snapshot              JSONB NOT NULL,
  content_hash          TEXT,
  binding_hash          TEXT,
  change_reason         TEXT,
  changed_by            TEXT,
  changed_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS docrev_number_idx ON document_revisions (doc_number);


-- ==========================================================================
-- AUDIT LOG — logujemo sve akcije radi compliance
-- ==========================================================================
CREATE TABLE IF NOT EXISTS audit_logs (
  id                    BIGSERIAL PRIMARY KEY,
  ts                    TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor                 TEXT,           -- email / user_id
  actor_role            TEXT,           -- 'admin' | 'employee' | 'partner' | 'system'
  action                TEXT NOT NULL,  -- 'CREATE' | 'EDIT' | 'DELETE' | 'LOGIN' | ...
  module                TEXT,           -- 'partners' | 'offers' | 'kyc' | ...
  details               TEXT,
  is_suspicious         BOOLEAN NOT NULL DEFAULT FALSE,
  ip_address            INET,
  user_agent            TEXT
);
CREATE INDEX IF NOT EXISTS audit_ts_idx        ON audit_logs (ts DESC);
CREATE INDEX IF NOT EXISTS audit_actor_idx     ON audit_logs (actor);
CREATE INDEX IF NOT EXISTS audit_module_idx    ON audit_logs (module);
CREATE INDEX IF NOT EXISTS audit_suspicious_idx ON audit_logs (is_suspicious) WHERE is_suspicious = true;


-- ==========================================================================
-- STORAGE OBJECTS TRACKING — evidencija svakog fajla u Supabase Storage-u
-- radi Free Tier housekeeping-a (admin lako identifikuje šta može da izvuče
-- i obriše).
-- ==========================================================================
CREATE TABLE IF NOT EXISTS storage_objects (
  id                    TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  bucket                TEXT NOT NULL,
  path                  TEXT NOT NULL,
  partner_id            TEXT REFERENCES partners(id) ON DELETE SET NULL,
  entity_type           TEXT,          -- 'kyc' | 'offer_pdf' | 'shared_doc' | 'portal_upload'
  entity_id             TEXT,
  original_filename     TEXT,
  mime_type             TEXT,
  size_bytes            BIGINT,
  sha256                TEXT,
  uploaded_by           TEXT,
  uploaded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  archived_at           TIMESTAMPTZ,     -- kad ga admin izvuče na disk
  deleted_from_bucket   BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE UNIQUE INDEX IF NOT EXISTS storage_objects_bucket_path_uidx
  ON storage_objects (bucket, path);
CREATE INDEX IF NOT EXISTS storage_objects_partner_idx  ON storage_objects (partner_id);
CREATE INDEX IF NOT EXISTS storage_objects_archived_idx ON storage_objects (archived_at)
  WHERE archived_at IS NULL;


-- ==========================================================================
-- READY. Sledeći korak: Storage bucket-i (kroz Dashboard) i .env fajl.
-- ==========================================================================

-- ========== SANITY CHECK ==========
-- Nakon što ovo pokreneš u SQL Editor-u, uradi:
--   SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1;
-- Treba da vidiš: audit_logs, deals, demands, document_register,
-- document_revisions, kyc_submissions, offer_versions, offers, partners,
-- portal_hidden_items, portal_products, products, profile_change_requests,
-- shared_documents, storage_objects
-- (15 tabela)
