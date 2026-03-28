-- Phase 0: Security fixes — restrict INSERT/UPDATE on tenders, favorites, predictions
-- Pattern from 009_fix_settings_rls.sql (service_role only)
-- Phase 5: GIN indexes on array columns for query performance

-- === TENDERS: restrict INSERT/UPDATE to service_role ===
DROP POLICY IF EXISTS "tenders_insert" ON tenders;
DROP POLICY IF EXISTS "tenders_update" ON tenders;

CREATE POLICY "tenders_insert_service" ON tenders
  FOR INSERT WITH CHECK (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  );
CREATE POLICY "tenders_update_service" ON tenders
  FOR UPDATE USING (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  ) WITH CHECK (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  );

-- === TENDER_FAVORITES: restrict INSERT/UPDATE to service_role ===
DROP POLICY IF EXISTS "tender_favorites_insert" ON tender_favorites;
DROP POLICY IF EXISTS "tender_favorites_update" ON tender_favorites;

CREATE POLICY "tender_favorites_insert_service" ON tender_favorites
  FOR INSERT WITH CHECK (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  );
CREATE POLICY "tender_favorites_update_service" ON tender_favorites
  FOR UPDATE USING (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  ) WITH CHECK (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  );

-- === TENDER_PREDICTIONS: restrict INSERT/UPDATE to service_role ===
DROP POLICY IF EXISTS "tender_predictions_insert" ON tender_predictions;
DROP POLICY IF EXISTS "tender_predictions_update" ON tender_predictions;

CREATE POLICY "tender_predictions_insert_service" ON tender_predictions
  FOR INSERT WITH CHECK (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  );
CREATE POLICY "tender_predictions_update_service" ON tender_predictions
  FOR UPDATE USING (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  ) WITH CHECK (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  );

-- === GIN INDEXES for array queries (Phase 5) ===
CREATE INDEX IF NOT EXISTS idx_tenders_keywords_gin ON tenders USING GIN (matched_keywords);
CREATE INDEX IF NOT EXISTS idx_tenders_categories_gin ON tenders USING GIN (categories);
CREATE INDEX IF NOT EXISTS idx_tenders_region ON tenders (region);

-- === CRAWL_RUNS table for observability (Phase 1) ===
CREATE TABLE IF NOT EXISTS crawl_runs (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  duration_seconds NUMERIC,
  total_fetched INT DEFAULT 0,
  total_new INT DEFAULT 0,
  total_upserted INT DEFAULT 0,
  total_enriched INT DEFAULT 0,
  alerts_sent INT DEFAULT 0,
  errors_count INT DEFAULT 0,
  source_details JSONB DEFAULT '{}',
  ai_calls_count INT DEFAULT 0,
  ai_estimated_cost_usd NUMERIC DEFAULT 0,
  error_sources TEXT[] DEFAULT '{}',
  error_messages JSONB DEFAULT '[]',
  dry_run BOOLEAN DEFAULT false,
  source_filter TEXT[],
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_crawl_runs_started ON crawl_runs(started_at DESC);

ALTER TABLE crawl_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "crawl_runs_select" ON crawl_runs FOR SELECT USING (true);
CREATE POLICY "crawl_runs_insert_service" ON crawl_runs
  FOR INSERT WITH CHECK (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  );
CREATE POLICY "crawl_runs_update_service" ON crawl_runs
  FOR UPDATE USING (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  ) WITH CHECK (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  );
