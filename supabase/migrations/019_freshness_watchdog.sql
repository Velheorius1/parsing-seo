-- 019: Freshness-SLO watchdog support.
-- Per-source freshness aggregate + covering index, used by
-- crawler.scripts.freshness_watchdog to detect sources that silently stopped
-- producing (fetcher removed / upstream dead) — the failure mode that lost
-- Cooperation.uz Bosma (993 rows) for 40 days, noticed only in a manual audit.
--
-- NOTE: CREATE INDEX CONCURRENTLY must run OUTSIDE a transaction block.
-- (Already applied to prod via Management API on 2026-06-06.)

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tenders_source_collected
  ON tenders(source, collected_at DESC);

-- SET statement_timeout makes the full-table GROUP BY survive PostgREST's short
-- default timeout (the aggregate over 264k rows otherwise hits 57014).
CREATE OR REPLACE FUNCTION source_freshness()
RETURNS TABLE(source text, cnt bigint, last_collected timestamptz)
LANGUAGE sql STABLE
SET statement_timeout = '60s'
AS $$
  SELECT source, count(*), max(collected_at) FROM tenders GROUP BY source
$$;
