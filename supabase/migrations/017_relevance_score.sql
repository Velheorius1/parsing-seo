-- 017: AI relevance score columns on tenders
-- Replaces binary YES/NO filter with structured score 0-100 + category + reason.
-- Score, category and reason are written when notifier._ai_check_relevance
-- evaluates a tender. NULL = not yet scored (e.g. crawler had no API key,
-- AI call failed, tender did not reach the keyword/bypass gate).

ALTER TABLE tenders
  ADD COLUMN IF NOT EXISTS relevance_score smallint,
  ADD COLUMN IF NOT EXISTS relevance_category text,
  ADD COLUMN IF NOT EXISTS relevance_reason text;

-- Partial index — most rows are NULL, no point indexing them.
-- Used by future UI/analytics: "top tenders by AI score".
CREATE INDEX IF NOT EXISTS idx_tenders_relevance_score
  ON tenders (relevance_score DESC NULLS LAST)
  WHERE relevance_score IS NOT NULL;

COMMENT ON COLUMN tenders.relevance_score IS '0-100 AI relevance score (NULL = not scored)';
COMMENT ON COLUMN tenders.relevance_category IS 'client | ad | irrelevant — matches feedback CLI labels';
COMMENT ON COLUMN tenders.relevance_reason IS 'Short AI rationale (<=200 chars)';
