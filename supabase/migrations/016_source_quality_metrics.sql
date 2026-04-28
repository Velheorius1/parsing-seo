-- 016: source_quality_metrics + source_quality_daily rollup
-- Per-source quality tracking with long-format schema. Designed by Markus Winand
-- (2026-04-28 deep-think). Indexes for: trend per source, top-N worst, zero-fetch SLA.
-- Retention: raw 90d, daily rollup forever.

CREATE TABLE IF NOT EXISTS source_quality_metrics (
  id BIGSERIAL PRIMARY KEY,
  source TEXT NOT NULL,
  metric_type TEXT NOT NULL,
  -- Known metric_types:
  --   'org_pct', 'price_pct', 'deadline_pct', 'region_pct', 'source_url_pct',
  --   'quality_score' (weighted 0-100),
  --   'items_fetched', 'items_new', 'items_kept_after_filter',
  --   'duplicate_ratio' (0-1), 'avg_age_hours',
  --   'errors_count', 'alert_client_rate', 'alert_ad_rate'
  metric_value NUMERIC NOT NULL,
  sample_size INT,                     -- N tenders the metric was computed over
  run_id UUID,                         -- optional FK link to crawl_runs.id (when avail)
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  metadata JSONB DEFAULT '{}'::jsonb
);

-- Q1: trend per source ("how did lex.uz quality move over 30 days?")
CREATE INDEX IF NOT EXISTS idx_sqm_source_time
  ON source_quality_metrics (source, metric_type, computed_at DESC);

-- Q2: top-N worst by metric today
CREATE INDEX IF NOT EXISTS idx_sqm_metric_time
  ON source_quality_metrics (metric_type, computed_at DESC)
  INCLUDE (source, metric_value);

-- Q3: zero-fetch SLA breach (partial index, very compact)
CREATE INDEX IF NOT EXISTS idx_sqm_zero_fetch
  ON source_quality_metrics (computed_at DESC, source)
  WHERE metric_type = 'items_fetched' AND metric_value = 0;

-- RLS: read public, write only service_role
ALTER TABLE source_quality_metrics ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "sqm_select" ON source_quality_metrics;
CREATE POLICY "sqm_select" ON source_quality_metrics FOR SELECT USING (true);

DROP POLICY IF EXISTS "sqm_insert_service" ON source_quality_metrics;
CREATE POLICY "sqm_insert_service" ON source_quality_metrics FOR INSERT WITH CHECK (
  (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
);

DROP POLICY IF EXISTS "sqm_delete_service" ON source_quality_metrics;
CREATE POLICY "sqm_delete_service" ON source_quality_metrics FOR DELETE USING (
  (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
);

-- Daily rollup table (filled by quality_rollup.py cron, retained forever)
CREATE TABLE IF NOT EXISTS source_quality_daily (
  source TEXT NOT NULL,
  day DATE NOT NULL,
  metric_type TEXT NOT NULL,
  avg_value NUMERIC,
  min_value NUMERIC,
  max_value NUMERIC,
  sample_size INT,
  PRIMARY KEY (source, day, metric_type)
);

CREATE INDEX IF NOT EXISTS idx_sqd_metric_day
  ON source_quality_daily (metric_type, day DESC);

ALTER TABLE source_quality_daily ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "sqd_select" ON source_quality_daily;
CREATE POLICY "sqd_select" ON source_quality_daily FOR SELECT USING (true);

DROP POLICY IF EXISTS "sqd_write_service" ON source_quality_daily;
CREATE POLICY "sqd_write_service" ON source_quality_daily FOR ALL
  USING ((current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role')
  WITH CHECK ((current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role');

-- Convenience view: latest metric per source/type
CREATE OR REPLACE VIEW source_quality_latest AS
  SELECT DISTINCT ON (source, metric_type)
    source, metric_type, metric_value, sample_size, computed_at, metadata
  FROM source_quality_metrics
  ORDER BY source, metric_type, computed_at DESC;
