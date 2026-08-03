-- ==========================================================================
-- ASPIDUS CRM — V25 BACKFILL — popuni nove top-level kolone iz data JSONB
-- ==========================================================================
-- Nakon Faze 1 (koja je dodala prazne top-level kolone), postojeći podaci
-- su i dalje u `data` JSONB koloni. Ovaj SQL prelivaju u top-level kolone
-- da bi DB-level filtriranje (npr. WHERE customer_id = ?) radilo brzo.
--
-- Bezbedno za višestruko pokretanje (COALESCE + WHERE ... IS NULL).
-- ==========================================================================

-- 1. PARTNERS — portal_token, is_portal_active (top-level columns iz data JSONB)
UPDATE partners
  SET portal_token = data->>'portalToken'
  WHERE portal_token IS NULL AND data ? 'portalToken';

UPDATE partners
  SET is_portal_active = (data->>'isPortalActive')::boolean
  WHERE is_portal_active IS NULL AND data ? 'isPortalActive';

UPDATE partners
  SET is_premium = (data->>'isPremium')::boolean
  WHERE is_premium IS NULL AND data ? 'isPremium';

UPDATE partners
  SET kyc_approved = (data->>'kycApproved')::boolean
  WHERE kyc_approved IS NULL AND data ? 'kycApproved';

UPDATE partners
  SET can_login = (data->>'canLogin')::boolean
  WHERE can_login IS NULL AND data ? 'canLogin';

-- portal_level (1-4) — proveri da li JSONB ima portalLevel, konvertuj u smallint
UPDATE partners
  SET portal_level = COALESCE(
    NULLIF(data->>'portalLevel', '')::smallint,
    CASE
      WHEN (data->>'isPremium')::boolean IS TRUE THEN 4
      WHEN (data->>'kycApproved')::boolean IS TRUE THEN 3
      ELSE 1
    END
  )
  WHERE portal_level IS NULL;

-- 2. OFFERS — customer_id, offer_no iz data JSONB
UPDATE offers
  SET customer_id = data->>'customerId'
  WHERE customer_id IS NULL AND data ? 'customerId';

UPDATE offers
  SET offer_no = data->>'offerNo'
  WHERE offer_no IS NULL AND data ? 'offerNo';

-- 3. DEALS — buyer_id, source_offer_id iz data JSONB
UPDATE deals
  SET buyer_id = COALESCE(data->>'buyerId', data->>'customerId')
  WHERE buyer_id IS NULL AND (data ? 'buyerId' OR data ? 'customerId');

UPDATE deals
  SET source_offer_id = data->>'sourceOfferId'
  WHERE source_offer_id IS NULL AND data ? 'sourceOfferId';

-- 4. DEMANDS — buyer_id iz data JSONB
UPDATE demands
  SET buyer_id = COALESCE(data->>'buyerId', data->>'customerId')
  WHERE buyer_id IS NULL AND (data ? 'buyerId' OR data ? 'customerId');

-- 5. SHARED_DOCUMENTS — partner_id, title, category, storage_bucket, storage_path
UPDATE shared_documents
  SET partner_id = data->>'partnerId'
  WHERE partner_id IS NULL AND data ? 'partnerId';

UPDATE shared_documents
  SET title = data->>'title'
  WHERE title IS NULL AND data ? 'title';

UPDATE shared_documents
  SET category = data->>'category'
  WHERE category IS NULL AND data ? 'category';

UPDATE shared_documents
  SET storage_bucket = data->>'storageBucket'
  WHERE storage_bucket IS NULL AND data ? 'storageBucket';

UPDATE shared_documents
  SET storage_path = data->>'storagePath'
  WHERE storage_path IS NULL AND data ? 'storagePath';

-- 6. KYC_SUBMISSIONS — status (default 'pending' ako nema)
-- Već ima DEFAULT 'pending' iz Faze 1, ali osiguraj za NULL vrednosti
UPDATE kyc_submissions
  SET status = 'pending'
  WHERE status IS NULL;

-- 7. PORTAL_PRODUCTS — status iz data JSONB (ako postoji)
UPDATE portal_products
  SET status = COALESCE(NULLIF(data->>'status', ''), 'pending')
  WHERE status IS NULL OR status = '';

-- ==========================================================================
-- Verifikacija
-- ==========================================================================
-- SELECT 'partners with portal_token' AS label, COUNT(*) FROM partners WHERE portal_token IS NOT NULL
-- UNION ALL SELECT 'offers with customer_id', COUNT(*) FROM offers WHERE customer_id IS NOT NULL
-- UNION ALL SELECT 'deals with buyer_id', COUNT(*) FROM deals WHERE buyer_id IS NOT NULL
-- UNION ALL SELECT 'demands with buyer_id', COUNT(*) FROM demands WHERE buyer_id IS NOT NULL
-- UNION ALL SELECT 'shared_docs with partner_id', COUNT(*) FROM shared_documents WHERE partner_id IS NOT NULL;
-- ==========================================================================
