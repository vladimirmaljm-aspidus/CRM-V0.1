-- ==========================================================================
-- ASPIDUS CRM — V25 ŠEMA FIX — idempotentna migracija
-- ==========================================================================
-- Cilj: šema u Supabase bazi postane identična onome što kod očekuje.
-- Bezbedno za višestruko pokretanje (sve je IF NOT EXISTS / DO blokovi).
--
-- Šta ovo radi:
--   1. Dodaje nedostajuće kolone u partners, offers, deals, demands,
--      shared_documents, kyc_submissions, portal_hidden_items, users
--   2. Menja tip permissions/notif_prefs u users iz TEXT u JSONB
--   3. Menja tip must_change_password u users iz INTEGER u BOOLEAN
--   4. Briše duplikat kolone recovery_codes iz users (zadržava totp_recovery)
--   5. Briše profil kolonu iz users (ide u data JSONB)
--   6. Menja tip snapshot u offer_versions i document_revisions u JSONB
--   7. Menja tip sent_at u email_queue u TIMESTAMPTZ
--   8. Popravlja portal_products status default u 'pending' + CHECK
-- ==========================================================================

-- ---------- extensions (uvek bezbedno) ----------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ==========================================================================
-- 1. PARTNERS — dodaj nedostajuće kolone
-- ==========================================================================
ALTER TABLE partners ADD COLUMN IF NOT EXISTS auth_user_id UUID UNIQUE REFERENCES auth.users(id) ON DELETE SET NULL;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS can_login BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS portal_level SMALLINT NOT NULL DEFAULT 1;

-- Dodaj CHECK constraint za portal_level (1-4) — bezbedno (DO blok proverava)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'partners_portal_level_check'
    ) THEN
        ALTER TABLE partners
          ADD CONSTRAINT partners_portal_level_check
          CHECK (portal_level BETWEEN 1 AND 4);
    END IF;
END $$;

ALTER TABLE partners ADD COLUMN IF NOT EXISTS kyc_approved BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS is_premium BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS created_by TEXT;

-- Indeksi (ako ih već nema)
CREATE UNIQUE INDEX IF NOT EXISTS partners_email_lower_uidx
    ON partners (LOWER(email));
