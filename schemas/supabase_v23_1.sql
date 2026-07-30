-- ==========================================================================
-- ASPIDUS CRM — Supabase schema V23.1 additive migration
-- ==========================================================================
-- POKRENI OVU FILE u Supabase Dashboard → SQL Editor → New query → RUN.
-- Idempotent — bezbedno je pokrenuti više puta.
-- Dodaje sve tabele koje su nastale kroz V23.1 + kolone koje su nedostajale
-- pa je merge wizard prijavljivao PGRST204 / PGRST205 / 23502.
-- ==========================================================================

-- --------- INVOICES i PROFORMAS (konverzija ponuda) ---------
CREATE TABLE IF NOT EXISTS invoices (
  id            TEXT PRIMARY KEY,
  data          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS proformas (
  id            TEXT PRIMARY KEY,
  data          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --------- USERS (CRM interni useri) ---------
CREATE TABLE IF NOT EXISTS users (
  id                        TEXT PRIMARY KEY,
  username                  TEXT UNIQUE,
  password                  TEXT,   -- V23.4: scrypt hash — bez ovoga login ne moze da radi kroz Supabase fallback posle Render redeploy-a
  role                      TEXT,
  full_name                 TEXT,
  email                     TEXT,
  phone                     TEXT,
  notif_prefs               JSONB DEFAULT '{}'::jsonb,
  permissions               JSONB DEFAULT '{}'::jsonb,
  must_change_password      BOOLEAN DEFAULT FALSE,
  locked_until              TIMESTAMPTZ,
  password_expires_at       TIMESTAMPTZ,
  signature                 TEXT,
  totp_secret               TEXT,   -- V23.4: potrebno za 2FA verifikaciju iz Supabase-a
  totp_enabled              BOOLEAN DEFAULT FALSE,
  totp_recovery             TEXT,   -- V23.4: hasovani recovery kodovi
  token_version             INTEGER DEFAULT 1,
  last_password_change_at   TIMESTAMPTZ,
  last_login_country        TEXT,
  data                      JSONB DEFAULT '{}'::jsonb,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- V23.4 idempotentne migracije (za baze koje su vec kreirane bez ovih kolona)
ALTER TABLE users ADD COLUMN IF NOT EXISTS password TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_secret TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_recovery TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER DEFAULT 1;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_password_change_at TIMESTAMPTZ;

-- --------- SETTINGS (comms_settings, company, security_policy…) ---------
CREATE TABLE IF NOT EXISTS settings (
  key       TEXT PRIMARY KEY,
  value     TEXT,
  data      JSONB DEFAULT '{}'::jsonb
);

-- --------- ROUND F: SESSIONS, KNOWN IPs, TRUSTED DEVICES, PW HISTORY ---------
CREATE TABLE IF NOT EXISTS user_sessions (
  id                TEXT PRIMARY KEY,
  user_id           TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  ip                TEXT,
  country           TEXT,
  user_agent        TEXT,
  ua_family         TEXT,
  device_label      TEXT,
  revoked           BOOLEAN NOT NULL DEFAULT FALSE,
  revoked_at        TIMESTAMPTZ,
  revoked_reason    TEXT,
  data              JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS user_sessions_user_idx ON user_sessions (user_id, revoked);

CREATE TABLE IF NOT EXISTS known_ips (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL,
  ip            TEXT NOT NULL,
  country       TEXT,
  city          TEXT,
  first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
  login_count   INTEGER DEFAULT 1,
  data          JSONB DEFAULT '{}'::jsonb,
  UNIQUE(user_id, ip)
);
CREATE INDEX IF NOT EXISTS known_ips_user_idx ON known_ips (user_id);

CREATE TABLE IF NOT EXISTS trusted_devices (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL,
  token_hash    TEXT NOT NULL UNIQUE,
  label         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ NOT NULL,
  last_seen_at  TIMESTAMPTZ,
  last_ip       TEXT,
  revoked       BOOLEAN DEFAULT FALSE,
  data          JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS trusted_devices_user_idx ON trusted_devices (user_id, revoked);

CREATE TABLE IF NOT EXISTS password_history (
  id                TEXT PRIMARY KEY,
  user_id           TEXT NOT NULL,
  password_hash     TEXT NOT NULL,
  changed_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pwhistory_user_idx ON password_history (user_id, changed_at);

CREATE TABLE IF NOT EXISTS magic_login_tokens (
  token         TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL,
  purpose       TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ NOT NULL,
  used_at       TIMESTAMPTZ,
  request_ip    TEXT
);
CREATE INDEX IF NOT EXISTS magictok_user_idx ON magic_login_tokens (user_id);

-- --------- ROUND G: TASKS, FILTERS ---------
CREATE TABLE IF NOT EXISTS user_tasks (
  id                    TEXT PRIMARY KEY,
  owner_user_id         TEXT NOT NULL,
  title                 TEXT NOT NULL,
  description           TEXT,
  due_at                TIMESTAMPTZ,
  priority              INTEGER DEFAULT 2,
  status                TEXT DEFAULT 'open',
  linked_entity_type    TEXT,
  linked_entity_id      TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at          TIMESTAMPTZ,
  data                  JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS usertasks_owner_idx ON user_tasks (owner_user_id, status);
CREATE INDEX IF NOT EXISTS usertasks_due_idx ON user_tasks (due_at);

CREATE TABLE IF NOT EXISTS saved_filters (
  id                TEXT PRIMARY KEY,
  owner_user_id     TEXT NOT NULL,
  name              TEXT NOT NULL,
  entity_type       TEXT NOT NULL,
  filter_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_shared         BOOLEAN DEFAULT FALSE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  data              JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS savedfilters_owner_idx ON saved_filters (owner_user_id, entity_type);

-- --------- V22 ROUND A/B: NOTES, DEAL DOCS, CUSTOM REPORTS ---------
CREATE TABLE IF NOT EXISTS entity_notes (
  id            TEXT PRIMARY KEY,
  entity_type   TEXT NOT NULL,
  entity_id     TEXT NOT NULL,
  body          TEXT NOT NULL,
  created_by    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  pinned        BOOLEAN DEFAULT FALSE,
  data          JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS entnotes_entity_idx ON entity_notes (entity_type, entity_id);

CREATE TABLE IF NOT EXISTS deal_documents (
  id            TEXT PRIMARY KEY,
  deal_id       TEXT NOT NULL,
  file_url      TEXT NOT NULL,
  filename      TEXT NOT NULL,
  doc_kind      TEXT,
  size_bytes    BIGINT,
  uploaded_by   TEXT,
  uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  note          TEXT,
  data          JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS dealdocs_deal_idx ON deal_documents (deal_id);

CREATE TABLE IF NOT EXISTS custom_reports (
  id                TEXT PRIMARY KEY,
  owner_user_id     TEXT NOT NULL,
  title             TEXT NOT NULL,
  description       TEXT,
  sql_query         TEXT NOT NULL,
  chart_type        TEXT,
  is_shared         BOOLEAN DEFAULT FALSE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  data              JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS customreports_owner_idx ON custom_reports (owner_user_id);

-- --------- FAZA 5: PER-PARTNER INVENTORY ---------
CREATE TABLE IF NOT EXISTS partner_inventory (
  id                    TEXT PRIMARY KEY,
  partner_id            TEXT NOT NULL,
  product_id            TEXT NOT NULL,
  qty_on_hand           NUMERIC NOT NULL DEFAULT 0,
  qty_reserved          NUMERIC NOT NULL DEFAULT 0,
  unit                  TEXT,
  last_movement_at      TIMESTAMPTZ,
  data                  JSONB DEFAULT '{}'::jsonb,
  UNIQUE(partner_id, product_id)
);
CREATE INDEX IF NOT EXISTS partinv_partner_idx ON partner_inventory (partner_id);
CREATE INDEX IF NOT EXISTS partinv_product_idx ON partner_inventory (product_id);

CREATE TABLE IF NOT EXISTS inventory_movements (
  id                TEXT PRIMARY KEY,
  partner_id        TEXT NOT NULL,
  product_id        TEXT NOT NULL,
  kind              TEXT NOT NULL,
  qty               NUMERIC NOT NULL,
  unit              TEXT,
  deal_id           TEXT,
  note              TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by        TEXT,
  data              JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS invmov_partner_idx ON inventory_movements (partner_id);
CREATE INDEX IF NOT EXISTS invmov_deal_idx    ON inventory_movements (deal_id);

-- --------- V23.1 EXTRAS: CUSTOM FIELDS, API KEYS, WEBHOOKS ---------
CREATE TABLE IF NOT EXISTS custom_field_defs (
  id                TEXT PRIMARY KEY,
  entity_type       TEXT NOT NULL,
  field_key         TEXT NOT NULL,
  field_label       TEXT NOT NULL,
  field_type        TEXT NOT NULL,
  options_json      JSONB,
  required          BOOLEAN DEFAULT FALSE,
  display_order     INTEGER DEFAULT 100,
  is_active         BOOLEAN DEFAULT TRUE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  data              JSONB DEFAULT '{}'::jsonb,
  UNIQUE(entity_type, field_key)
);
CREATE INDEX IF NOT EXISTS cfd_entity_idx ON custom_field_defs (entity_type, is_active);

CREATE TABLE IF NOT EXISTS api_keys (
  id                    TEXT PRIMARY KEY,
  name                  TEXT NOT NULL,
  key_hash              TEXT NOT NULL UNIQUE,
  key_prefix            TEXT NOT NULL,
  owner_user_id         TEXT NOT NULL,
  scope                 TEXT DEFAULT 'read',
  rate_limit_per_min    INTEGER DEFAULT 60,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at          TIMESTAMPTZ,
  revoked               BOOLEAN DEFAULT FALSE,
  revoked_at            TIMESTAMPTZ,
  data                  JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS apikey_owner_idx ON api_keys (owner_user_id, revoked);

CREATE TABLE IF NOT EXISTS outbound_webhooks (
  id                TEXT PRIMARY KEY,
  name              TEXT NOT NULL,
  target_url        TEXT NOT NULL,
  events            TEXT NOT NULL,
  secret            TEXT NOT NULL,
  is_active         BOOLEAN DEFAULT TRUE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by        TEXT,
  last_fired_at     TIMESTAMPTZ,
  last_status       TEXT,
  fail_count        INTEGER DEFAULT 0,
  data              JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS webhook_active_idx ON outbound_webhooks (is_active);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
  id                TEXT PRIMARY KEY,
  webhook_id        TEXT NOT NULL,
  event             TEXT NOT NULL,
  payload_hash      TEXT,
  status_code       INTEGER,
  response_snippet  TEXT,
  delivered_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  duration_ms       INTEGER
);
CREATE INDEX IF NOT EXISTS delivery_webhook_idx ON webhook_deliveries (webhook_id, delivered_at);

-- --------- FILE TEXT CACHE (OCR) ---------
CREATE TABLE IF NOT EXISTS file_text (
  id            TEXT PRIMARY KEY,
  file_url      TEXT NOT NULL UNIQUE,
  partner_id    TEXT,
  filename      TEXT,
  content_type  TEXT,
  text_preview  TEXT,
  full_text     TEXT,
  char_count    INTEGER,
  extracted_at  TIMESTAMPTZ,
  data          JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS filetext_partner_idx ON file_text (partner_id);

-- ==========================================================================
-- FIX: audit_logs — SQLite koristi TEXT id (UUID); Supabase je imao BIGSERIAL.
-- Dodajemo alternativni tekstualni id kolonu za sync-uvoz i ostavljamo
-- BIGSERIAL id netaknut da postojeci audit ne pukne.
-- ==========================================================================
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS sync_id     TEXT UNIQUE;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS user_id     TEXT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS username    TEXT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS timestamp   TIMESTAMPTZ;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS location    TEXT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS data        JSONB DEFAULT '{}'::jsonb;

-- ==========================================================================
-- OSTALO ako neka od preporucenih tabela iz staره sheme jos nema data kolonu
-- ==========================================================================
ALTER TABLE partners            ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE partners            ADD COLUMN IF NOT EXISTS contact_person TEXT;
ALTER TABLE partners            ADD COLUMN IF NOT EXISTS country TEXT;
ALTER TABLE partners            ADD COLUMN IF NOT EXISTS city    TEXT;
ALTER TABLE partners            ADD COLUMN IF NOT EXISTS street  TEXT;
ALTER TABLE partners            ADD COLUMN IF NOT EXISTS tax_id  TEXT;
ALTER TABLE partners            ADD COLUMN IF NOT EXISTS portal_token TEXT;
ALTER TABLE partners            ADD COLUMN IF NOT EXISTS is_portal_active BOOLEAN DEFAULT TRUE;
-- Ublazi NOT NULL na email/company_name da uvoz starih SQLite redova sa
-- praznim poljem ne pukne — validacija u aplikaciji ostaje.
ALTER TABLE partners            ALTER COLUMN email DROP NOT NULL;
ALTER TABLE partners            ALTER COLUMN company_name DROP NOT NULL;

ALTER TABLE products            ADD COLUMN IF NOT EXISTS name TEXT;
ALTER TABLE products            ADD COLUMN IF NOT EXISTS sku TEXT;
ALTER TABLE products            ADD COLUMN IF NOT EXISTS hs_code TEXT;
ALTER TABLE products            ADD COLUMN IF NOT EXISTS unit TEXT;
ALTER TABLE products            ADD COLUMN IF NOT EXISTS supplier_id TEXT;

ALTER TABLE deals               ADD COLUMN IF NOT EXISTS supplier_id TEXT;
ALTER TABLE deals               ADD COLUMN IF NOT EXISTS product_id TEXT;
ALTER TABLE deals               ADD COLUMN IF NOT EXISTS status TEXT;
ALTER TABLE deals               ADD COLUMN IF NOT EXISTS total_amount NUMERIC;
ALTER TABLE deals               ADD COLUMN IF NOT EXISTS currency TEXT;

-- ==========================================================================
-- Done. Vrati u Merge Wizard i klikni "Push ALL tables".
-- ==========================================================================