CREATE INDEX IF NOT EXISTS partners_company_name_trgm
    ON partners USING gin (company_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS partners_level_idx
    ON partners (portal_level);
CREATE INDEX IF NOT EXISTS partners_can_login_idx
    ON partners (can_login) WHERE can_login = false;

-- ==========================================================================
-- 2. OFFERS — dodaj offer_no i customer_id
-- ==========================================================================
ALTER TABLE offers ADD COLUMN IF NOT EXISTS offer_no TEXT;
ALTER TABLE offers ADD COLUMN IF NOT EXISTS customer_id TEXT REFERENCES partners(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS offers_customer_idx ON offers (customer_id);
CREATE INDEX IF NOT EXISTS offers_offer_no_idx ON offers (offer_no);

-- ==========================================================================
-- 3. DEALS — dodaj buyer_id i source_offer_id
-- ==========================================================================
ALTER TABLE deals ADD COLUMN IF NOT EXISTS buyer_id TEXT REFERENCES partners(id) ON DELETE SET NULL;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS source_offer_id TEXT REFERENCES offers(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS deals_buyer_idx ON deals (buyer_id);
CREATE INDEX IF NOT EXISTS deals_source_offer_idx ON deals (source_offer_id);

-- ==========================================================================
-- 4. DEMANDS — dodaj buyer_id
-- ==========================================================================
ALTER TABLE demands ADD COLUMN IF NOT EXISTS buyer_id TEXT REFERENCES partners(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS demands_buyer_idx ON demands (buyer_id);

-- ==========================================================================
-- 5. SHARED_DOCUMENTS — dodaj 5 kolona koje fale
-- ==========================================================================
ALTER TABLE shared_documents ADD COLUMN IF NOT EXISTS partner_id TEXT REFERENCES partners(id) ON DELETE CASCADE;
ALTER TABLE shared_documents ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE shared_documents ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE shared_documents ADD COLUMN IF NOT EXISTS storage_bucket TEXT;
ALTER TABLE shared_documents ADD COLUMN IF NOT EXISTS storage_path TEXT;

CREATE INDEX IF NOT EXISTS shared_documents_partner_idx ON shared_documents (partner_id);

-- ==========================================================================
-- 6. KYC_SUBMISSIONS — dodaj status, reviewed_at, reviewed_by
-- ==========================================================================
ALTER TABLE kyc_submissions ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE kyc_submissions ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE kyc_submissions ADD COLUMN IF NOT EXISTS reviewed_by TEXT;

-- Dodaj CHECK constraint za status (bezbedno)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'kyc_submissions_status_check'
    ) THEN
        ALTER TABLE kyc_submissions
          ADD CONSTRAINT kyc_submissions_status_check
          CHECK (status IN ('pending', 'approved', 'rejected', 'update_requested'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS kyc_submissions_partner_idx ON kyc_submissions (partner_id);
CREATE INDEX IF NOT EXISTS kyc_submissions_status_idx ON kyc_submissions (status);

-- ==========================================================================
-- 7. PORTAL_HIDDEN_ITEMS — dodaj label
-- ==========================================================================
ALTER TABLE portal_hidden_items ADD COLUMN IF NOT EXISTS label TEXT;

-- ==========================================================================
-- 8. PORTAL_PRODUCTS — popravi status default i dodaj CHECK
-- ==========================================================================
DO $$
BEGIN
    -- Proveri da li je default 'active' i zameni u 'pending'
    -- Napomena: koristimo dupli apostrof za escape unutar stringa
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'portal_products'
          AND column_name = 'status'
          AND column_default = '''active''::text'
    ) THEN
        ALTER TABLE portal_products ALTER COLUMN status SET DEFAULT 'pending';
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'portal_products_status_check'
    ) THEN
        ALTER TABLE portal_products
          ADD CONSTRAINT portal_products_status_check
          CHECK (status IN ('pending', 'approved', 'rejected'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS portal_products_partner_idx ON portal_products (partner_id);
CREATE INDEX IF NOT EXISTS portal_products_status_idx ON portal_products (status);

-- ==========================================================================
-- 9. USERS — dodaj signature kolonu (ako je nema)
-- ==========================================================================
ALTER TABLE users ADD COLUMN IF NOT EXISTS signature TEXT;

-- ==========================================================================
-- 10. USERS — promena tipa permissions TEXT → JSONB
-- ==========================================================================
DO $$
BEGIN
    -- Proveri da li je permissions TEXT (ne JSONB)
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users'
          AND column_name = 'permissions'
          AND data_type = 'text'
    ) THEN
        -- DROP default pre nego sto promenimo tip (Postgres ne moze automatski da castuje default)
        ALTER TABLE users ALTER COLUMN permissions DROP DEFAULT;

        -- Konvertuj postojece stringove u prazan objekat ako su nevalidni JSON
        UPDATE users
          SET permissions = '{}'
          WHERE permissions IS NULL OR permissions = '' OR permissions !~ '^\{.*\}$';

        ALTER TABLE users
          ALTER COLUMN permissions TYPE JSONB USING
            CASE
              WHEN permissions IS NULL OR permissions = '' THEN '{}'::jsonb
              ELSE permissions::jsonb
            END;

        ALTER TABLE users ALTER COLUMN permissions SET DEFAULT '{}'::jsonb;
    END IF;
END $$;

-- ==========================================================================
-- 11. USERS — promena tipa notif_prefs TEXT → JSONB
-- ==========================================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users'
          AND column_name = 'notif_prefs'
          AND data_type = 'text'
    ) THEN
        ALTER TABLE users ALTER COLUMN notif_prefs DROP DEFAULT;

        UPDATE users
          SET notif_prefs = '{}'
          WHERE notif_prefs IS NULL OR notif_prefs = '' OR notif_prefs !~ '^\{.*\}$';

        ALTER TABLE users
          ALTER COLUMN notif_prefs TYPE JSONB USING
            CASE
              WHEN notif_prefs IS NULL OR notif_prefs = '' THEN '{}'::jsonb
              ELSE notif_prefs::jsonb
            END;

        ALTER TABLE users ALTER COLUMN notif_prefs SET DEFAULT '{}'::jsonb;
    END IF;
END $$;

-- ==========================================================================
-- 12. USERS — promena tipa must_change_password INTEGER → BOOLEAN
-- ==========================================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users'
          AND column_name = 'must_change_password'
          AND data_type = 'integer'
    ) THEN
        ALTER TABLE users ALTER COLUMN must_change_password DROP DEFAULT;

        ALTER TABLE users
          ALTER COLUMN must_change_password TYPE BOOLEAN USING
            CASE WHEN must_change_password = 0 THEN FALSE ELSE TRUE END;

        ALTER TABLE users ALTER COLUMN must_change_password SET DEFAULT FALSE;
    END IF;
END $$;

-- ==========================================================================
-- 13. USERS — briši duplikat kolonu recovery_codes (zadrži totp_recovery)
-- ==========================================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'recovery_codes'
    ) THEN
        -- Ako je recovery_codes popunjeno a totp_recovery prazno, prekopiraj
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'users' AND column_name = 'totp_recovery'
        ) THEN
            UPDATE users
              SET totp_recovery = recovery_codes
              WHERE totp_recovery IS NULL AND recovery_codes IS NOT NULL;
        END IF;

        ALTER TABLE users DROP COLUMN recovery_codes;
    END IF;
END $$;

-- ==========================================================================
-- 14. USERS — briši profil kolonu (preliva u data JSONB)
-- ==========================================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'profile'
    ) THEN
        -- Prebaci postojeće podatke u data JSONB ako je neprazno
        UPDATE users
          SET data = COALESCE(data, '{}'::jsonb) ||
                     CASE
                       WHEN profile IS NULL OR profile = '' OR profile = '{}' THEN '{}'::jsonb
                       ELSE profile::jsonb
                     END
          WHERE profile IS NOT NULL AND profile != '' AND profile != '{}';

        ALTER TABLE users DROP COLUMN profile;
    END IF;
END $$;

-- ==========================================================================
-- 15. USERS — dodaj ostale kolone koje kod očekuje (ako ih nema)
-- ==========================================================================
ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_expires_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_secret TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_recovery TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER DEFAULT 1;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_password_change_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_country TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS data JSONB DEFAULT '{}'::jsonb;
ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();
ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

-- ==========================================================================
-- 16. OFFER_VERSIONS — promeni tip snapshot TEXT → JSONB
-- ==========================================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'offer_versions'
          AND column_name = 'snapshot'
          AND data_type = 'text'
    ) THEN
        UPDATE offer_versions
          SET snapshot = '{}'::text
          WHERE snapshot IS NULL OR snapshot = '' OR snapshot !~ '^\{.*\}$';

        ALTER TABLE offer_versions
          ALTER COLUMN snapshot TYPE JSONB USING
            CASE
              WHEN snapshot IS NULL OR snapshot = '' THEN '{}'::jsonb
              ELSE snapshot::jsonb
            END;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS offer_versions_offer_idx ON offer_versions (offer_id);
CREATE INDEX IF NOT EXISTS offer_versions_at_idx ON offer_versions (changed_at);

-- ==========================================================================
-- 17. DOCUMENT_REVISIONS — promeni tip snapshot TEXT → JSONB
-- ==========================================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'document_revisions'
          AND column_name = 'snapshot'
          AND data_type = 'text'
    ) THEN
        UPDATE document_revisions
          SET snapshot = '{}'::text
          WHERE snapshot IS NULL OR snapshot = '' OR snapshot !~ '^\{.*\}$';

        ALTER TABLE document_revisions
          ALTER COLUMN snapshot TYPE JSONB USING
            CASE
              WHEN snapshot IS NULL OR snapshot = '' THEN '{}'::jsonb
              ELSE snapshot::jsonb
            END;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS docrev_number_idx ON document_revisions (doc_number);

-- ==========================================================================
-- 18. EMAIL_QUEUE — promeni tip sent_at TEXT → TIMESTAMPTZ
-- ==========================================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'email_queue'
          AND column_name = 'sent_at'
          AND data_type = 'text'
    ) THEN
        -- NULL-uj nevalidne stringove
        UPDATE email_queue
          SET sent_at = NULL
          WHERE sent_at IS NOT NULL AND sent_at != '' AND sent_at !~ '^\d{4}-\d{2}-\d{2}';

        ALTER TABLE email_queue
          ALTER COLUMN sent_at TYPE TIMESTAMPTZ USING
            CASE
              WHEN sent_at IS NULL OR sent_at = '' THEN NULL
              ELSE sent_at::timestamptz
            END;
    END IF;
END $$;

-- ==========================================================================
-- 19. AUDIT_LOGS — osiguraj sve kolone koje kod koristi
-- ==========================================================================
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS sync_id TEXT UNIQUE;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS user_id TEXT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS username TEXT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS timestamp TIMESTAMPTZ DEFAULT now();
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS location TEXT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS data JSONB DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS audit_ts_idx ON audit_logs (timestamp DESC);
CREATE INDEX IF NOT EXISTS audit_user_idx ON audit_logs (user_id);
CREATE INDEX IF NOT EXISTS audit_module_idx ON audit_logs (module);

-- ==========================================================================
-- 20. SETTINGS — osiguraj strukturu
-- ==========================================================================
ALTER TABLE settings ADD COLUMN IF NOT EXISTS data JSONB DEFAULT '{}'::jsonb;

-- ==========================================================================
-- 21. updated_at triggeri (ako ih nema)
-- ==========================================================================
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.triggers
        WHERE event_object_table = 'partners' AND trigger_name = 'partners_set_updated_at'
    ) THEN
        CREATE TRIGGER partners_set_updated_at
            BEFORE UPDATE ON partners
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.triggers
        WHERE event_object_table = 'offers' AND trigger_name = 'offers_set_updated_at'
    ) THEN
        CREATE TRIGGER offers_set_updated_at
            BEFORE UPDATE ON offers
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.triggers
        WHERE event_object_table = 'deals' AND trigger_name = 'deals_set_updated_at'
    ) THEN
        CREATE TRIGGER deals_set_updated_at
            BEFORE UPDATE ON deals
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.triggers
        WHERE event_object_table = 'products' AND trigger_name = 'products_set_updated_at'
    ) THEN
        CREATE TRIGGER products_set_updated_at
            BEFORE UPDATE ON products
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.triggers
        WHERE event_object_table = 'users' AND trigger_name = 'users_set_updated_at'
    ) THEN
        CREATE TRIGGER users_set_updated_at
            BEFORE UPDATE ON users
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
END $$;

-- ==========================================================================
-- KRAJ — sada baza treba da bude potpuno usaglašena sa kodom
-- ==========================================================================
-- Verifikacija (možeš da pokreneš u SQL Editoru):
--
-- SELECT table_name, column_name, data_type
-- FROM information_schema.columns
-- WHERE table_schema = 'public'
--   AND table_name IN ('partners', 'offers', 'deals', 'users', 'shared_documents')
-- ORDER BY table_name, ordinal_position;
-- ==========================================================================
